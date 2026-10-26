"""Direct associations require corroborated observational evidence."""

import hashlib
import io
import json
import subprocess
import tarfile
from dataclasses import asdict, replace
from functools import lru_cache
from pathlib import Path

import pytest

from robo_minerador.environments.plan import Limits
from robo_minerador.execution.docker import STORAGE_POLICY, ExecutionResult
from robo_minerador.execution.results import CaseResult, load_report
from robo_minerador.extraction.instrumentation import (
    Callsite,
    InstrumentationManifest,
    OverlayFile,
    verify_overlay,
)
from robo_minerador.state.artifacts import ArtifactStore
from robo_minerador.validation.cases import (
    TraceEvent,
    correlate_cases,
    trace_mode_supported,
    valid_case_ids,
    validate_cases,
)


def test_booleans_without_original_entry_are_not_evidence():
    candidate = [
        {
            "case_id": "c1",
            "status": "passed",
            "direct": True,
            "callsite_reached": True,
            "original_entered": False,
        }
    ]
    assert valid_case_ids(candidate) == set()


def test_all_true_booleans_are_still_not_trace_provenance():
    candidate = [
        {
            "case_id": "c1",
            "status": "passed",
            "direct": True,
            "callsite_reached": True,
            "original_entered": True,
        }
    ]
    assert valid_case_ids(candidate) == set()


def case():
    return CaseResult(
        "jest",
        "29.7.0",
        "a.test.ts",
        10,
        ("same",),
        None,
        "0",
        "passed",
        1.0,
        False,
        "trusted",
        "a" * 64,
    )


def callsite():
    return Callsite(
        id="b" * 64,
        symbol_target_id="target",
        callsite_path="a.test.ts",
        start_offset=30,
        end_offset=38,
        case_declaration_id="declaration",
        case_title="same",
        case_start_offset=10,
        parameterized=False,
        link_kind="direct",
        reason="resolved_symbol",
        path_resolution=["value.ts"],
        runtime_confirmed=False,
        mock_potential=True,
    )


def events():
    common = {
        "stream_id": "stream",
        "pid": 1000,
        "file": "a.test.ts",
        "declaration_position": 10,
        "runtime_id": "0",
        "parameter_index": None,
    }
    values = [
        {"kind": "case_collected"},
        {"kind": "test_entry"},
        {
            "kind": "direct_callsite",
            "invocation_id": "i",
            "callsite_id": "b" * 64,
            "target_id": "target",
        },
        {
            "kind": "original_target_entry",
            "invocation_id": "i",
            "callsite_id": "b" * 64,
            "target_id": "target",
            "attributable": True,
        },
        {"kind": "case_end", "outcome": "returned"},
    ]
    return [
        TraceEvent.model_validate({**common, "sequence": i, **value})
        for i, value in enumerate(values)
    ]


def test_trace_requires_all_four_events_and_collection_identity():
    assert correlate_cases([case()], [callsite()], events(), "target") == {case().identity}
    for index in range(5):
        trace = events()
        trace.pop(index)
        assert correlate_cases([case()], [callsite()], trace, "target") == set()


@pytest.mark.parametrize(
    "change",
    [
        {"expected_failure": True},
        {"status": "failed"},
        {"status": "skipped"},
        {"mapping": "not_evaluated"},
        {"runtime_id": "another"},
        {"declaration_position": 11},
        {"parameter_index": 0},
    ],
)
def test_case_contract_corroborates_trace_identity_and_eligibility(change):
    assert correlate_cases([replace(case(), **change)], [callsite()], events(), "target") == set()


@pytest.mark.parametrize(
    "change",
    [
        {"attributable": False},
        {"invocation_id": "nested"},
        {"target_id": "other-class"},
        {"callsite_id": "c" * 64},
        {"runtime_id": "another"},
        {"stream_id": "other-stream"},
        {"sequence": 5},
    ],
)
def test_mock_hook_helper_other_class_and_cross_context_events_cannot_approve(change):
    trace = events()
    trace[3] = trace[3].model_copy(update=change)
    assert correlate_cases([case()], [callsite()], trace, "target") == set()


