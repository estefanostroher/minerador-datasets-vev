"""Reassess existing real P3 evidence offline; never execute a project or edit raw CAS."""

import json
import os
import shutil
from pathlib import Path

import pytest

from robo_minerador.state.artifacts import ArtifactStore
from robo_minerador.state.store import StateStore
from robo_minerador.validation.service import EligibilityInputs, EligibilityService


@pytest.fixture
def p3_evidence(request, tmp_path):
    root = os.environ.get("ROBO_T14_P3_ROOT")
    if not root:
        pytest.skip("set ROBO_T14_P3_ROOT to the existing real Docker P3 CAS")
    source = Path(root) / f"test_p3_real_originals_three_p{request.param}"
    summary = json.loads((source / "p3-summary.json").read_text(encoding="utf-8"))
    shutil.copy2(source / "state.sqlite", tmp_path / "state.sqlite")
    artifacts = ArtifactStore(source / "cas")
    with StateStore(tmp_path / "state.sqlite", artifacts=artifacts) as state:
        row = state.database.connection.execute("SELECT run_id FROM tasks LIMIT 1").fetchone()
        run = row[0]
        task_ids = {event.task_id for event in state.events(run) if event.task_id}
        legacy_task = next(
            task_id for task_id in task_ids if summary["evidence"] in state.task_evidence(task_id)
        )
        request_model = next(
            model
            for event in state.task_checkpoints(legacy_task)
            if (model := json.loads(artifacts.get(str(event.payload["digest"]))))["kind"]
            == "eligibility_request"
        )
        inputs = EligibilityInputs.model_validate_json(json.dumps(request_model["inputs"]))
        args = dict(
            run_id=run,
            extraction_digest=inputs.extraction_digest,
            candidate_id=inputs.candidate_id,
            repository=inputs.repository,
            associations=inputs.associations,
            coverage=inputs.coverage,
        )
        yield (
            state,
            artifacts,
            EligibilityService(state, artifacts),
            args,
            summary,
            source,
            legacy_task,
        )


@pytest.mark.parametrize("p3_evidence", range(6), indirect=True)
def test_current_criteria_are_rederived_from_real_p3_without_running_suites(
    p3_evidence, monkeypatch
):
    state, artifacts, api, args, old_summary, source, legacy_task = p3_evidence
    old_assessments = state.assessments(legacy_task)
    old_suites = state.suite_runs(args["repository"].task_id)
    if (
        old_summary["framework"] == "jest"
        and old_summary["patch"] == "29.7.0"
        and old_summary["accepted"]
    ):
        with pytest.raises(ValueError, match="immutable"):
            api.validate(**{**args, "associations": ()})
    occurrence = api.evaluate(**args)
    decisions = {a.criterion_id: a for a in occurrence.assessments}
    assert decisions["H-F02"].reason == "direct_executed_case_in_each_run"
    assert decisions["H-F04"].reason == "behavioral_description"
    assert decisions["H-F05"].status == (
        "approved" if old_summary["missing_direct_ordinal"] is None else "not_evaluated"
    )
    assert decisions["H-F01"].status == (
        "approved" if old_summary["missing_direct_ordinal"] is None else "rejected"
    )
    expected_counts = [len(ids) for ids in old_summary["case_ids_per_run"]]
    assert [len(ids) for ids in occurrence.case_ids_per_run] == expected_counts
    assert occurrence.accepted is old_summary["accepted"]
    if occurrence.accepted:
        assert all(a.status == "approved" for a in occurrence.assessments)
    first_positive = (
        old_summary["framework"] == "jest"
        and old_summary["patch"] == "29.7.0"
        and occurrence.accepted
    )
    if first_positive:

        class CoordinatorCrash(BaseException):
            pass

        with monkeypatch.context() as crash:

            def fail_publication(*_args, **_kwargs):
                raise CoordinatorCrash()

            crash.setattr(state, "finish_task", fail_publication)
            with pytest.raises(CoordinatorCrash):
                api.validate(**args)
        database_path = Path(
            state.database.connection.execute("PRAGMA database_list").fetchone()[2]
        )
        with StateStore(database_path, artifacts=artifacts) as reopened:
            recovered = EligibilityService(reopened, artifacts)
            with pytest.raises(ValueError, match="immutable"):
                recovered.validate(**{**args, "associations": ()})
            result = recovered.validate(**args)
            assert recovered.load(args["run_id"], result.digest) == result
            assert len(reopened.assessments(result.task_id)) == 12
    else:
        result = api.validate(**args)
    assert result.occurrence.rule_version == "T14-v2"
    assert result.occurrence.schema_version == "1.1"
    assert api.load(args["run_id"], result.digest) == result
    assert state.assessments(legacy_task) == old_assessments
    assert state.suite_runs(args["repository"].task_id) == old_suites
    assert result.task_id != legacy_task
    with pytest.raises(ValueError, match="unsupported.*methodology"):
        api.load(args["run_id"], old_summary["evidence"])
    with pytest.raises(ValueError, match="immutable"):
        api.validate(**{**args, "associations": ()})
    if first_positive:
        from robo_minerador.extraction.service import load_extraction

        extracted = load_extraction(artifacts, args["extraction_digest"])
        undocumented = next(
            c for c in extracted.candidates if c.target.qualified_name == "undocumented"
        )
        missing_doc = api.evaluate(
            **{**args, "candidate_id": undocumented.id, "associations": (), "coverage": None}
        )
        assert not missing_doc.accepted
        doc_decisions = {a.criterion_id: a for a in missing_doc.assessments}
        assert doc_decisions["H-F04"].status == "rejected"
        assert doc_decisions["H-F02"].status == "not_evaluated"
        assert doc_decisions["H-F01"].status != "approved"
        missing_trace = api.evaluate(**{**args, "associations": ()})
        pending = {a.criterion_id: a for a in missing_trace.assessments}
        assert pending["H-F04"].status == "approved"
        assert pending["H-F02"].status == "not_evaluated"
        assert pending["H-F01"].status == "not_evaluated"
    summary = {
        "validation_state": state.database.connection.execute("PRAGMA database_list").fetchone()[2],
        **old_summary,
        "rule_version": result.occurrence.rule_version,
        "legacy_evidence": old_summary["evidence"],
        "evidence": result.digest,
        "original_executions": occurrence.original_executions,
        "association_evidence": occurrence.association_evidence,
        "assessments": [a.model_dump(mode="json") for a in occurrence.assessments],
        "case_ids_per_run": occurrence.case_ids_per_run,
        "unresolved_case_ids_per_run": occurrence.unresolved_case_ids_per_run,
        "accepted": occurrence.accepted,
        "reassessment": "offline-CAS-no-suite-execution",
    }
    (source / "p3-summary-v2.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
