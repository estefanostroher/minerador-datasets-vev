"""T13 stopping rule and durable recovery, without executing projects on the host."""

import importlib
import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import pytest

from robo_minerador.config import ExecutionConfig
from robo_minerador.environments.catalog import load_catalog
from robo_minerador.environments.plan import plan_environment
from robo_minerador.execution.results import SuiteResult
from robo_minerador.screening.commands import plan_commands
from robo_minerador.state.artifacts import ArtifactStore
from robo_minerador.state.store import StateStore


def repetitions():
    assert importlib.util.find_spec("robo_minerador.execution.repetitions"), "T13 is missing"
    return importlib.import_module("robo_minerador.execution.repetitions")


@pytest.mark.parametrize(
    "outcomes,expected",
    [
        (("passed", "passed", "passed"), (1, 2, 3)),
        (("passed", "failed", "passed"), (1, 2)),
        (("passed", "interrupted", "passed"), (1, 2)),
        (("failed", "passed", "passed"), (1,)),
    ],
)
def test_run_three_stops_at_first_nonpass(outcomes, expected):
    calls = []

    def execute(ordinal):
        calls.append(ordinal)
        return SuiteResult(ordinal, outcomes[ordinal - 1], 0, "a" * 64)

    results = repetitions().run_three(execute)
    assert tuple(calls) == expected
    assert tuple(result.ordinal for result in results) == expected


def test_crash_does_not_erase_results_persisted_by_caller():
    persisted = []

    def execute(ordinal):
        if ordinal == 2:
            raise RuntimeError("coordinator crash")
        result = SuiteResult(ordinal, "passed", 0, "a" * 64)
        persisted.append(result)
        return result

    with pytest.raises(RuntimeError, match="coordinator crash"):
        repetitions().run_three(execute)
    assert len(persisted) == 1


@pytest.fixture
def durable(tmp_path):
    artifacts = ArtifactStore(tmp_path / "cas")
    state = StateStore(tmp_path / "state.sqlite", artifacts=artifacts)
    run = state.create_run("T13")
    files = {
        "package.json": b'{"scripts":{"test":"jest"},"devDependencies":{"jest":"29.7.0"}}',
        "package-lock.json": b'{"lockfileVersion":3}',
    }
    commands = plan_commands(files)
    plan = plan_environment(
        files, commands, load_catalog(Path(__file__).resolve().parents[2]), ExecutionConfig()
    ).plan
    assert plan is not None
    yield state, artifacts, run, commands, plan
    state.close()


def test_checkpoint_survives_reopen_and_verifies_cas(durable):
    state, artifacts, run, _, _ = durable
    assert hasattr(state, "record_task_checkpoint"), "T13 checkpoint API missing"
    task = state.start_task(run, "repo", "repository_validation")
    digest = state.register_artifact(artifacts.put(b'{"artifacts":[]}'))
    state.record_task_checkpoint(task, "execution_result", digest)
    with StateStore(artifacts.root.parent / "state.sqlite", artifacts=artifacts) as reopened:
        assert reopened.task_checkpoints(task)[0].payload["digest"] == digest
    (artifacts.root / digest).write_bytes(b"corrupt")
    with pytest.raises(ValueError):
        state.task_checkpoints(task)


def test_persisted_failure_finishes_offline_after_coordinator_crash(durable):
    module = repetitions()
    state, artifacts, run, commands, plan = durable
    task = state.start_task(run, "repo", "repository_validation")
    request = module.validation_identity(plan, commands, b"archive")
    model = {
        "kind": "result",
        "request_hash": request,
        "phase": "run",
        "ordinal": 2,
        "outcome": "failed",
        "exit_code": 7,
        "artifacts": [],
        "scope_proven": False,
    }
    digest = state.register_artifact(artifacts.put(json.dumps(model).encode()))
    state.record_task_checkpoint(task, "execution_result", digest)
    # None is deliberately not an executor fake: recovery must finish entirely offline.
    service = module.RepositoryValidation(None, state, artifacts)
    result = service.validate(run, "repo", plan, commands, b"archive")
    assert result.outcome == "rejected"
    assert service.validate(run, "repo", plan, commands, b"archive") == result
    assert [a.assessment.status for a in state.assessments(task)] == ["rejected", "approved"]
    with pytest.raises(ValueError, match="immutable"):
        service.validate(run, "repo", replace(plan, node_version="24.0.0"), commands, b"archive")
    assert state.get_task(task).outcome == "rejected"


