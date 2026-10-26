"""Evidence-derived instance eligibility. Descriptive metrics never enter the gates."""

import json
import posixpath
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Literal

from pydantic import TypeAdapter

from robo_minerador.domain import Assessment, CriterionStatus
from robo_minerador.environments.plan import EnvironmentPlan
from robo_minerador.execution.docker import ExecutionResult, PreparedEnvironment, _canonical_archive
from robo_minerador.execution.repetitions import (
    ValidationResult,
    prove_suite_scope,
    validation_identity,
)
from robo_minerador.execution.results import FrameworkReport, load_report
from robo_minerador.extraction.context import (
    FrozenModel,
    _prepared_files,
    load_context,
    load_prepared_context,
)
from robo_minerador.extraction.instrumentation import InstrumentationResponse
from robo_minerador.extraction.service import Candidate, ExtractionManifest, load_extraction
from robo_minerador.identity import stable_digest
from robo_minerador.screening.commands import CommandPlan
from robo_minerador.screening.service import load_report as load_screening
from robo_minerador.screening.snapshots import load_snapshot
from robo_minerador.state.artifacts import ArtifactStore
from robo_minerador.state.store import StateStore, TaskOutcome
from robo_minerador.validation.cases import (
    CaseValidation,
    _execution_files,
)
from robo_minerador.validation.coverage import (
    CoverageMeasurement,
    CoverageService,
    replay_association,
)

MANDATORY_CRITERIA = tuple(f"H-{group}{n:02}" for group in ("R", "F") for n in range(1, 7))
RULE_VERSION = "T14-v2"
INSTANCE_STAGE = "instance_validation:" + RULE_VERSION


def all_mandatory_approved(decisions: Mapping[str, str]) -> bool:
    return all(decisions.get(criterion) == "approved" for criterion in MANDATORY_CRITERIA)


def case_minimum_status(
    associations: tuple[CaseValidation, ...], *, minimum: int
) -> CriterionStatus:
    """A lower bound can prove the minimum; only a complete deficit can reject it."""
    if len(associations) != 3:
        return "not_evaluated"
    if all(a.status == "evaluated" and len(a.case_ids) >= minimum for a in associations):
        return "approved"
    if any(
        a.status == "evaluated" and len(a.case_ids) < minimum and not a.unresolved_case_ids
        for a in associations
    ):
        return "rejected"
    return "not_evaluated"


class DependencyReview(FrozenModel):
    status: CriterionStatus
    reason: str
    method: Literal["closed-local-arithmetic-v2"] = "closed-local-arithmetic-v2"
    local_paths: tuple[str, ...]
    configuration_paths: tuple[str, ...]
    observed_attempts: tuple[str, ...]
    limitations: tuple[str, ...]


