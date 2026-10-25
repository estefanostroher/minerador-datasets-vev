"""Runtime report contracts, including exclusion and identity evidence."""

import hashlib
import importlib.util
import json
import subprocess
from dataclasses import FrozenInstanceError, asdict, replace
from pathlib import Path

import pytest


def test_results_contract_exists():
    assert importlib.util.find_spec("robo_minerador.execution.results") is not None


def test_parameter_index_distinguishes_equal_titles():
    from robo_minerador.execution.results import case_identity

    assert case_identity("vitest", "a.test.ts", 100, ("f", "same"), 0) != case_identity(
        "vitest", "a.test.ts", 100, ("f", "same"), 1
    )


def test_case_eligibility_requires_execution_mapping_and_no_expected_failure():
    from robo_minerador.execution.results import CaseResult, SuiteResult

    case = CaseResult(
        framework="jest",
        framework_version="29.7.0",
        file="a.test.ts",
        declaration_position=100,
        title_path=("f", "same"),
        parameter_index=0,
        runtime_id="0",
        status="passed",
        duration_ms=1.0,
        expected_failure=False,
        mapping="trusted",
        evidence_id="a" * 64,
    )
    assert case.eligible
    for changes in (
        {"expected_failure": True},
        {"status": "pending"},
        {"mapping": "not_evaluated"},
        {"status": "skipped"},
    ):
        assert not replace(case, **changes).eligible
    with pytest.raises(FrozenInstanceError):
        case.status = "failed"
    suite = SuiteResult(1, "failed", 1, "a" * 64)
    assert json.loads(json.dumps(asdict(suite)))["outcome"] == "failed"


@pytest.mark.parametrize(
    ("framework", "version"),
    [("jest", "29.7.0"), ("jest", "30.0.0"), ("vitest", "3.2.4"), ("vitest", "4.0.0")],
)
def test_controlled_reports_cross_language_and_artifact_roundtrip(tmp_path, framework, version):
    from robo_minerador.execution.results import load_report
    from robo_minerador.state.artifacts import ArtifactStore

    root = Path(__file__).resolve().parents[2]
    fixture = subprocess.run(
        [
            "node",
            str(root / "tools/ts-analyzer/dist/test/framework-fixtures.js"),
            framework,
            version,
        ],
        capture_output=True,
        check=True,
    )
    raw = json.dumps(json.loads(fixture.stdout)["raw"], separators=(",", ":")).encode()
    source = (root / f"tests/fixtures/projects/{framework}-basic/value.test.ts").read_bytes()
    source = source.decode("utf-8")
    # Static analyzer only; no project scripts/config are imported on the host.
    normalized = subprocess.run(
        ["node", str(root / "tools/ts-analyzer/dist/src/frameworks/normalize.js")],
        input=json.dumps(
            {
                "raw": json.loads(raw),
                "sources": {"value.test.ts": source},
                "evidence_id": hashlib.sha256(raw).hexdigest(),
            }
        ).encode(),
        capture_output=True,
        check=True,
    ).stdout
    store = ArtifactStore(tmp_path / "artifacts")
    report = load_report(normalized, raw, store)
    assert len(report.cases) == 10
    assert sum(c.eligible for c in report.cases) == 7
    assert len({c.identity for c in report.cases}) == 10
    assert sum(c.expected_failure for c in report.cases) == 1
    assert store.get(report.evidence_id) == raw
    assert report.completeness == "not_evaluated"
    assert report.support.status == "tested"
    assert report.trace_support is not None
    if framework == "vitest":
        config = json.loads(raw)["configuration"]
        assert "fileParallelism" in config and "maxWorkers" in config
        # The default T10 result mode is not evidence of the exact T11 serial mode.
        assert config["fileParallelism"] is True
        assert report.trace_support.status == "not_evaluated"
    legacy = json.loads(normalized)
    legacy.pop("trace_support")
    old_report = load_report(json.dumps(legacy).encode(), raw, store)
    assert old_report.support.status == "tested" and old_report.trace_support is None
    prefix = source.partition("test.each")[0]
    assert report.cases[0].declaration_position == len(prefix.encode("utf-16-le")) // 2
    assert store.get(report.normalized_evidence_id) == normalized
    assert report.scope_evidence.argv
    for change in ({"status": "invented"}, {"duration_ms": -1}, {"parameter_index": True}):
        with pytest.raises(ValueError):
            replace(report.cases[0], **change)
    with pytest.raises(ValueError):
        load_report(
            normalized.replace(b'"schema_version":"1.0"', b'"schema_version":"9.0"'), raw, store
        )
    with pytest.raises(ValueError):
        load_report(normalized, b"{}", store)
    forged = json.loads(normalized)
    forged["compiler_version"] = "0.0.0"
    with pytest.raises(ValueError, match="metadata"):
        load_report(json.dumps(forged).encode(), raw, store)


@pytest.mark.parametrize(
    ("framework", "version"),
    [("jest", "29.7.0"), ("jest", "30.0.0"), ("vitest", "3.2.4"), ("vitest", "4.0.0")],
)
def test_assertion_failure_report_is_preserved_without_passing_cases(tmp_path, framework, version):
    from robo_minerador.execution.results import SuiteResult, load_report
    from robo_minerador.state.artifacts import ArtifactStore

    root = Path(__file__).resolve().parents[2]
    fixture = subprocess.run(
        [
            "node",
            str(root / "tools/ts-analyzer/dist/test/framework-fixtures.js"),
            framework,
            version,
            "failure",
        ],
        capture_output=True,
        check=True,
    )
    raw = json.dumps(json.loads(fixture.stdout)["raw"]).encode()
    source = root / f"tests/fixtures/projects/{framework}-basic/assertion.case.ts"
    normalized = subprocess.run(
        ["node", str(root / "tools/ts-analyzer/dist/src/frameworks/normalize.js")],
        input=json.dumps(
            {
                "raw": json.loads(raw),
                "sources": {"assertion.case.ts": source.read_bytes().decode("utf-8")},
                "evidence_id": hashlib.sha256(raw).hexdigest(),
            }
        ).encode(),
        capture_output=True,
        check=True,
    ).stdout
    store = ArtifactStore(tmp_path / "storefiles")
    report = load_report(normalized, raw, store)
    # Controlled unit input; real process exit codes are checked by the Docker test.
    suite = SuiteResult(1, "failed", 1, store.put(raw))
    assert suite.exit_code == 1
    assert len(report.cases) == 1
    assert report.cases[0].status == "failed" and not report.cases[0].expected_failure
    assert not any(c.eligible for c in report.cases)
    assert store.get(report.evidence_id) == raw