def test_unresolved_link_or_thrown_case_cannot_approve_even_if_adapter_says_entry():
    unresolved = callsite().model_copy(update={"link_kind": "unresolved"})
    assert correlate_cases([case()], [unresolved], events(), "target") == set()
    trace = events()
    trace[-1] = trace[-1].model_copy(update={"outcome": "threw"})
    assert correlate_cases([case()], [callsite()], trace, "target") == set()


def test_duplicate_case_windows_are_ambiguous_and_candidate_ids_are_not_validations():
    trace = events() + events()
    assert correlate_cases([case()], [callsite()], trace, "target") == set()
    # Even correctly correlated candidate IDs need Docker/archive provenance.
    assert valid_case_ids([{"case_id": case().identity, "events": events()}]) == set()


def correlation(cases, trace):
    from robo_minerador.validation import cases as module

    rule = getattr(module, "correlate_case_evidence", None)
    assert rule is not None, "T11 drops the distinction between unknown and conclusively absent"
    return rule(cases, [callsite()], trace, "target")


@pytest.mark.parametrize("missing", range(5))
def test_missing_trace_events_are_unknown_not_a_conclusive_zero(missing):
    trace = events()
    trace.pop(missing)
    result = correlation([case()], trace)
    assert not result.case_ids
    assert result.unresolved_case_ids == (case().identity,)


def test_passed_case_with_unavailable_mapping_is_unknown():
    unmapped = replace(case(), mapping="not_evaluated")
    result = correlation([unmapped], events())
    assert not result.case_ids
    assert result.unresolved_case_ids == (unmapped.identity,)


def test_loss_in_another_case_does_not_erase_an_observed_complete_invocation():
    known = events()
    unrelated = [
        event.model_copy(
            update={"runtime_id": "1", "declaration_position": 20, "sequence": event.sequence + 1}
        )
        for event in events()
        if event.kind != "test_entry"
    ]
    # Framework collection precedes both executions, as in real Jest/Vitest.
    trace = [
        known[0],
        *unrelated,
        *(event.model_copy(update={"sequence": event.sequence + 5}) for event in known[1:]),
    ]
    unknown = replace(case(), runtime_id="1", declaration_position=20)
    result = correlation([case(), unknown], trace)
    assert result.case_ids == (case().identity,)
    assert result.unresolved_case_ids == (unknown.identity,)


@pytest.mark.parametrize("variant", ["mock_only", "unreached", "helper"])
def test_complete_context_can_prove_no_direct_original_invocation(variant):
    trace = events()
    if variant == "mock_only":
        trace.pop(3)  # A reached direct call but a mock never enters the original.
    elif variant == "unreached":
        del trace[2:4]  # The static direct call lives in a branch not taken.
    else:
        trace[3] = trace[3].model_copy(update={"attributable": False})
    trace = [event.model_copy(update={"sequence": i}) for i, event in enumerate(trace)]
    result = correlation([case()], trace)
    assert not result.case_ids and not result.unresolved_case_ids


@pytest.mark.parametrize(
    "change",
    [
        {"expected_failure": True},
        {"status": "failed"},
        {"status": "skipped"},
        {"status": "pending"},
        {"status": "todo"},
        {"status": "not_executed"},
    ],
)
def test_excluded_outcomes_remain_conclusive_even_without_mapping_or_context(change):
    excluded = replace(case(), mapping="not_evaluated", **change)
    result = correlation([excluded], [])
    assert not result.case_ids and not result.unresolved_case_ids


def test_utf16_overlay_coordinates_replay_original_bytes_and_reject_false_maps():
    original = "// 🧪\nexport function f() { return 1; }"
    offset = len("// 🧪\n".encode("utf-16-le")) // 2
    source = "// 🧪\n/* probe */export function f() { return 1; }"

    def digest(value):
        return hashlib.sha256(value.encode()).hexdigest()

    overlay = OverlayFile.model_validate(
        {
            "path": "value.ts",
            "original_sha256": digest(original),
            "overlay_sha256": digest(source),
            "source": source,
            "edits": [
                {
                    "start": offset,
                    "end": offset,
                    "text": "/* probe */",
                    "overlay_start": offset,
                    "overlay_end": offset + 11,
                }
            ],
            "transpilation": {
                "typescript_version": "5.8.3",
                "input_sha256": digest(source),
                "javascript": "",
                "source_map": "{}",
            },
        }
    )
    verify_overlay(original.encode(), overlay)
    edited = overlay.model_copy(deep=True)
    edited.edits[0].overlay_start += 1
    with pytest.raises(ValueError, match="coordinates"):
        verify_overlay(original.encode(), edited)
    with pytest.raises(ValueError, match="hash"):
        verify_overlay((original + "\n").encode(), overlay)


