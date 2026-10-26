"""Attributable descriptive metrics: absence is distinct from observed zero."""

import hashlib
from copy import deepcopy

import pytest

from robo_minerador.extraction.client import Target


@pytest.mark.parametrize("covered,total,expected", [(0, 0, None), (0, 4, 0.0), (3, 4, 75.0)])
def test_percentage(covered, total, expected):
    from robo_minerador.validation.coverage import percentage

    assert percentage(covered, total) == expected


def sample():
    source = b"function f(x) {\r\n  if (x) return 1;\r\n  return 0;\r\n}\r\n"
    end = source.index(b"}") + 1
    target = Target(
        id="target",
        kind="function",
        qualified_name="f",
        path="value.ts",
        start_offset=0,
        end_offset=end,
        start_line=1,
        start_column=1,
        end_line=4,
        end_column=2,
        code=source[:end].decode(),
        signature="f(x)",
    )

    def loc(line, start, end):
        return {"start": {"line": line, "column": start}, "end": {"line": line, "column": end}}

    body = {"start": {"line": 1, "column": 14}, "end": {"line": 4, "column": 1}}
    data = {
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "source_maps": "istanbul-provider-remapped-original",
        "functions": [{"start": 0, "end": end, "body": body, "nested": []}],
        "map": {
            "fnMap": {"0": {"loc": body}},
            "f": {"0": 1},
            "statementMap": {"0": loc(2, 2, 18), "1": loc(3, 2, 11)},
            "s": {"0": 1, "1": 0},
            "branchMap": {"0": {"locations": [loc(2, 9, 18), loc(2, 2, 18)]}},
            "b": {"0": [1, 0]},
        },
    }
    return source, target, data


def test_original_function_lines_and_branch_arms():
    from robo_minerador.validation.coverage import summarize_istanbul

    lines, branches = summarize_istanbul(*sample())
    assert (lines.covered, lines.total, lines.percentage) == (1, 2, 50.0)
    assert (branches.covered, branches.total, branches.percentage) == (1, 2, 50.0)


def test_nested_function_body_does_not_contribute():
    from robo_minerador.validation.coverage import summarize_istanbul

    source, target, data = sample()
    data["functions"][0]["nested"] = [data["map"]["statementMap"]["1"]]
    lines, _ = summarize_istanbul(source, target, data)
    assert (lines.covered, lines.total) == (1, 1)


def test_no_branches_is_not_observed_zero():
    from robo_minerador.validation.coverage import summarize_istanbul

    source, target, data = sample()
    data["map"]["branchMap"] = {}
    data["map"]["b"] = {}
    _, branches = summarize_istanbul(source, target, data)
    assert (branches.covered, branches.total, branches.percentage) == (0, 0, None)


def test_ts_jest_return_type_mapping_requires_exact_original_name_and_end():
    from robo_minerador.validation.coverage import CoverageUnattributable, summarize_istanbul

    source, target, data = sample()
    name = {"start": {"line": 1, "column": 9}, "end": {"line": 1, "column": 10}}
    data["functions"][0]["declaration"] = name
    data["map"]["fnMap"]["0"] = {
        "decl": name,
        "loc": {"start": {"line": 1, "column": 13}, "end": {"line": 4, "column": 1}},
    }
    assert summarize_istanbul(source, target, data)[0].total == 2
    data["map"]["fnMap"]["0"]["decl"] = {
        "start": {"line": 1, "column": 8},
        "end": {"line": 1, "column": 9},
    }
    with pytest.raises(CoverageUnattributable):
        summarize_istanbul(source, target, data)


def test_vite_infinite_sourcemap_end_is_a_bound_not_an_invented_extent():
    from robo_minerador.validation.coverage import summarize_istanbul

    source, target, data = sample()
    data["map"]["fnMap"]["0"] = deepcopy(data["map"]["fnMap"]["0"])
    data["map"]["fnMap"]["0"]["loc"]["end"]["column"] = None
    data["map"]["statementMap"]["1"]["end"]["column"] = None
    before = deepcopy(data)
    assert summarize_istanbul(source, target, data)[0].total == 2
    assert data == before


def test_uncertain_mapped_end_crossing_original_body_is_unavailable():
    from robo_minerador.validation.coverage import CoverageUnattributable, summarize_istanbul

    source, target, data = sample()
    source = source.replace(b"}\r\n", b"} other();\r\n")
    data["source_sha256"] = hashlib.sha256(source).hexdigest()
    data["map"]["statementMap"]["1"]["end"] = {"line": 4, "column": None}
    with pytest.raises(CoverageUnattributable):
        summarize_istanbul(source, target, data)


def test_sourcemap_losing_an_if_arm_must_not_inflate_branch_percentage():
    from robo_minerador.validation.coverage import CoverageUnattributable, summarize_istanbul

    source, target, data = sample()
    branch = data["map"]["branchMap"]["0"]
    branch["type"] = "if"
    branch["loc"] = branch["locations"][1]
    branch["locations"] = branch["locations"][:1]
    data["map"]["b"]["0"] = [1]
    with pytest.raises(CoverageUnattributable):
        summarize_istanbul(source, target, data)


