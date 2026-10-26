"""Descriptive, original-function coverage; never an eligibility decision.

Istanbul providers perform the transform sourcemap remapping. The supplementary
TypeScript AST supplies original UTF-16 function intervals, including nested bodies.
Ambiguous maps are unavailable, never silently interpreted as zero coverage.
"""

import hashlib
import io
import json
import re
import tarfile
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import TypeAdapter

from robo_minerador.environments.plan import Limits
from robo_minerador.execution.docker import (
    STORAGE_POLICY,
    DockerExecutor,
    ExecutionResult,
    InfrastructureInterruption,
    ProjectExecutionFailure,
)
from robo_minerador.execution.results import CaseResult, FrameworkReport, load_report
from robo_minerador.extraction.client import Target
from robo_minerador.extraction.instrumentation import InstrumentationManifest
from robo_minerador.state.artifacts import ArtifactStore
from robo_minerador.state.store import StateStore
from robo_minerador.validation.cases import CaseValidation, validate_cases


class CoverageUnattributable(ValueError):
    """The provider map cannot identify exactly one original target body."""


class CoverageUnavailable(ValueError):
    """An observational coverage prerequisite is unavailable."""


def percentage(covered: int, total: int) -> float | None:
    if not 0 <= covered <= total:
        raise ValueError("invalid coverage counters")
    return 100.0 * covered / total if total else None


@dataclass(frozen=True)
class CoverageCounter:
    covered: int
    total: int

    @property
    def percentage(self) -> float | None:
        return percentage(self.covered, self.total)


def _point(location: dict[str, int]) -> tuple[int, int]:
    return location["line"], location["column"]


def summarize_istanbul(
    source: bytes, target: Target, data: dict[str, Any]
) -> tuple[CoverageCounter, CoverageCounter]:
    """Count statement-start lines and branch arms belonging to the original body."""
    encoded = source.decode().encode("utf-16-le")
    if (
        data.get("source_sha256") != hashlib.sha256(source).hexdigest()
        or data.get("source_maps") != "istanbul-provider-remapped-original"
        or encoded[target.start_offset * 2 : target.end_offset * 2].decode("utf-16-le")
        != target.code
    ):
        raise CoverageUnattributable("original source/map mismatch")
    functions = [
        f
        for f in data["functions"]
        if (f["start"], f["end"]) == (target.start_offset, target.end_offset)
    ]
    if len(functions) != 1:
        raise CoverageUnattributable("ambiguous original AST interval")
    function = functions[0]
    coverage = data["map"]
    source_lines = source.decode().splitlines()

    def end_bound(point: dict[str, Any]) -> tuple[int, int]:
        column = point["column"]
        if column is None:
            # Istanbul serializes Vite's +Infinity mapped column as JSON null.
            # Bound it by actual original line length; do not synthesize a span.
            column = len(source_lines[point["line"] - 1].encode("utf-16-le")) // 2
        return point["line"], column

    def matches(mapped: dict[str, Any]) -> bool:
        if mapped["loc"] == function["body"]:
            return True
        if (
            mapped["loc"]["start"] == function["body"]["start"]
            and mapped["loc"]["end"]["column"] is None
            and mapped["loc"]["end"]["line"] == function["body"]["end"]["line"]
        ):
            # Exact mapped brace uniquely identifies this original AST body.
            return (
                sum(1 for f in data["functions"] if f["body"]["start"] == mapped["loc"]["start"])
                == 1
            )
        # ts-jest maps the generated opening brace to the original return-type
        # annotation. Require the exact original name AND exact body end; the
        # provider start must still precede the original brace on that line.
        declaration = function.get("declaration")
        return bool(
            declaration
            and mapped.get("decl") == declaration
            and mapped["loc"]["end"] == function["body"]["end"]
            and _point(declaration["end"])
            <= _point(mapped["loc"]["start"])
            <= _point(function["body"]["start"])
        )

    mapped = [f for f in coverage["fnMap"].values() if matches(f)]
    if len(mapped) != 1:
        raise CoverageUnattributable("ambiguous provider function interval")

    def belongs(location: dict[str, Any]) -> bool:
        start = _point(location["start"])
        end = end_bound(location["end"])
        body = function["body"]
        if any(
            _point(nested["start"]) <= start < _point(nested["end"])
            for nested in function["nested"]
        ):
            return False
        if _point(body["start"]) <= start < _point(body["end"]):
            if not start <= end <= _point(body["end"]):
                raise CoverageUnattributable("mapped interval crosses original function body")
            return True
        return False

    lines: dict[int, bool] = {}
    for key, location in coverage["statementMap"].items():
        if belongs(location):
            line = location["start"]["line"]
            lines[line] = lines.get(line, False) or coverage["s"][key] > 0
    branches = []
    for key, branch in coverage["branchMap"].items():
        counts = coverage["b"][key]
        if len(counts) != len(branch["locations"]):
            raise CoverageUnattributable("incomplete branch map")
        if (
            branch.get("type") in {"if", "cond-expr"}
            and len(counts) != 2
            and belongs(branch["loc"])
        ):
            # Some remappers drop an implicit else arm with no source location.
            # Its missing counter cannot become a smaller, inflated denominator.
            raise CoverageUnattributable("sourcemap dropped an original branch arm")
        for location, count in zip(branch["locations"], counts, strict=True):
            # Istanbul uses empty locations for a synthetic implicit else.
            if not all({"line", "column"} <= location.get(p, {}).keys() for p in ("start", "end")):
                location = branch.get("loc", {})
            if not all({"line", "column"} <= location.get(p, {}).keys() for p in ("start", "end")):
                raise CoverageUnattributable("branch has no original interval")
            if belongs(location):
                branches.append(count > 0)
    return CoverageCounter(sum(lines.values()), len(lines)), CoverageCounter(
        sum(branches), len(branches)
    )