# A deliberately closed lexical subset, not a general-purpose TS dependency analyzer.
# Unknown identifiers, computed access, dynamic imports, runtime packages and custom
# configuration stay inconclusive. This can establish a local arithmetic module;
# absence of a network error, by itself, cannot establish external independence.
_TOKEN = re.compile(r"/\*[\s\S]*?\*/|//[^\r\n]*|'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"")
_IMPORT = re.compile(r"\bimport\s*\{([^{}]+)\}\s*from\s*(['\"])([^'\"]+)\2\s*;?")
_SOURCE_TOKEN = re.compile(_IMPORT.pattern + "|" + _TOKEN.pattern)
_WORDS = set(
    "export function const let return if else number boolean string true false null "
    "undefined void throw Error".split()
)
_TEST_WORDS = set("test expect describe it toBe toEqual skip todo failing fails".split())
_CONFIG_WORDS = set(
    "module exports import defineConfig from export default test testEnvironment "
    "testMatch transform diagnostics false true tsconfig target testLocationInResults "
    "include includeTaskLocation pool maxWorkers fileParallelism reporters".split()
)
_CONFIG_LITERALS = {
    "vitest/config",
    "node",
    "ts-jest",
    "ES2022",
    "CommonJS",
    "forks",
    "./reporter.cjs",
    "./reporter.mjs",
}


def review_dependencies(
    files: Mapping[str, bytes], observed_attempts: tuple[str, ...]
) -> DependencyReview:
    """Review original config and the complete preserved local TS module set.

    Attempts are observations requiring analysis, never factual proof that a real
    service is necessary. Unsupported projects receive no affirmative decision.
    """
    paths = tuple(sorted(p for p in files if p.endswith(".ts") and not p.endswith(".config.ts")))
    configurations = tuple(
        sorted(p for p in files if p.endswith(".json") or "config" in p or p.endswith(".yaml"))
    )
    unresolved = []
    if observed_attempts:
        unresolved.append("observed_network_attempt_requires_analysis")
    if "package.json" not in files or not paths:
        unresolved.append("missing_dependency_configuration")
    for path, body in files.items():
        parsed = {}
        if path.endswith(".json"):
            if path.rsplit("/", 1)[-1] not in {
                "package.json",
                "package-lock.json",
                "tsconfig.json",
            }:
                unresolved.append("custom_json_configuration_requires_analysis:" + path)
                continue
            try:
                parsed = json.loads(body)
            except (ValueError, UnicodeError):
                unresolved.append("unreadable_configuration:" + path)
                continue
            if not isinstance(parsed, dict):
                unresolved.append("unsupported_configuration_shape:" + path)
                continue
        if path.endswith("package.json"):
            package = parsed
            if package.get("dependencies") or package.get("optionalDependencies"):
                unresolved.append("runtime_package_dependencies_require_analysis")
            if package.get("config") or package.get("jest") or package.get("vitest"):
                unresolved.append("custom_package_configuration_requires_analysis")
            if set(package.get("scripts", {})) - {"test"}:
                unresolved.append("custom_lifecycle_requires_analysis")
            if package.get("scripts", {}).get("test", "") not in {
                "jest",
                "jest --runInBand",
                "vitest run",
            }:
                unresolved.append("unsupported_runner_configuration_selection:" + path)
        if path.endswith("tsconfig.json"):
            config = parsed
            if config.get("extends") or config.get("compilerOptions", {}).get("plugins"):
                unresolved.append("custom_compiler_configuration_requires_analysis")
        if path.endswith((".yaml", ".yml")) or path.rsplit("/", 1)[-1].startswith(".env"):
            unresolved.append("external_service_configuration_requires_analysis")
        if path.endswith((".js", ".mjs", ".cjs")) or path.endswith(".config.ts"):
            if any(
                body == CoverageService.reporter_source(framework)
                for framework in ("jest", "vitest")
            ):
                continue  # Exact miner reporter bytes, not an arbitrary project plugin.
            if path not in {"jest.config.cjs", "vitest.config.ts"}:
                unresolved.append("unsupported_executable_configuration:" + path)
                continue
            source = body.decode()
            tokens = _TOKEN.findall(source)
            literals = [token[1:-1] for token in tokens if token.startswith(("'", '"'))]
            if set(re.findall(r"[A-Za-z_$][\w$]*", _TOKEN.sub(" ", source))) - _CONFIG_WORDS or any(
                value not in _CONFIG_LITERALS and not value.startswith(("*", "^"))
                for value in literals
            ):
                unresolved.append("unsupported_executable_configuration:" + path)
    for path in paths:
        source = files[path].decode("utf-8-sig")
        names = set(_WORDS)
        if ".test." in path or ".spec." in path:
            names.update(_TEST_WORDS)

        def source_token(match: re.Match[str], *, path: str = path, names: set[str] = names) -> str:
            if match[1] is None:
                return " "  # Comments and literals cannot introduce bindings.
            module = match[3]
            exports = {
                "test",
                "it",
                "describe",
                "expect",
                "beforeEach",
                "afterEach",
                "beforeAll",
                "afterAll",
                "vi",
            }
            if module not in {"vitest"}:
                exports = set()
                base = posixpath.normpath(posixpath.join(posixpath.dirname(path), module))
                suffix = PurePosixPath(module).suffix
                candidates = (
                    (base,)
                    if suffix
                    else tuple(
                        root + extension
                        for root in (base, base + "/index")
                        for extension in (
                            "",
                            ".ts",
                            ".tsx",
                            ".js",
                            ".jsx",
                            ".json",
                            ".mts",
                            ".cts",
                            ".mjs",
                            ".cjs",
                        )
                    )
                )
                resolved = [candidate for candidate in candidates if candidate in files]
                if (
                    not module.startswith(".")
                    or suffix not in {"", ".ts"}
                    or len(resolved) != 1
                    or resolved[0] not in paths
                ):
                    unresolved.append("unreviewed_or_external_import:" + path)
                else:
                    module_source = _TOKEN.sub(" ", files[resolved[0]].decode("utf-8-sig"))
                    exports.update(
                        re.findall(
                            r"\bexport\s+(?:const|let|function)\s+([A-Za-z_$][\w$]*)", module_source
                        )
                    )
            bindings = _TOKEN.sub(" ", match[1])
            for item in bindings.split(","):
                if not item.strip():
                    continue
                binding = re.fullmatch(
                    r"\s*([A-Za-z_$][\w$]*)(?:\s+as\s+([A-Za-z_$][\w$]*))?\s*", item
                )
                if binding is None or binding[1] not in exports:
                    unresolved.append("unsupported_import_binding:" + path)
                else:
                    names.add(binding[2] or binding[1])
            return " "

        source = _SOURCE_TOKEN.sub(source_token, source)
        names.update(re.findall(r"\b(?:const|let|function)\s+([A-Za-z_$][\w$]*)", source))
        names.update(re.findall(r"\b([A-Za-z_$][\w$]*)\s*:\s*(?:number|boolean|string)\b", source))
        words = set(re.findall(r"[A-Za-z_$][\w$]*", source))
        # Slash expressions need a real TS lexer to distinguish division from
        # regex literals. Neither may manufacture imports in this closed subset.
        if words - names or any(char in source for char in ("\\", "`", "[", "]", "@", "/")):
            unresolved.append("unsupported_local_source_dependency_analysis:" + path)
    return DependencyReview(
        status="not_evaluated" if unresolved else "approved",
        reason="dependency_analysis_unresolved" if unresolved else "closed_local_sources_reviewed",
        local_paths=paths,
        configuration_paths=configurations,
        observed_attempts=observed_attempts,
        limitations=tuple(dict.fromkeys(unresolved)),
    )


class CriterionDecision(FrozenModel):
    criterion_id: str
    status: CriterionStatus
    reason: str
    evidence_ids: tuple[str, ...]


class EligibleOccurrence(FrozenModel):
    """T16 input: an original candidate with replayable mandatory decisions."""

    kind: Literal["eligible_occurrence"] = "eligible_occurrence"
    schema_version: Literal["1.1"] = "1.1"
    rule_version: Literal["T14-v2"] = "T14-v2"
    request_hash: str
    repository_id: str
    repository_sha: str
    extraction_digest: str
    candidate: Candidate
    context_reference: str
    prepared_context_reference: str | None
    prepared_archive: str | None
    prepared_evidence: str | None
    repository_evidence: str
    original_executions: tuple[str, ...]
    association_evidence: tuple[str, ...]
    case_ids_per_run: tuple[tuple[str, ...], ...]
    unresolved_case_ids_per_run: tuple[tuple[str, ...], ...]
    dependency_review: DependencyReview
    assessments: tuple[CriterionDecision, ...]
    accepted: bool
    coverage_evidence: str | None
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class EligibilityResult:
    task_id: str
    digest: str
    occurrence: EligibleOccurrence


class EligibilityInputs(FrozenModel):
    extraction_digest: str
    candidate_id: str
    repository: ValidationResult
    associations: tuple[CaseValidation, ...]
    coverage: CoverageMeasurement | None = None


class EligibilityService:
    def __init__(self, state: StateStore, artifacts: ArtifactStore) -> None:
        self.state, self.artifacts = state, artifacts

    def _repository(
        self, run_id: str, extraction: ExtractionManifest, result: ValidationResult
    ) -> tuple[list[ExecutionResult], list[FrameworkReport], PreparedEnvironment | None]:
        task = self.state.get_task(result.task_id)
        if (
            task.run_id != run_id
            or task.entity_id != extraction.repository_id
            or task.stage != "repository_validation"
            or task.outcome != result.outcome
            or result.evidence_id not in self.state.task_evidence(task.id)
        ):
            raise ValueError("repository validation differs from recorded run identity")
        terminal = json.loads(self.artifacts.get(result.evidence_id))
        if terminal["kind"] != "validation" or terminal["outcome"] != result.outcome:
            raise ValueError("repository validation differs from sealed outcome")
        plan = TypeAdapter(EnvironmentPlan).validate_json(
            self.artifacts.get(extraction.plan_artifact)
        )
        screening = load_screening(self.artifacts, extraction.screening_artifact)
        commands = TypeAdapter(CommandPlan).validate_json(json.dumps(screening["commands"]))
        history = []
        for event in self.state.task_checkpoints(task.id):
            digest = str(event.payload["digest"])
            value = json.loads(self.artifacts.get(digest))
            for ref in value.get("artifacts", []):
                self.artifacts.get(ref)
            history.append((event.attempt, digest, value))
        plan_records = [m for _, _, m in history if m["kind"] == "plan"]
        if not plan_records:
            raise ValueError("repository validation lacks frozen input evidence")
        plan_ref, commands_ref, archive_ref = plan_records[-1]["artifacts"]
        if (
            json.loads(self.artifacts.get(plan_ref))
            != json.loads(self.artifacts.get(extraction.plan_artifact))
            or json.loads(self.artifacts.get(commands_ref)) != screening["commands"]
        ):
            raise ValueError("repository plan differs from extraction provenance")
        request = validation_identity(plan, commands, self.artifacts.get(archive_ref))
        if any(m["request_hash"] != request for _, _, m in history):
            raise ValueError("repository validation inputs differ from sealed request")
        if result.outcome != "completed":
            return [], [], None
        active = [m for attempt, _, m in history if attempt == task.attempt]
        runs = [m for m in active if m["kind"] == "result" and m["phase"] == "run"]
        scopes = [m for m in active if m["kind"] == "scope"]
        if (
            [m["ordinal"] for m in runs] != [1, 2, 3]
            or [m["ordinal"] for m in scopes] != [1, 2, 3]
            or any(m["outcome"] != "passed" for m in runs)
        ):
            raise ValueError("repository validation is not three uninterrupted original runs")
        prepared = TypeAdapter(PreparedEnvironment).validate_json(json.dumps(terminal["prepared"]))
        expected_prepared = {**asdict(prepared), "evidence_artifact": ""}
        if json.loads(self.artifacts.get(prepared.evidence_artifact)) != json.loads(
            json.dumps(expected_prepared)
        ):
            raise ValueError("prepared environment evidence mismatch")
        if prepared.plan_digest != plan.digest or prepared.parent_image_id != plan.derived_image_id:
            raise ValueError("prepared environment differs from selected image/plan")
        if json.loads(self.artifacts.get(prepared.plan_artifact)) != json.loads(
            self.artifacts.get(extraction.plan_artifact)
        ):
            raise ValueError("prepared plan differs from extraction plan")
        snapshot = load_snapshot(self.artifacts, extraction.snapshot_manifest)
        original_entries = _prepared_files(
            _canonical_archive(self.artifacts.get(prepared.input_archive_artifact)), self.artifacts
        )
        if {(f.path, f.kind, f.mode, f.sha256) for f in original_entries} != {
            (f.path, f.kind, f.mode, f.sha256) for f in snapshot.files
        }:
            raise ValueError("prepared original archive differs from extraction snapshot")
        expected_phases = [
            phase for phase in ("install", "build") if getattr(plan, phase) is not None
        ]
        if (
            [command.phase for command in prepared.commands] != expected_phases
            or not prepared.commands
            or prepared.workspace_artifact != prepared.commands[-1].workspace_artifact
        ):
            raise ValueError("prepared archive lacks final installation/build provenance")
        for command in prepared.commands:
            expected = getattr(plan, command.phase)
            if (
                command.argv != expected.argv
                or PurePosixPath(command.cwd) != PurePosixPath("/workspace", expected.cwd)
                or command.timeout_seconds != expected.timeout_seconds
            ):
                raise ValueError("prepared command differs from original frozen plan")
            # Installation may use the frozen bridge policy; execution's ledger,
            # captured full archive and original command still have to agree.
            recorded = json.loads(self.artifacts.get(command.evidence_artifact))
            if (
                recorded != json.loads(json.dumps({**asdict(command), "evidence_artifact": ""}))
                or command.reason != "passed"
                or command.exit_code != 0
            ):
                raise ValueError("prepared command evidence mismatch")
        # Retain the ENTIRE prepared tree, including installed packages, bin
        # links, hidden lock metadata and local modules. Nothing is classified
        # away. The existing archive reader verifies paths, links and byte hashes.
        _prepared_files(self.artifacts.get(prepared.workspace_artifact), self.artifacts)
        executions, reports = [], []
        suite_rows = {row["id"]: row for row in self.state.suite_runs(task.id)}
        for run, scope in zip(runs, scopes, strict=True):
            execution = TypeAdapter(ExecutionResult).validate_json(json.dumps(run["execution"]))
            report = load_report(
                self.artifacts.get(scope["report"][1]),
                self.artifacts.get(scope["report"][0]),
                self.artifacts,
            )
            row = suite_rows[run["suite_run_id"]]
            if (
                execution.image_id != prepared.image_id
                or execution.limits != plan.limits
                or row["environment_hash"] != plan.digest
                or row["outcome"] != "completed"
                or row["exit_code"] != 0
                or not prove_suite_scope(plan, commands, execution, report, self.artifacts)
            ):
                raise ValueError("original suite lacks environment/runtime scope provenance")
            _execution_files(execution, self.artifacts, set())
            executions.append(execution)
            reports.append(report)
        if (
            len({e.container_id for e in executions}) != 3
            or len({e.evidence_artifact for e in executions}) != 3
        ):
            raise ValueError("original repetitions must be distinct executions")
        return executions, reports, prepared

    def evaluate(
        self,
        *,
        run_id: str,
        extraction_digest: str,
        candidate_id: str,
        repository: ValidationResult,
        associations: tuple[CaseValidation, ...],
        coverage: CoverageMeasurement | None = None,
    ) -> EligibleOccurrence:
        """Replay inputs from CAS on every call, including terminal recovery."""
        extraction = load_extraction(self.artifacts, extraction_digest)
        snapshot = load_snapshot(self.artifacts, extraction.snapshot_manifest)
        if not any(
            row["id"] == extraction.repository_id
            and row["commit_sha"] == snapshot.repository.commit_sha
            for row in self.state.repositories(run_id)
        ):
            raise ValueError("extraction repository differs from run/commit")
        candidate = next((c for c in extraction.candidates if c.id == candidate_id), None)
        if candidate is None:
            raise ValueError("unknown extracted candidate")
        executions, reports, prepared = self._repository(run_id, extraction, repository)
        decisions: dict[str, CriterionDecision] = {}

        def decide(criterion: str, status: CriterionStatus, reason: str, *refs: str) -> None:
            for ref in refs:
                self.artifacts.get(ref)
            decisions[criterion] = CriterionDecision(
                criterion_id=criterion, status=status, reason=reason, evidence_ids=tuple(refs)
            )

        screening = load_screening(self.artifacts, extraction.screening_artifact)
        screening_tasks = [
            self.state.get_task(event.task_id)
            for event in self.state.events(run_id)
            if event.task_id and self.state.get_task(event.task_id).stage == "screening"
        ]
        verified_screening = next(
            (
                task
                for task in screening_tasks
                if task.entity_id == extraction.repository_id
                and extraction.screening_artifact in self.state.task_evidence(task.id)
            ),
            None,
        )
        recorded = (
            {
                a.assessment.criterion_id: a.assessment
                for a in self.state.assessments(verified_screening.id)
            }
            if verified_screening
            else {}
        )
        for criterion in ("H-R01", "H-R02", "H-R03", "H-R04"):
            entry = next(
                (a for a in screening.get("assessments", []) if a["criterion_id"] == criterion),
                None,
            )
            fact = recorded.get(criterion)
            if (
                entry
                and fact
                and entry["status"] == fact.status
                and extraction.screening_artifact in fact.evidence_ids
            ):
                decide(criterion, fact.status, fact.reason_code, extraction.screening_artifact)
            else:
                decide(
                    criterion,
                    "not_evaluated",
                    "verified_screening_missing",
                    extraction.screening_artifact,
                )
        runtime_status: CriterionStatus = (
            "approved"
            if executions
            else "rejected"
            if repository.outcome == "rejected"
            else "not_evaluated"
        )
        for criterion in ("H-R05", "H-R06"):
            facts = [
                a.assessment
                for a in self.state.assessments(repository.task_id)
                if a.assessment.criterion_id == criterion
            ]
            fact = facts[-1] if facts else None
            decide(
                criterion,
                fact.status if fact else "not_evaluated",
                fact.reason_code if fact else "repository_evidence_missing",
                repository.evidence_id,
            )
        if executions:
            for criterion in ("H-R03", "H-R04"):
                # Only upgrade pending screening, never erase a factual rejection.
                if (
                    decisions[criterion].status == "not_evaluated"
                    and decisions[criterion].reason != "verified_screening_missing"
                ):
                    decide(
                        criterion,
                        "approved",
                        "selected_environment_and_three_runtime_framework_proofs",
                        extraction.plan_artifact,
                        repository.evidence_id,
                    )
        document = candidate.documents[0] if len(candidate.documents) == 1 else None
        document_verified = document is not None and bool(document.sources)
        if document:
            for source in document.sources:
                raw = snapshot.read(source.path, self.artifacts).decode().encode("utf-16-le")
                if (
                    raw[source.start_offset * 2 : source.end_offset * 2].decode("utf-16-le")
                    != source.text
                    or source.symbol != candidate.target.qualified_name
                ):
                    raise ValueError("documentation differs from original associated source")
        decide(
            "H-F04",
            document.status
            if document and (document_verified or document.status != "approved")
            else "not_evaluated",
            document.reason if document else "documentation_unavailable",
            extraction_digest,
        )
        metric = candidate.metrics[0] if len(candidate.metrics) == 1 else None
        decide(
            "H-F06",
            metric.triviality.status if metric else "not_evaluated",
            metric.triviality.reason if metric else "ast_triviality_unavailable",
            extraction_digest,
        )
        context = load_context(self.artifacts, extraction.context_reference)
        context_proven = bool(executions) and context.manifest.status == "approved"
        original_files = snapshot.contents(self.artifacts)
        if executions:
            for execution in executions:
                actual = _execution_files(execution, self.artifacts, set(original_files))
                if actual != original_files:
                    context_proven = False
        if extraction.prepared_context_reference:
            derived = load_prepared_context(self.artifacts, extraction.prepared_context_reference)
            context_proven = (
                context_proven
                and prepared is not None
                and derived.manifest.prepared_evidence == prepared.evidence_artifact
            )
        # A pruned prepared manifest is optional: the verified full T12 archive
        # above is a lossless context, including packages unsupported by pruning.
        case_sets: list[set[str]] = []
        current_proofs = []
        pair_refs = []
        traced_ids = []
        instrumented = (
            InstrumentationResponse.model_validate_json(
                self.artifacts.get(extraction.instrumentation_artifact)
            )
            if extraction.instrumentation_artifact
            else None
        )
        for ordinal, association in enumerate(associations, 1):
            current, original, manifest = replay_association(association, self.artifacts)
            ledger = json.loads(self.artifacts.get(current.evidence_id))
            if (
                ordinal > len(executions)
                or ledger["ordinal"] != ordinal
                or ledger["original_execution"] != executions[ordinal - 1].evidence_artifact
                or original != reports[ordinal - 1]
                or association.target_id != candidate.target.id
            ):
                raise ValueError("association differs from current original ordinal/target/context")
            current_proofs.append(current)
            pair_refs.append(current.evidence_id)
            case_sets.append(set(current.case_ids))
            if current.status != "evaluated":
                continue
            if (
                instrumented is None
                or manifest != instrumented.manifest
                or candidate.target.model_dump() not in [t.model_dump() for t in manifest.targets]
            ):
                raise ValueError("association differs from current original ordinal/target/context")
            traced_record = json.loads(self.artifacts.get(ledger["traced_execution"]))
            traced_record["evidence_artifact"] = ledger["traced_execution"]
            traced_execution = TypeAdapter(ExecutionResult).validate_json(json.dumps(traced_record))
            expected_context = {
                **original_files,
                **{file.path: file.source.encode() for file in manifest.files},
            }
            if (
                _execution_files(traced_execution, self.artifacts, set(expected_context))
                != expected_context
            ):
                raise ValueError("trace differs from original full source/configuration context")
            traced_ids.append(ledger["traced_execution"])
        if len(set(traced_ids)) != len(traced_ids):
            raise ValueError("one trace cannot establish three distinct repetitions")
        case_status = (
            case_minimum_status(tuple(current_proofs), minimum=4)
            if len(executions) == 3
            else "not_evaluated"
        )
        direct_status = (
            case_minimum_status(tuple(current_proofs), minimum=1)
            if len(executions) == 3
            else "not_evaluated"
        )
        decide(
            "H-F02",
            direct_status,
            "direct_executed_case_in_each_run"
            if direct_status == "approved"
            else "no_direct_executed_case_in_a_run"
            if direct_status == "rejected"
            else "direct_executed_association_unresolved",
            extraction_digest,
            *pair_refs,
        )
        decide(
            "H-F03",
            case_status,
            "four_direct_executed_cases_in_each_run"
            if case_status == "approved"
            else "insufficient_direct_cases_in_a_run"
            if case_status == "rejected"
            else "direct_case_minimum_unresolved"
            if len(current_proofs) == 3
            else "three_trusted_paired_traces_unavailable",
            *pair_refs,
        )
        attempts = []
        for execution in executions:
            for ref in (execution.stdout_artifact, execution.stderr_artifact):
                for line in self.artifacts.get(ref).decode(errors="replace").splitlines():
                    if re.search(
                        r"ECONN|ENETUNREACH|ENOTFOUND|fetch failed|network attempt", line, re.I
                    ):
                        attempts.append(line)
        review = review_dependencies(original_files, tuple(attempts))
        dependency_status = review.status if executions and context_proven else "not_evaluated"
        decide(
            "H-F05",
            dependency_status,
            review.reason
            if dependency_status == review.status
            else "isolated_reproduction_or_local_context_unverified",
            extraction_digest,
            repository.evidence_id,
            extraction.context_reference,
        )
        prerequisites = [
            candidate.source_status.status,
            *(decisions[f"H-F{n:02}"].status for n in range(2, 7)),
        ]
        function_status: CriterionStatus = (
            "rejected"
            if "rejected" in prerequisites
            else "not_evaluated"
            if "not_evaluated" in prerequisites
            else "approved"
        )
        function_refs = tuple(
            dict.fromkeys(
                [
                    extraction_digest,
                    *(
                        ref
                        for decision in decisions.values()
                        if decision.criterion_id.startswith("H-F")
                        for ref in decision.evidence_ids
                    ),
                ]
            )
        )
        decide(
            "H-F01",
            function_status,
            "implemented_documented_and_directly_tested_function"
            if function_status == "approved"
            else "function_evidence_prerequisite_rejected"
            if function_status == "rejected"
            else "function_evidence_prerequisite_unresolved",
            *function_refs,
        )
        coverage_ref = coverage.evidence_id if coverage else None
        if coverage:
            if coverage.target_id != candidate.target.id:
                raise ValueError("coverage target differs from candidate")
            self.artifacts.get(coverage.evidence_id)
        request_hash = stable_digest(
            {
                "rule_version": RULE_VERSION,
                "extraction": extraction_digest,
                "candidate": candidate_id,
                "repository": asdict(repository),
                "associations": [asdict(a) for a in associations],
                "coverage": asdict(coverage) if coverage else None,
            }
        )
        accepted = (
            all_mandatory_approved({k: v.status for k, v in decisions.items()})
            and runtime_status == "approved"
        )
        return EligibleOccurrence(
            request_hash=request_hash,
            repository_id=extraction.repository_id,
            repository_sha=snapshot.repository.commit_sha,
            extraction_digest=extraction_digest,
            candidate=candidate,
            context_reference=extraction.context_reference,
            prepared_context_reference=extraction.prepared_context_reference,
            prepared_archive=prepared.workspace_artifact if prepared else None,
            prepared_evidence=prepared.evidence_artifact if prepared else None,
            repository_evidence=repository.evidence_id,
            original_executions=tuple(e.evidence_artifact for e in executions),
            association_evidence=tuple(pair_refs),
            case_ids_per_run=tuple(tuple(sorted(ids)) for ids in case_sets),
            unresolved_case_ids_per_run=tuple(a.unresolved_case_ids for a in current_proofs),
            dependency_review=review,
            assessments=tuple(decisions[c] for c in MANDATORY_CRITERIA),
            accepted=accepted,
            coverage_evidence=coverage_ref,
            limitations=tuple(d.reason for d in decisions.values() if d.status == "not_evaluated")
            + review.limitations,
        )

    def validate(
        self,
        *,
        run_id: str,
        extraction_digest: str,
        candidate_id: str,
        repository: ValidationResult,
        associations: tuple[CaseValidation, ...],
        coverage: CoverageMeasurement | None = None,
    ) -> EligibilityResult:
        frozen_inputs = EligibilityInputs(
            extraction_digest=extraction_digest,
            candidate_id=candidate_id,
            repository=repository,
            associations=associations,
            coverage=coverage,
        )
        self._check_legacy_inputs(run_id, frozen_inputs)
        occurrence = self.evaluate(
            run_id=run_id,
            extraction_digest=extraction_digest,
            candidate_id=candidate_id,
            repository=repository,
            associations=associations,
            coverage=coverage,
        )
        digest = self.artifacts.put(occurrence.model_dump_json().encode())
        tasks = {event.task_id for event in self.state.events(run_id) if event.task_id}
        for task_id in tasks:
            task = self.state.get_task(task_id)
            if task.stage != INSTANCE_STAGE or task.entity_id != candidate_id:
                continue
            for event in self.state.task_checkpoints(task_id):
                checkpoint = json.loads(self.artifacts.get(str(event.payload["digest"])))
                if checkpoint["request_hash"] != occurrence.request_hash:
                    raise ValueError("instance validation inputs are immutable; create a new run")
                if checkpoint["kind"] == "eligibility_request":
                    for reference in checkpoint["artifacts"]:
                        self.artifacts.get(reference)
                    continue
                previous = EligibleOccurrence.model_validate_json(
                    self.artifacts.get(str(event.payload["digest"]))
                )
                if previous != occurrence:
                    raise ValueError("instance validation replay differs from original decisions")
                if task.outcome != "running":
                    return EligibilityResult(task_id, digest, occurrence)
            if task.outcome == "running":
                return self._finish(task_id, digest, occurrence)
        refs = list(
            dict.fromkeys(
                [
                    extraction_digest,
                    repository.evidence_id,
                    *(a.evidence_id for a in associations),
                    *occurrence.association_evidence,
                ]
            )
        )
        if coverage:
            refs.append(coverage.evidence_id)
        request = self.artifacts.put(
            json.dumps(
                {
                    "kind": "eligibility_request",
                    "request_hash": occurrence.request_hash,
                    "artifacts": refs,
                    "inputs": frozen_inputs.model_dump(mode="json"),
                },
                sort_keys=True,
            ).encode()
        )
        with self.state.transaction():
            task_id = self.state.start_task(run_id, candidate_id, INSTANCE_STAGE)
            for reference in (*refs, request):
                self.state.register_artifact(reference)
            self.state.record_task_checkpoint(task_id, "eligibility_request", request)
        return self._finish(task_id, digest, occurrence)

    def _check_legacy_inputs(self, run_id: str, inputs: EligibilityInputs) -> None:
        """A new methodology may reassess, but cannot change the frozen observations."""
        tasks = {event.task_id for event in self.state.events(run_id) if event.task_id}
        for task_id in tasks:
            task = self.state.get_task(task_id)
            if task.stage != "instance_validation" or task.entity_id != inputs.candidate_id:
                continue
            found = False
            for event in self.state.task_checkpoints(task_id):
                checkpoint = json.loads(self.artifacts.get(str(event.payload["digest"])))
                if checkpoint["kind"] != "eligibility_request":
                    continue
                if "inputs" not in checkpoint:
                    raise ValueError(
                        "legacy immutable eligibility inputs unavailable; create a new run"
                    )
                original = EligibilityInputs.model_validate_json(json.dumps(checkpoint["inputs"]))
                if original != inputs:
                    raise ValueError(
                        "instance validation inputs are immutable across methodology versions"
                    )
                for reference in checkpoint["artifacts"]:
                    self.artifacts.get(reference)
                found = True
            if not found:
                raise ValueError(
                    "legacy immutable eligibility inputs unavailable; create a new run"
                )

    def load(self, run_id: str, digest: str) -> EligibilityResult:
        """T16 boundary: verify durable ownership and rederive every mandatory gate."""
        raw = self.artifacts.get(digest)
        metadata = json.loads(raw)
        if metadata.get("schema_version") != "1.1" or metadata.get("rule_version") != RULE_VERSION:
            raise ValueError(
                "unsupported eligibility methodology; reassess the frozen original inputs"
            )
        saved = EligibleOccurrence.model_validate_json(raw)
        tasks = {event.task_id for event in self.state.events(run_id) if event.task_id}
        for task_id in tasks:
            task = self.state.get_task(task_id)
            if (
                task.entity_id != saved.candidate.id
                or task.stage != INSTANCE_STAGE
                or digest not in self.state.task_evidence(task_id)
            ):
                continue
            for event in self.state.task_checkpoints(task_id):
                checkpoint = json.loads(self.artifacts.get(str(event.payload["digest"])))
                if checkpoint["kind"] != "eligibility_request":
                    continue
                for reference in checkpoint["artifacts"]:
                    self.artifacts.get(reference)
                inputs = EligibilityInputs.model_validate_json(json.dumps(checkpoint["inputs"]))
                replayed = self.evaluate(
                    run_id=run_id,
                    extraction_digest=inputs.extraction_digest,
                    candidate_id=inputs.candidate_id,
                    repository=inputs.repository,
                    associations=inputs.associations,
                    coverage=inputs.coverage,
                )
                if replayed != saved or checkpoint["request_hash"] != saved.request_hash:
                    raise ValueError("eligibility manifest differs from CAS replay")
                recorded = self.state.assessments(task_id)
                if len(recorded) != len(saved.assessments) or any(
                    fact.assessment.criterion_id != decision.criterion_id
                    or fact.assessment.status != decision.status
                    or fact.assessment.reason_code != decision.reason
                    or fact.assessment.evidence_ids != (digest, *decision.evidence_ids)
                    for fact, decision in zip(recorded, saved.assessments, strict=True)
                ):
                    raise ValueError("eligibility assessment history differs from CAS replay")
                return EligibilityResult(task_id, digest, replayed)
        raise ValueError("eligibility manifest lacks a durable request in this run")

    def _finish(
        self, task_id: str, digest: str, occurrence: EligibleOccurrence
    ) -> EligibilityResult:
        with self.state.transaction():
            self.state.register_artifact(digest)
            self.state.record_task_checkpoint(task_id, "eligibility", digest)
            for decision in occurrence.assessments:
                for ref in decision.evidence_ids:
                    self.state.register_artifact(ref)
                self.state.append_assessment(
                    Assessment(
                        occurrence.candidate.id,
                        decision.criterion_id,
                        decision.status,
                        decision.reason,
                        (digest, *decision.evidence_ids),
                    ),
                    task_id=task_id,
                    rule_version=RULE_VERSION,
                    stage=INSTANCE_STAGE,
                    explanation=decision.reason,
                )
            outcome: TaskOutcome = (
                "completed"
                if occurrence.accepted
                else "rejected"
                if any(d.status == "rejected" for d in occurrence.assessments)
                else "unsupported"
            )
            self.state.finish_task(
                task_id,
                outcome,
                evidence_ids=(digest,),
                explanation="instance_eligible"
                if occurrence.accepted
                else "mandatory_criteria_not_all_approved",
            )
        return EligibilityResult(task_id, digest, occurrence)