@pytest.mark.parametrize("variant", ["hash", "ambiguous", "unmapped", "outside"])
def test_unattributable_original_interval_is_rejected(variant):
    from robo_minerador.validation.coverage import CoverageUnattributable, summarize_istanbul

    source, target, data = sample()
    if variant == "hash":
        source += b"changed"
    elif variant == "ambiguous":
        data["map"]["fnMap"]["1"] = deepcopy(data["map"]["fnMap"]["0"])
    elif variant == "unmapped":
        data["source_maps"] = "generated"
    else:
        data["functions"][0]["end"] -= 1
    with pytest.raises(CoverageUnattributable):
        summarize_istanbul(source, target, data)


def test_non_bmp_utf16_and_crlf_are_not_python_character_offsets():
    from robo_minerador.validation.coverage import summarize_istanbul

    source, target, data = sample()
    prefix = "// 😀\r\n"
    target.start_offset += len(prefix.encode("utf-16-le")) // 2
    target.end_offset += len(prefix.encode("utf-16-le")) // 2
    source = prefix.encode() + source
    data["source_sha256"] = hashlib.sha256(source).hexdigest()
    function = data["functions"][0]
    function["start"] = target.start_offset
    function["end"] = target.end_offset

    def shift(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "line":
                    value[key] += 1
                else:
                    shift(child)
        elif isinstance(value, list):
            for child in value:
                shift(child)

    # body and fnMap intentionally reference the same location in sample().
    shift(data["map"])
    lines, _ = summarize_istanbul(source, target, data)
    assert (lines.covered, lines.total) == (1, 2)


def case(title="direct 😀", position=0, status="passed"):
    from robo_minerador.execution.results import CaseResult

    return CaseResult(
        "jest",
        "29.7.0",
        "value.test.ts",
        position,
        (title,),
        None,
        "",
        status,
        1.0,
        False,
        "trusted",
        "0" * 64,
    )


def test_selection_deduplicates_ids_and_preserves_unicode():
    from robo_minerador.validation.coverage import select_cases

    direct = case()
    selected, pattern = select_cases((direct, case("indirect", 10)), [direct.identity] * 2)
    assert selected == (direct,)
    assert "😀" in pattern
    assert pattern.startswith("^(?:") and pattern.endswith(")$")


def test_homonymous_unselected_case_is_not_name_isolation():
    from robo_minerador.validation.coverage import CoverageUnavailable, select_cases

    direct = case()
    with pytest.raises(CoverageUnavailable, match="coverage_identity_isolation_unavailable"):
        select_cases((direct, case(position=10)), [direct.identity])


@pytest.mark.parametrize("status", ["skipped", "failed", "pending", "todo"])
def test_unapproved_runtime_cases_cannot_enter_denominator(status):
    from robo_minerador.validation.coverage import CoverageUnavailable, select_cases

    direct = case(status=status)
    with pytest.raises(CoverageUnavailable):
        select_cases((direct,), [direct.identity])


def test_measurements_are_stable_durable_and_do_not_change_eligibility(tmp_path):
    from robo_minerador.domain import Assessment
    from robo_minerador.state.artifacts import ArtifactStore
    from robo_minerador.state.store import StateStore
    from robo_minerador.validation.coverage import (
        CoverageCounter,
        CoverageMeasurement,
        persist_measurement,
    )

    artifacts = ArtifactStore(tmp_path / "cas")
    state = StateStore(tmp_path / "state.sqlite", artifacts=artifacts)
    run = state.create_run("T15")
    task = state.start_task(run, "target", "coverage")
    evidence = artifacts.put(b'{"reason":"coverage_tool_unavailable"}')
    state.register_artifact(evidence)
    state.append_assessment(
        Assessment("target", "H-T01", "approved", "passed", (evidence,)),
        task_id=task,
        rule_version="1",
        stage="coverage",
        explanation="previous eligibility",
    )
    before = state.assessments(task)
    measurement = CoverageMeasurement(
        "target",
        "unavailable",
        "coverage_tool_unavailable",
        None,
        None,
        ("associated",),
        "istanbul",
        evidence,
    )
    persist_measurement(state, task, measurement)
    persist_measurement(state, task, measurement)
    rows = state.measurements(task)
    assert len(rows) == 2
    assert all(row["value"] is None and row["availability"] == "unavailable" for row in rows)
    assert state.assessments(task) == before
    available = CoverageMeasurement(
        "target",
        "available",
        "measured",
        CoverageCounter(0, 4),
        CoverageCounter(0, 0),
        ("associated",),
        "istanbul",
        evidence,
    )
    persist_measurement(state, task, available)
    assert [(row["availability"], row["value"]) for row in state.measurements(task)][-2:] == [
        ("available", 0.0),
        ("not_applicable", None),
    ]
    state.close()
    reopened = StateStore(tmp_path / "state.sqlite", artifacts=artifacts)
    assert reopened.measurements(task) == rows + reopened.measurements(task)[2:]
    reopened.close()


def test_forged_association_is_not_a_trace_proof(tmp_path):
    from robo_minerador.state.artifacts import ArtifactStore
    from robo_minerador.validation.cases import CaseValidation
    from robo_minerador.validation.coverage import verify_association

    artifacts = ArtifactStore(tmp_path / "cas")
    evidence = artifacts.put(b'{"status":"evaluated","case_ids":["forged"]}')
    forged = CaseValidation("evaluated", ("forged",), "target", "approved", evidence)
    with pytest.raises(ValueError, match="association provenance"):
        verify_association(forged, artifacts)