@lru_cache(maxsize=4)
def controlled_fixture(framework, version):
    """Static inputs built from source; never a claim of Docker execution."""
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            "node",
            str(root / "tools/ts-analyzer/dist/test/framework-fixtures.js"),
            framework,
            version,
            "serial",
        ],
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def controlled_pair(tmp_path, framework, version, *, missing_mapping=False, missing_context=False):
    """Build a complete, small engineering CAS in pytest's temporary directory."""
    value = json.loads(json.dumps(controlled_fixture(framework, version)))
    if missing_mapping:
        first = next(
            c
            for c in value["normalized"]["cases"]
            if c["status"] == "passed" and not c["expected_failure"]
        )
        first["mapping"] = "not_evaluated"
    store = ArtifactStore(tmp_path / "artifacts")
    raw = json.dumps(value["raw"], ensure_ascii=False, separators=(",", ":")).encode()
    report = load_report(json.dumps(value["normalized"]).encode(), raw, store)
    manifest = InstrumentationManifest.model_validate(value["manifest"])
    target = next(t for t in manifest.targets if t.qualified_name == "magnitude")
    trace_events = []
    expected = set()
    for case_result in report.cases:
        site = next(
            (
                s
                for s in manifest.callsites
                if s.symbol_target_id == target.id
                and s.callsite_path == case_result.file
                and s.case_start_offset == case_result.declaration_position
            ),
            None,
        )
        common = {
            "stream_id": "controlled",
            "pid": 1,
            "file": case_result.file,
            "declaration_position": case_result.declaration_position,
            "runtime_id": case_result.runtime_id,
            "parameter_index": case_result.parameter_index,
        }
        rows = [{"kind": "case_collected"}, {"kind": "test_entry"}]
        if site is not None:
            invocation = {
                "invocation_id": case_result.identity,
                "callsite_id": site.id,
                "target_id": target.id,
            }
            rows += [
                {"kind": "direct_callsite", **invocation},
                {"kind": "original_target_entry", "attributable": True, **invocation},
            ]
            if case_result.eligible:
                expected.add(case_result.identity)
        rows += [{"kind": "case_end", "outcome": "returned"}]
        for row in rows:
            trace_events.append({**common, "sequence": len(trace_events), **row})
    if missing_context:
        first = next(c for c in report.cases if c.eligible)
        trace_events = [
            row
            for row in trace_events
            if not (
                row["kind"] == "test_entry"
                and row["file"] == first.file
                and row["runtime_id"] == first.runtime_id
                and row["declaration_position"] == first.declaration_position
                and row["parameter_index"] == first.parameter_index
            )
        ]
    trace = b"\n".join(json.dumps(row).encode() for row in trace_events) + b"\n"
    manifest_supplement = store.put(
        b"module.exports = " + manifest.model_dump_json().encode() + b";\n"
    )
    runtime_supplement = store.put(b"// Controlled unit ledger; no runtime execution claimed.\n")

    def execution(traced):
        files = {
            f.path: (f.source if traced else value["sources"][f.path]).encode()
            for f in manifest.files
        }
        files[".robo-reports/raw.json"] = raw
        if traced:
            files[".robo-reports/trace.jsonl"] = trace
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            for name, body in files.items():
                entry = tarfile.TarInfo("workspace/" + name)
                entry.size = len(body)
                tar.addfile(entry, io.BytesIO(body))
        empty = store.put(b"")
        observed = store.put(b'{"purpose":"controlled_unit_input"}')
        result = ExecutionResult(
            container_id="controlled-unit-no-container",
            image_id="controlled-unit-no-image",
            argv=("controlled",),
            cwd="/workspace",
            environment=(),
            limits=Limits(1, 128, 16),
            timeout_seconds=1,
            network_mode="none",
            phase="test",
            exit_code=0,
            signal=None,
            timed_out=False,
            oom_killed=False,
            elapsed_seconds=0,
            reason="passed",
            stdout_artifact=empty,
            stderr_artifact=empty,
            workspace_artifact=store.put(archive.getvalue()),
            workspace_used_bytes=len(archive.getvalue()),
            storage_policy=STORAGE_POLICY,
            storage_verified=True,
            storage_observation_artifact=observed,
            memory_oom_events=0,
            container_inspect_artifact=observed,
            supplements=(
                ("/opt/robo-reporters/manifest.cjs", manifest_supplement),
                ("/opt/robo-reporters/runtime.cjs", runtime_supplement),
            )
            if traced
            else (),
        )
        return replace(result, evidence_artifact=store.put(json.dumps(asdict(result)).encode()))

    return store, manifest, report, trace, target.id, execution(False), execution(True), expected


