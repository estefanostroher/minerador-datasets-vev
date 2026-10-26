"""Observational direct-call validation; boolean adapter flags are not evidence."""

import hashlib
import io
import json
import tarfile
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any, Literal

from robo_minerador.execution.docker import STORAGE_POLICY, ExecutionResult
from robo_minerador.execution.results import CaseResult, FrameworkReport, load_report
from robo_minerador.extraction.client import ProtocolModel
from robo_minerador.extraction.instrumentation import (
    Callsite,
    InstrumentationManifest,
    verify_overlay,
)
from robo_minerador.state.artifacts import ArtifactStore


class TraceEvent(ProtocolModel):
    kind: Literal[
        "case_collected", "test_entry", "direct_callsite", "original_target_entry", "case_end"
    ]
    stream_id: str
    pid: int
    sequence: int
    file: str | None
    declaration_position: int | None
    runtime_id: str | None
    parameter_index: int | None
    invocation_id: str | None = None
    callsite_id: str | None = None
    target_id: str | None = None
    attributable: bool = False
    outcome: Literal["returned", "threw"] | None = None


def _correlate_proven_cases(
    cases: Iterable[CaseResult],
    callsites: Iterable[Callsite],
    events: list[TraceEvent],
    target_id: str,
) -> set[str]:
    """Candidate associations only. Approval additionally needs validate_cases provenance."""
    sites = {
        site.id: site
        for site in callsites
        if site.link_kind == "direct"
        and site.reason == "resolved_symbol"
        and site.symbol_target_id == target_id
    }
    result = set()
    for case in cases:
        if not case.eligible:
            continue
        matching = [
            e
            for e in events
            if (e.file, e.declaration_position, e.runtime_id, e.parameter_index)
            == (case.file, case.declaration_position, case.runtime_id, case.parameter_index)
        ]
        collected = [e for e in matching if e.kind == "case_collected"]
        starts = [e for e in matching if e.kind == "test_entry"]
        ends = [e for e in matching if e.kind == "case_end"]
        if len(collected) != 1 or len(starts) != 1 or len(ends) != 1:
            continue
        first, start, end = collected[0], starts[0], ends[0]
        if (
            first.stream_id != start.stream_id
            or start.stream_id != end.stream_id
            or first.pid != start.pid
            or start.pid != end.pid
            or not first.sequence < start.sequence < end.sequence
            or end.outcome != "returned"
        ):
            continue
        for call in matching:
            site = sites.get(call.callsite_id or "")
            if (
                call.kind != "direct_callsite"
                or site is None
                or call.target_id != target_id
                or site.case_start_offset != case.declaration_position
                or site.callsite_path != case.file
                or call.stream_id != start.stream_id
                or call.pid != start.pid
                or call.invocation_id is None
                or not start.sequence < call.sequence < end.sequence
            ):
                continue
            entries = [
                e
                for e in matching
                if e.kind == "original_target_entry"
                and e.stream_id == call.stream_id
                and e.pid == call.pid
                and e.invocation_id == call.invocation_id
                and e.callsite_id == site.id
                and e.target_id == target_id
                and e.attributable
                and call.sequence < e.sequence < end.sequence
            ]
            if len(entries) == 1:
                result.add(case.identity)
    return result


@dataclass(frozen=True)
class CaseCorrelation:
    case_ids: tuple[str, ...]
    unresolved_case_ids: tuple[str, ...]
    uncertainty_reasons: tuple[str, ...]