def select_cases(
    cases: tuple[CaseResult, ...], selected_ids: Iterable[str]
) -> tuple[tuple[CaseResult, ...], str]:
    """Name filtering is allowed only when it proves identity isolation beforehand."""
    ids = set(selected_ids)
    selected = tuple(c for c in cases if c.identity in ids)
    if not ids or {c.identity for c in selected} != ids or not all(c.eligible for c in selected):
        raise CoverageUnavailable("coverage_associated_cases_unavailable")
    names = {" ".join(c.title_path) for c in selected}
    if any(" ".join(c.title_path) in names and c.identity not in ids for c in cases):
        raise CoverageUnavailable("coverage_identity_isolation_unavailable")
    # Escape the JS RegExp metacharacters, without Python-only escaping conventions.
    escaped = [re.sub(r"([\\^$.*+?()[\]{}|])", r"\\\1", name) for name in sorted(names)]
    return selected, "^(?:" + "|".join(escaped) + ")$"


def replay_association(
    validation: CaseValidation, artifacts: ArtifactStore
) -> tuple[CaseValidation, FrameworkReport, InstrumentationManifest]:
    """Recompute T11, including uncertainty omitted by historical v1 ledgers."""
    ledger = json.loads(artifacts.get(validation.evidence_id))
    required = {
        "original_normalized",
        "original_raw",
        "traced_normalized",
        "traced_raw",
        "original_execution",
        "traced_execution",
        "manifest",
        "trace",
        "ordinal",
        "status",
        "case_ids",
        "target_id",
        "reason",
    }
    if not required <= ledger.keys():
        raise ValueError("missing association provenance")
    recorded = TypeAdapter(CaseValidation).validate_json(
        json.dumps(
            {
                "status": ledger["status"],
                "case_ids": ledger["case_ids"],
                "target_id": ledger["target_id"],
                "reason": ledger["reason"],
                "evidence_id": validation.evidence_id,
                "unresolved_case_ids": ledger.get("unresolved_case_ids", []),
                "uncertainty_reasons": ledger.get("uncertainty_reasons", []),
            }
        )
    )
    version = ledger.get("association_rule_version")
    if recorded != validation or version not in {None, "T11-v2"}:
        raise ValueError("association provenance mismatch")

    def report(prefix: str) -> FrameworkReport:
        return load_report(
            artifacts.get(ledger[prefix + "_normalized"]),
            artifacts.get(ledger[prefix + "_raw"]),
            artifacts,
        )

    def execution(prefix: str) -> ExecutionResult:
        digest = ledger[prefix + "_execution"]
        value = json.loads(artifacts.get(digest))
        value["evidence_artifact"] = digest
        return TypeAdapter(ExecutionResult).validate_json(json.dumps(value))

    original, traced = report("original"), report("traced")
    manifest = InstrumentationManifest.model_validate_json(artifacts.get(ledger["manifest"]))
    replayed = validate_cases(
        original,
        traced,
        manifest,
        validation.target_id,
        execution("original"),
        execution("traced"),
        artifacts.get(ledger["trace"]),
        artifacts,
        pair_ordinal=ledger["ordinal"],
    )
    if version is not None and replayed != validation:
        raise ValueError("association provenance mismatch")
    return replayed, original, manifest