@pytest.mark.parametrize("variant", ["missing_mapping", "missing_context"])
def test_partial_t11_uncertainty_survives_sealed_cas_and_coverage_replay(tmp_path, variant):
    from robo_minerador.validation.coverage import verify_association

    store, manifest, report, trace, target, baseline, traced, _ = controlled_pair(
        tmp_path, "jest", "29.7.0", **{variant: True}
    )
    result = validate_cases(report, report, manifest, target, baseline, traced, trace, store)
    assert result.status == "evaluated" and len(result.case_ids) == 6
    assert len(result.unresolved_case_ids) == 1
    ledger = json.loads(store.get(result.evidence_id))
    assert ledger["unresolved_case_ids"] == list(result.unresolved_case_ids)
    assert verify_association(result, store) == (report, manifest)
    with pytest.raises(ValueError, match="association provenance"):
        verify_association(replace(result, unresolved_case_ids=()), store)


def test_legacy_association_is_rederived_from_original_evidence_not_stamped_as_complete(tmp_path):
    from robo_minerador.validation.cases import CaseValidation
    from robo_minerador.validation.coverage import verify_association

    store, manifest, report, trace, target, baseline, traced, _ = controlled_pair(
        tmp_path, "jest", "29.7.0", missing_mapping=True
    )
    result = validate_cases(report, report, manifest, target, baseline, traced, trace, store)
    ledger = json.loads(store.get(result.evidence_id))
    for field in ("association_rule_version", "unresolved_case_ids", "uncertainty_reasons"):
        ledger.pop(field, None)
    legacy = CaseValidation(
        result.status,
        result.case_ids,
        target,
        result.reason,
        store.put(json.dumps(ledger, sort_keys=True).encode()),
    )
    assert verify_association(legacy, store) == (report, manifest)
    from robo_minerador.validation import coverage

    replay = getattr(coverage, "replay_association", None)
    assert replay is not None, "T14 must consume current uncertainty when replaying legacy T11"
    current, _, _ = replay(legacy, store)
    assert len(current.unresolved_case_ids) == 1
    assert current.evidence_id != legacy.evidence_id