def correlate_case_evidence(
    cases: Iterable[CaseResult],
    callsites: Iterable[Callsite],
    events: list[TraceEvent],
    target_id: str,
) -> CaseCorrelation:
    """Separate observed absence from missing mapping/context/instrumentation.

    A complete case window with no reached direct call is a negative observation.
    A complete reached call without an original entry is likewise mock-only, not
    an instrumentation failure. Missing events (including sequence holes) cannot
    establish either negative fact.
    """
    cases, callsites = tuple(cases), tuple(callsites)
    proven = _correlate_proven_cases(cases, callsites, events, target_id)
    unresolved, reasons = set(), set()
    for case in cases:
        if case.status != "passed" or case.expected_failure:
            continue  # Actual failures/skips/expected failures are conclusively excluded.
        reason = ""
        matching = [
            e
            for e in events
            if (e.file, e.declaration_position, e.runtime_id, e.parameter_index)
            == (case.file, case.declaration_position, case.runtime_id, case.parameter_index)
        ]
        windows = [
            [e for e in matching if e.kind == kind]
            for kind in ("case_collected", "test_entry", "case_end")
        ]
        if not case.eligible:
            reason = "runtime_case_mapping_unavailable"
        elif any(len(window) != 1 for window in windows):
            reason = "case_context_unavailable"
        else:
            collected, start, end = (window[0] for window in windows)
            if (
                len({(e.stream_id, e.pid) for e in (collected, start, end)}) != 1
                or not collected.sequence < start.sequence < end.sequence
                or end.outcome != "returned"
            ):
                reason = "case_context_inconsistent"
            else:
                sequences = [
                    e.sequence
                    for e in events
                    if (e.stream_id, e.pid) == (start.stream_id, start.pid)
                    and start.sequence <= e.sequence <= end.sequence
                ]
                if sequences != list(range(start.sequence, end.sequence + 1)):
                    reason = "case_trace_sequence_incomplete"
        if not reason and case.identity not in proven:
            if any(
                site.callsite_path == case.file
                and site.case_start_offset == case.declaration_position
                and site.symbol_target_id in {None, target_id}
                and (site.link_kind != "direct" or site.reason != "resolved_symbol")
                for site in callsites
            ):
                reason = "static_direct_link_unavailable"
            elif any(
                e.kind == "original_target_entry" and e.target_id == target_id and e.attributable
                for e in matching
            ):
                reason = "original_entry_without_matching_direct_invocation"
        if reason:
            proven.discard(case.identity)
            unresolved.add(case.identity)
            reasons.add(reason)
    return CaseCorrelation(tuple(sorted(proven)), tuple(sorted(unresolved)), tuple(sorted(reasons)))


def correlate_cases(
    cases: Iterable[CaseResult],
    callsites: Iterable[Callsite],
    events: list[TraceEvent],
    target_id: str,
) -> set[str]:
    """Legacy set API: only positive observations, never a claim of completeness."""
    return set(correlate_case_evidence(cases, callsites, events, target_id).case_ids)


@dataclass(frozen=True)
class CaseValidation:
    status: Literal["evaluated", "not_evaluated"]
    case_ids: tuple[str, ...]
    target_id: str
    reason: str
    evidence_id: str
    unresolved_case_ids: tuple[str, ...] = ()
    uncertainty_reasons: tuple[str, ...] = ()


def trace_mode_supported(raw: Mapping[str, Any]) -> bool:
    """Corroborate adapter support against raw evidence, not its normalized flag."""
    config = raw.get("configuration")
    transform = raw.get("transform")
    if not isinstance(config, dict) or not isinstance(transform, dict):
        return False
    if raw.get("compiler_version") != "5.8.3":
        return False
    version = raw.get("version")
    if raw.get("framework") == "vitest":
        sequence = config.get("sequence", {})
        return (
            version in {"3.2.4", "4.0.0"}
            and config.get("mode") == "esm-vite"
            and transform == {"name": "vite", "version": "6.1.0"}
            and config.get("projectCount") == 1
            and config.get("pool") == "forks"
            and config.get("fileParallelism") is False
            and type(config.get("maxWorkers")) is int
            and config.get("maxWorkers") == 1
            and isinstance(sequence, dict)
            and sequence.get("concurrent") is not True
            and sequence.get("shuffle") is not True
        )
    if raw.get("framework") != "jest" or version not in {"29.7.0", "30.0.0"}:
        return False
    projects = config.get("projects")
    global_config = config.get("global")
    if (
        config.get("mode") != "cjs-ts-jest"
        or transform != {"name": "ts-jest", "version": "29.4.5"}
        or "--runInBand" not in config.get("argv", [])
        or not isinstance(projects, list)
        or len(projects) != 1
        or not isinstance(projects[0], dict)
        or not isinstance(global_config, dict)
        or global_config.get("projects")
        or global_config.get("randomize") is True
    ):
        return False
    runner = projects[0].get("runner")
    if not isinstance(runner, dict):
        return False
    path = runner.get("package_json_path")
    if not isinstance(path, str) or not path.startswith("/workspace/"):
        return False
    if not path.endswith("/node_modules/jest-circus/package.json"):
        return False
    if "\\" in path or any(p in {".", ".."} for p in path.split("/")):
        return False
    entry = path.removesuffix("package.json") + (
        "runner.js" if version == "29.7.0" else "build/runner.js"
    )
    return (
        runner.get("namespace") == "jest-circus/runner"
        and runner.get("package_name") == "jest-circus"
        and runner.get("package_version") == version
        and runner.get("resolved_path") == entry
        and runner.get("configured_path") == entry
        and runner.get("configured_realpath") == entry
        and projects[0].get("testRunner") == entry
    )