def verify_association(
    validation: CaseValidation, artifacts: ArtifactStore
) -> tuple[FrameworkReport, InstrumentationManifest]:
    """T15 may select proven IDs even when other cases remain unresolved.

    Legacy case sets must still be reproduced exactly. T14 consumes
    replay_association's current completeness rather than assuming the old zero.
    """
    replayed, original, manifest = replay_association(validation, artifacts)
    if replayed.status != "evaluated" or replayed.case_ids != validation.case_ids:
        raise ValueError("association provenance mismatch")
    return original, manifest


@dataclass(frozen=True)
class CoverageMeasurement:
    target_id: str
    availability: Literal["available", "unavailable"]
    reason: str
    lines: CoverageCounter | None
    branches: CoverageCounter | None
    selected_ids: tuple[str, ...]
    method: str
    evidence_id: str


def persist_measurement(state: StateStore, task_id: str, result: CoverageMeasurement) -> None:
    """Publish two stable measurement rows in one existing state transaction."""
    state.register_artifact(result.evidence_id)
    with state.transaction():
        for name, counter in (("lines", result.lines), ("branches", result.branches)):
            availability = result.availability
            reason = result.reason
            if availability == "available" and counter is not None and counter.total == 0:
                status: Literal["available", "unavailable", "not_applicable"] = "not_applicable"
                reason = "no_observable_" + name
            else:
                status = availability
            state.record_measurement(
                task_id,
                name="coverage_" + name,
                value=counter.percentage if counter is not None and status == "available" else None,
                availability=status,
                method=result.method,
                reason_code=reason,
                evidence_id=result.evidence_id,
            )