@pytest.mark.parametrize("version", ["jest-29.7.0", "jest-30.0.0", "vitest-3.2.4", "vitest-4.0.0"])
def test_controlled_trace_and_complete_cas_corroborate_case_contract(tmp_path, version):
    framework, patch = version.split("-", 1)
    store, manifest, report, trace, target, baseline, traced, expected = controlled_pair(
        tmp_path, framework, patch
    )
    raw = json.loads(store.get(report.evidence_id))
    assert trace_mode_supported(raw)
    for field, value in (
        [
            ("mode", "unknown"),
            ("projects", []),
        ]
        if raw["framework"] == "jest"
        else [
            ("mode", "unknown"),
            ("projectCount", 2),
            ("pool", "threads"),
            ("sequence", {"concurrent": True}),
            ("sequence", {"shuffle": True}),
        ]
    ):
        changed = {**raw, "configuration": {**raw["configuration"], field: value}}
        assert not trace_mode_supported(changed)
    assert len(report.cases) == 10
    assert sum(case.eligible for case in report.cases) == 7
    assert len(expected) == 7
    validation = validate_cases(report, report, manifest, target, baseline, traced, trace, store)
    assert valid_case_ids(validation) == expected
    ledger = json.loads(store.get(validation.evidence_id))
    for key in (
        "original_execution",
        "traced_execution",
        "original_raw",
        "traced_raw",
        "original_normalized",
        "traced_normalized",
        "trace",
        "manifest",
    ):
        assert store.get(ledger[key])
    with pytest.raises(ValueError, match="provenance"):
        validate_cases(report, report, manifest, target, baseline, traced, trace + b"\n", store)
    with pytest.raises(ValueError, match="ledger mismatch"):
        validate_cases(
            report,
            report,
            manifest,
            target,
            baseline,
            replace(traced, elapsed_seconds=2),
            trace,
            store,
        )
    with pytest.raises(ValueError, match="untrusted observational"):
        validate_cases(
            report,
            report,
            manifest,
            target,
            baseline,
            replace(traced, storage_verified=False),
            trace,
            store,
        )


@pytest.mark.parametrize("version", ["3.2.4", "4.0.0"])
@pytest.mark.parametrize(
    "change",
    [
        {"fileParallelism": True, "maxWorkers": 2},
        {"fileParallelism": True},
        {"maxWorkers": 2},
        {"fileParallelism": None},
        {"maxWorkers": None},
        {"fileParallelism": "false"},
        {"maxWorkers": "1"},
        {"maxWorkers": True},
        {"maxWorkers": 1.0},
        {"fileParallelism": "missing"},
        {"maxWorkers": "missing"},
    ],
)
def test_vitest_trace_gate_requires_resolved_serial_options_without_defaults(version, change):
    raw = json.loads(json.dumps(controlled_fixture("vitest", version)["raw"]))
    raw["configuration"].update(fileParallelism=False, maxWorkers=1)
    assert trace_mode_supported(raw)
    for key, value in change.items():
        if value == "missing":
            raw["configuration"].pop(key)
        else:
            raw["configuration"][key] = value
    assert not trace_mode_supported(raw)


@pytest.mark.parametrize("version", ["3.2.4", "4.0.0"])
@pytest.mark.parametrize(
    "variant", ["parallel", "missing-fileParallelism", "missing-maxWorkers", "unknown"]
)
def test_controlled_g1_rejects_parallel_missing_unknown_and_forged_support(
    tmp_path, version, variant
):
    store, manifest, report, trace, target, baseline, traced, expected = controlled_pair(
        tmp_path, "vitest", version
    )
    raw = json.loads(store.get(report.evidence_id))
    if variant == "parallel":
        raw["configuration"].update(fileParallelism=True, maxWorkers=2)
    elif variant.startswith("missing-"):
        raw["configuration"].pop(variant.removeprefix("missing-"))
    else:
        raw["configuration"].update(fileParallelism=None, maxWorkers=None)
    assert not trace_mode_supported(raw)
    changed_id = store.put(json.dumps(raw).encode())
    changed = replace(report, evidence_id=changed_id)
    candidate_ids = correlate_cases(
        changed.cases,
        manifest.callsites,
        [TraceEvent.model_validate_json(line) for line in trace.splitlines()],
        target,
    )
    assert candidate_ids == expected and len(expected) == 7
    # Raw evidence must deny a forged 'tested' flag before trusting normalized metadata.
    independent = validate_cases(report, changed, manifest, target, baseline, traced, trace, store)
    assert independent.reason == "unsupported_raw_framework_configuration"
    assert valid_case_ids(independent) == set()
    denied = validate_cases(
        report,
        replace(changed, trace_support=replace(report.trace_support, status="not_evaluated")),
        manifest,
        target,
        baseline,
        traced,
        trace,
        store,
    )
    assert denied.status == "not_evaluated" and valid_case_ids(denied) == set()
    for validation in (independent, denied):
        ledger = json.loads(store.get(validation.evidence_id))
        assert ledger["status"] == "not_evaluated" and ledger["case_ids"] == []