def test_three_durable_passes_with_unknown_scope_finish_offline(durable):
    module = repetitions()
    state, artifacts, run, commands, plan = durable
    task = state.start_task(run, "repo", "repository_validation")
    request = module.validation_identity(plan, commands, b"archive")
    for ordinal in range(1, 4):
        model = {
            "kind": "result",
            "request_hash": request,
            "phase": "run",
            "ordinal": ordinal,
            "outcome": "passed",
            "exit_code": 0,
            "artifacts": [],
            "scope_proven": False,
        }
        digest = state.register_artifact(artifacts.put(json.dumps(model).encode()))
        state.record_task_checkpoint(task, "execution_result", digest)
    result = module.RepositoryValidation(None, state, artifacts).validate(
        run, "repo", plan, commands, b"archive"
    )
    assert result.outcome == "unsupported"


def test_interrupted_passes_cannot_be_combined(durable):
    module = repetitions()
    state, artifacts, run, commands, plan = durable
    task = state.start_task(run, "repo", "repository_validation")
    request = module.validation_identity(plan, commands, b"archive")
    for attempt in range(2):
        for ordinal, outcome in [(1, "passed"), (2, "interrupted")]:
            model = {
                "kind": "result",
                "request_hash": request,
                "phase": "run",
                "ordinal": ordinal,
                "outcome": outcome,
                "exit_code": None,
                "artifacts": [],
                "scope_proven": False,
            }
            digest = state.register_artifact(artifacts.put(json.dumps(model).encode()))
            state.record_task_checkpoint(task, "execution_result", digest)
        state.mark_interrupted(task, explanation="daemon interrupted")
        if attempt == 0:
            assert state.start_task(run, "repo", "repository_validation") == task
    service = module.RepositoryValidation(None, state, artifacts)
    assert service.recover(task, request) is None
    assert len(state.task_checkpoints(task)) == 4


def test_fake_pass_flags_without_framework_proof_never_approve(durable):
    module = repetitions()
    state, artifacts, run, commands, plan = durable
    task = state.start_task(run, "repo", "repository_validation")
    request = module.validation_identity(plan, commands, b"archive")
    for ordinal in range(1, 4):
        model = {
            "kind": "result",
            "request_hash": request,
            "phase": "run",
            "ordinal": ordinal,
            "outcome": "passed",
            "exit_code": 0,
            "artifacts": [],
            "scope_proven": True,
        }
        digest = state.register_artifact(artifacts.put(json.dumps(model).encode()))
        state.record_task_checkpoint(task, "execution_result", digest)
    result = module.RepositoryValidation(None, state, artifacts).validate(
        run, "repo", plan, commands, b"archive"
    )
    assert result.outcome == "unsupported"


def test_replay_verifies_nested_evidence_and_retains_factual_rejection(durable):
    module = repetitions()
    state, artifacts, run, commands, plan = durable
    task = state.start_task(run, "repo", "repository_validation")
    request = module.validation_identity(plan, commands, b"archive")
    log = state.register_artifact(artifacts.put(b"project failure evidence"))
    model = {
        "kind": "result",
        "request_hash": request,
        "phase": "install",
        "ordinal": 0,
        "outcome": "failed",
        "exit_code": 7,
        "artifacts": [log],
        "scope_proven": False,
    }
    digest = state.register_artifact(artifacts.put(json.dumps(model).encode()))
    state.record_task_checkpoint(task, "execution_result", digest)
    service = module.RepositoryValidation(None, state, artifacts)
    assert service.validate(run, "repo", plan, commands, b"archive").outcome == "rejected"
    (artifacts.root / log).write_bytes(b"corrupt")
    with pytest.raises(ValueError):
        service.validate(run, "repo", plan, commands, b"archive")
    assert state.get_task(task).outcome == "rejected"
    assert state.assessments(task)[0].assessment.status == "rejected"