class CoverageService:
    """T15 boundary for T14: execute a verified association, persist descriptive data.

    prepared_id must be the original, installed snapshot, not the T11 overlay.
    read_report is the existing analyzer normalization adapter (no host project run).
    Original suites and eligibility are intentionally owned by T13/T14.
    """

    def __init__(
        self,
        runner: DockerExecutor,
        state: StateStore,
        artifacts: ArtifactStore,
        *,
        read_report: Callable[[ExecutionResult], FrameworkReport],
    ) -> None:
        self.runner, self.state, self.artifacts = runner, state, artifacts
        self.read_report = read_report

    def run_observation(
        self,
        prepared_id: str,
        argv: tuple[str, ...],
        limits: Limits,
        *,
        config_path: str,
        framework: str,
        version: str,
        pattern: str,
    ) -> ExecutionResult:
        """Execute the original config through a CAS-recorded observational wrapper."""
        root = Path(__file__).parent / "supplements"
        tool = (
            {"name": "@vitest/coverage-istanbul", "version": version}
            if framework == "vitest"
            else {
                "name": "babel-plugin-istanbul",
                "version": {"29.7.0": "6.1.1", "30.0.0": "7.0.1"}[version],
            }
        )
        # Canonical reporters are packaged beside the launcher; editable
        # checkouts use the analyzer sources. Neither is installed in the project.
        supplements = {
            "coverage-launch.cjs": (root / "coverage-launch.cjs").read_bytes(),
            "coverage-request.cjs": (
                "module.exports = "
                + json.dumps(
                    {
                        "framework": framework,
                        "version": version,
                        "pattern": pattern,
                        "config_path": config_path,
                        "argv": argv,
                        "instrumentation": {
                            "tool": tool,
                            "compiler_version": "5.8.3",
                            "source_maps": "istanbul-provider-remapped-original",
                        },
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + ";\n"
            ).encode(),
        }
        reporter = framework + (".cjs" if framework == "jest" else ".mjs")
        supplements[reporter] = self.reporter_source(framework)
        return self.runner.run_fresh(
            prepared_id,
            ("node", "/opt/robo-reporters/coverage-launch.cjs"),
            limits,
            supplements=supplements,
        )

    @staticmethod
    def reporter_source(framework: str) -> bytes:
        root = Path(__file__).resolve().parents[3]
        name = framework + (".cjs" if framework == "jest" else ".mjs")
        packaged = Path(__file__).parent / "supplements" / name
        if packaged.is_file():
            return packaged.read_bytes()
        return (root / "tools/ts-analyzer/src/frameworks/reporters" / name).read_bytes()

    def _files(self, execution: ExecutionResult, names: set[str]) -> dict[str, bytes]:
        """Verify observational archives even when only coverage caused a nonzero exit."""
        expected = json.loads(json.dumps(asdict(execution)))
        expected["evidence_artifact"] = ""
        if (
            json.loads(self.artifacts.get(execution.evidence_artifact)) != expected
            or execution.storage_policy != STORAGE_POLICY
            or not execution.storage_verified
            or not execution.workspace_artifact
            or execution.network_mode != "none"
        ):
            raise ValueError("coverage execution provenance mismatch")
        with tarfile.open(
            fileobj=io.BytesIO(self.artifacts.get(execution.workspace_artifact))
        ) as tar:
            result = {}
            for name in names:
                entry = tar.getmember("workspace/" + name)
                if not entry.isfile():
                    raise CoverageUnattributable("non-regular observational evidence")
                stream = tar.extractfile(entry)
                if stream is None:
                    raise CoverageUnattributable("missing observational evidence")
                result[name] = stream.read()
            return result

    def measure(
        self,
        task_id: str,
        validation: CaseValidation,
        target: Target,
        prepared_id: str,
        argv: tuple[str, ...],
        limits: Limits,
        *,
        config_path: str,
        source: bytes,
    ) -> CoverageMeasurement:
        original, manifest = verify_association(validation, self.artifacts)
        if target not in manifest.targets or validation.target_id != target.id:
            raise ValueError("association target mismatch")
        selected_ids = tuple(sorted(set(validation.case_ids)))
        ledger: dict[str, Any] = {
            "schema_version": "1.0",
            "purpose": "associated_target_coverage",
            "target": target.model_dump(),
            "association": validation.evidence_id,
            "selected_ids": selected_ids,
            "executed_ids": [],
            "configuration": json.loads(self.artifacts.get(original.evidence_id))["configuration"],
            "method": "istanbul-original-function-nested-excluded",
        }
        lines = branches = None
        availability: Literal["available", "unavailable"] = "unavailable"
        reason = "coverage_unattributable"
        try:
            _, pattern = select_cases(original.cases, selected_ids)
            # The reference snapshot and all test bodies must be original bytes.
            with tarfile.open(fileobj=io.BytesIO(self.runner.prepared_archive(prepared_id))) as tar:
                reference = {}
                for name in {f.path for f in manifest.files} | {target.path, config_path}:
                    stream = tar.extractfile("workspace/" + name)
                    if stream is None:
                        raise ValueError("missing original prepared source")
                    reference[name] = stream.read()
            if reference[target.path] != source or any(
                hashlib.sha256(reference[f.path]).hexdigest() != f.original_sha256
                for f in manifest.files
            ):
                raise ValueError("coverage requires original prepared snapshot")
            association = json.loads(self.artifacts.get(validation.evidence_id))
            baseline_id = association["original_execution"]
            baseline_record = json.loads(self.artifacts.get(baseline_id))
            baseline_record["evidence_artifact"] = baseline_id
            baseline = TypeAdapter(ExecutionResult).validate_json(json.dumps(baseline_record))
            if self._files(baseline, set(reference)) != reference:
                raise CoverageUnattributable(
                    "prepared sources/config differ from original execution"
                )
            ledger["reference"] = {
                name: self.artifacts.put(body) for name, body in reference.items()
            }
            ledger["selection_pattern"] = pattern
            try:
                execution = self.run_observation(
                    prepared_id,
                    argv,
                    limits,
                    config_path=config_path,
                    framework=original.framework,
                    version=original.framework_version,
                    pattern=pattern,
                )
            except ProjectExecutionFailure as error:
                execution = error.result
            ledger["execution"] = execution.evidence_artifact
            ledger["supplements"] = dict(execution.supplements)
            ledger["stdout"] = execution.stdout_artifact
            ledger["stderr"] = execution.stderr_artifact
            failed = execution.exit_code != 0 or execution.reason != "passed"
            if failed:
                ledger["failure"] = execution.reason
            if execution.timed_out or execution.oom_killed or not execution.workspace_artifact:
                raise CoverageUnavailable("coverage_execution_failed")
            files = self._files(execution, {".robo-reports/coverage-envelope.json"})
            envelope = json.loads(files[".robo-reports/coverage-envelope.json"])
            ledger["coverage"] = self.artifacts.put(files[".robo-reports/coverage-envelope.json"])
            if envelope["availability"] != "available":
                raise CoverageUnavailable(envelope["reason"])
            report = self.read_report(execution)
            ledger["report"] = [report.evidence_id, report.normalized_evidence_id]
            ledger["configuration"] = json.loads(self.artifacts.get(report.evidence_id))[
                "configuration"
            ]
            if (
                load_report(
                    self.artifacts.get(report.normalized_evidence_id),
                    self.artifacts.get(report.evidence_id),
                    self.artifacts,
                )
                != report
                or report.support.status != "tested"
                or (
                    report.framework,
                    report.framework_version,
                    report.compiler_version,
                    report.transform,
                )
                != (
                    original.framework,
                    original.framework_version,
                    original.compiler_version,
                    original.transform,
                )
            ):
                raise CoverageUnattributable("coverage framework/transform mismatch")
            actual = tuple(
                c
                for c in report.cases
                if c.status not in {"skipped", "pending", "todo", "not_executed"}
            )
            ledger["executed_ids"] = [c.identity for c in actual]
            if (
                {c.identity for c in actual} != set(selected_ids)
                or len(actual) != len(selected_ids)
                or not all(c.eligible for c in actual)
            ):
                raise CoverageUnavailable("coverage_identity_isolation_unavailable")
            names = set(reference) | {".robo-reports/raw.json"}
            observed = self._files(execution, names)
            if any(observed[name] != body for name, body in reference.items()):
                raise CoverageUnattributable("original source/config modified")
            if observed[".robo-reports/raw.json"] != self.artifacts.get(report.evidence_id):
                raise ValueError("coverage report provenance mismatch")
            ledger["tool"] = envelope["tool"]
            lines, branches = summarize_istanbul(
                source, target, envelope["files"].get(target.path, {})
            )
            if failed:
                raise CoverageUnavailable("coverage_execution_failed")
            availability, reason = "available", "measured"
        except InfrastructureInterruption as error:
            ledger["interruption"] = error.evidence_artifact
            reason = "coverage_execution_interrupted"
        except CoverageUnavailable as error:
            reason = str(error)
        except (CoverageUnattributable, KeyError, tarfile.TarError) as error:
            ledger["detail"] = str(error)
            reason = "coverage_unattributable"
        ledger.update(
            availability=availability,
            reason=reason,
            lines={**asdict(lines), "percentage": lines.percentage} if lines else None,
            branches={**asdict(branches), "percentage": branches.percentage} if branches else None,
        )
        evidence_id = self.artifacts.put(
            json.dumps(ledger, sort_keys=True, ensure_ascii=False).encode()
        )
        result = CoverageMeasurement(
            target.id,
            availability,
            reason,
            lines,
            branches,
            selected_ids,
            str(ledger["method"]),
            evidence_id,
        )
        persist_measurement(self.state, task_id, result)
        return result