def _execution_files(
    execution: ExecutionResult, store: ArtifactStore, names: set[str]
) -> dict[str, bytes]:
    if (
        not execution.storage_verified
        or execution.storage_policy != STORAGE_POLICY
        or execution.exit_code != 0
        or execution.reason != "passed"
        or execution.timed_out
        or execution.oom_killed
        or not execution.workspace_artifact
        or execution.network_mode != "none"
    ):
        raise ValueError("untrusted observational execution")
    recorded = json.loads(store.get(execution.evidence_artifact))
    expected = json.loads(json.dumps(asdict(execution)))
    expected["evidence_artifact"] = ""
    if recorded != expected:
        raise ValueError("execution ledger mismatch")
    with tarfile.open(fileobj=io.BytesIO(store.get(execution.workspace_artifact))) as tar:
        result = {}
        for name in names:
            entry = tar.getmember("workspace/" + name)
            if not entry.isfile():
                raise ValueError("observational evidence is not a regular file")
            stream = tar.extractfile(entry)
            if stream is None:
                raise ValueError("missing observational evidence")
            result[name] = stream.read()
        return result


def validate_cases(
    original: FrameworkReport,
    traced: FrameworkReport,
    manifest: InstrumentationManifest,
    target_id: str,
    original_execution: ExecutionResult,
    traced_execution: ExecutionResult,
    trace: bytes,
    artifacts: ArtifactStore,
    *,
    pair_ordinal: int = 1,
    observable_paths: tuple[str, ...] = (),
) -> CaseValidation:
    """Validate one original/reproduction pair; callers may record ordinals 1..3.

    These are observational reproductions, not original H-R05 suites. Full-suite
    scope/completeness remains unknown. No T13 three-suite execution is performed.
    Archives, raw/normalized reports, supplements and the trace must resolve in CAS.
    """
    if pair_ordinal not in (1, 2, 3):
        raise ValueError("pair ordinal must be 1, 2 or 3")
    ledger: dict[str, object] = {
        "schema_version": "1.0",
        "purpose": "observational_pair",
        "association_rule_version": "T11-v2",
        "ordinal": pair_ordinal,
        "original_execution": original_execution.evidence_artifact,
        "traced_execution": traced_execution.evidence_artifact,
        "original_raw": original.evidence_id,
        "traced_raw": traced.evidence_id,
        "original_normalized": original.normalized_evidence_id,
        "traced_normalized": traced.normalized_evidence_id,
        "trace": artifacts.put(trace),
        "manifest": artifacts.put(manifest.model_dump_json().encode()),
        "target_id": target_id,
        "scope": "unknown",
        "completeness": "not_evaluated",
    }

    def finish(
        status: Literal["evaluated", "not_evaluated"],
        ids: set[str],
        reason: str,
        unresolved: tuple[str, ...] = (),
        uncertainty_reasons: tuple[str, ...] = (),
    ) -> CaseValidation:
        ordered = tuple(sorted(ids))
        evidence = artifacts.put(
            json.dumps(
                {
                    **ledger,
                    "status": status,
                    "case_ids": ordered,
                    "reason": reason,
                    "unresolved_case_ids": unresolved,
                    "uncertainty_reasons": uncertainty_reasons,
                },
                sort_keys=True,
            ).encode()
        )
        return CaseValidation(
            status, ordered, target_id, reason, evidence, unresolved, uncertainty_reasons
        )

    if (
        manifest.status != "ready"
        or original.support.status != "tested"
        or traced.support.status != "tested"
        or original.trace_support is None
        or original.trace_support.status != "tested"
        or traced.trace_support is None
        or traced.trace_support.status != "tested"
        or manifest.typescript_version != "5.8.3"
        or manifest.framework != original.framework
        or manifest.framework != traced.framework
        or manifest.framework_version != original.framework_version
        or manifest.framework_version != traced.framework_version
    ):
        return finish("not_evaluated", set(), "unsupported_instrumentation_or_framework_mode")
    if target_id not in {target.id for target in manifest.targets}:
        raise ValueError("unknown original target identity")
    for report in (original, traced):
        if not trace_mode_supported(json.loads(artifacts.get(report.evidence_id))):
            return finish("not_evaluated", set(), "unsupported_raw_framework_configuration")
        if (
            not report.normalized_evidence_id
            or load_report(
                artifacts.get(report.normalized_evidence_id),
                artifacts.get(report.evidence_id),
                artifacts,
            )
            != report
        ):
            raise ValueError("report evidence mismatch")
    if [(c.identity, c.status, c.expected_failure, c.mapping) for c in original.cases] != [
        (c.identity, c.status, c.expected_failure, c.mapping) for c in traced.cases
    ]:
        return finish("not_evaluated", set(), "baseline_reproduction_observable_mismatch")
    names = {f.path for f in manifest.files} | {".robo-reports/raw.json"} | set(observable_paths)
    baseline_files = _execution_files(original_execution, artifacts, names)
    traced_files = _execution_files(
        traced_execution, artifacts, names | {".robo-reports/trace.jsonl"}
    )
    if (
        baseline_files[".robo-reports/raw.json"] != artifacts.get(original.evidence_id)
        or traced_files[".robo-reports/raw.json"] != artifacts.get(traced.evidence_id)
        or traced_files[".robo-reports/trace.jsonl"] != trace
    ):
        raise ValueError("workspace trace/report provenance mismatch")
    for file in manifest.files:
        verify_overlay(baseline_files[file.path], file)
        if (
            hashlib.sha256(baseline_files[file.path]).hexdigest() != file.original_sha256
            or hashlib.sha256(traced_files[file.path]).hexdigest() != file.overlay_sha256
            or traced_files[file.path] != file.source.encode()
        ):
            raise ValueError("original/overlay source hash mismatch")
    for path in observable_paths:
        if baseline_files[path] != traced_files[path]:
            return finish("not_evaluated", set(), "baseline_reproduction_observable_mismatch")
    supplements = dict(traced_execution.supplements)
    manifest_id = supplements.get("/opt/robo-reporters/manifest.cjs")
    encoded = artifacts.get(manifest_id).decode() if manifest_id else ""
    if (
        not encoded.startswith("module.exports = ")
        or not encoded.endswith(";\n")
        or json.loads(encoded[len("module.exports = ") : -2]) != manifest.model_dump()
    ):
        raise ValueError("missing executed manifest supplement")
    if "/opt/robo-reporters/runtime.cjs" not in supplements:
        raise ValueError("missing executed runtime supplement")
    ledger["supplements"] = supplements
    events = [TraceEvent.model_validate_json(line) for line in trace.splitlines() if line]
    streams: dict[tuple[str, int], list[int]] = {}
    for event in events:
        streams.setdefault((event.stream_id, event.pid), []).append(event.sequence)
    if any(values != sorted(set(values)) for values in streams.values()):
        return finish("not_evaluated", set(), "trace_stream_order_invalid")
    correlation = correlate_case_evidence(traced.cases, manifest.callsites, events, target_id)
    return finish(
        "evaluated",
        set(correlation.case_ids),
        "passed_static_direct_and_original_entry_in_same_invocation",
        correlation.unresolved_case_ids,
        correlation.uncertainty_reasons,
    )


def valid_case_ids(candidates: CaseValidation | Iterable[Mapping[str, object]]) -> set[str]:
    # Legacy adapter-shaped dictionaries cannot establish an observational link.
    if isinstance(candidates, CaseValidation) and candidates.status == "evaluated":
        return set(candidates.case_ids)
    return set()


def meets_minimum_in_each_run(case_sets: list[set[str]]) -> bool:
    """H-F03 counts distinct observed cases separately in each original repetition."""
    return len(case_sets) == 3 and all(len(cases) >= 4 for cases in case_sets)
