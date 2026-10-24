"""State contracts against real, reopenable SQLite databases and artifact files."""

import sqlite3
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from threading import Event

import pytest

from robo_minerador.domain import Assessment
from robo_minerador.state.artifacts import ArtifactIntegrityError, ArtifactStore
from robo_minerador.state.database import Database, apply_migration
from robo_minerador.state.store import InvalidTransition, StateStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[StateStore]:
    with StateStore(
        tmp_path / "state.sqlite", artifacts=ArtifactStore(tmp_path / "artifacts")
    ) as state:
        yield state


def test_reopen_preserves_decisions_config_and_events(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite"
    artifacts = ArtifactStore(tmp_path / "artifacts")
    with StateStore(path, artifacts=artifacts) as state:
        run = state.create_run("config-a", config_json='{"limit": 2}', meta={"label": "pilot"})
        task = state.start_task(run, "repo-1", "validation")
        digest = artifacts.put(b"rejection evidence\r\n")
        state.register_artifact(digest)
        assessment_id = state.append_assessment(
            Assessment("repo-1", "license", "rejected", "missing_license", (digest,)),
            task_id=task,
            rule_version="1",
            stage="validation",
            explanation="No compatible license",
        )
        state.finish_task(task, "rejected", evidence_ids=(digest,))
        events = state.events(run)
    with StateStore(path, artifacts=artifacts) as state:
        assert state.get_run(run).config_json == '{"limit": 2}'
        assert state.get_run(run).meta == {"label": "pilot"}
        assert state.get_task(task).outcome == "rejected"
        decision = state.assessments(task)[0]
        assert decision.id == assessment_id
        assert decision.assessment.status == "rejected"
        assert decision.assessment.evidence_ids == (digest,)
        assert decision.observed_at.utcoffset() == timedelta(0)
        assert state.events(run) == events
        assert all(event.observed_at.utcoffset() == timedelta(0) for event in events)
        assert state.pending_tasks(run) == []
        assert artifacts.get(digest) == b"rejection evidence\r\n"


@pytest.mark.parametrize("outcome", ["completed", "rejected", "unsupported"])
def test_terminal_tasks_cannot_reopen(store: StateStore, outcome: str) -> None:
    run = store.create_run("config")
    task = store.start_task(run, "repo-1", "validation")
    store.finish_task(task, outcome)  # type: ignore[arg-type]
    with pytest.raises(InvalidTransition):
        store.start_task(run, "repo-1", "validation")
    with pytest.raises(InvalidTransition):
        store.finish_task(task, "completed")
    assert store.pending_tasks(run) == []


def test_running_task_requires_explicit_interruption_before_resume(store: StateStore) -> None:
    run = store.create_run("config")
    task = store.start_task(run, "repo-1", "validation")
    with pytest.raises(InvalidTransition):
        store.start_task(run, "repo-1", "validation")
    assert store.pending_tasks(run) == []
    store.mark_interrupted(task, explanation="Worker crashed")
    assert [pending.id for pending in store.pending_tasks(run)] == [task]
    with pytest.raises(InvalidTransition):
        store.finish_task(task, "completed")
    assert store.start_task(run, "repo-1", "validation") == task
    assert store.get_task(task).attempt == 2
    store.finish_task(task, "completed")
    changes = [event for event in store.events(run) if event.task_id == task]
    assert [event.kind for event in changes] == [
        "task_started",
        "task_interrupted",
        "task_resumed",
        "task_completed",
    ]
    assert [event.attempt for event in changes] == [1, 1, 2, 2]
    assert changes[1].payload["explanation"] == "Worker crashed"


def test_outer_transaction_rolls_back_nested_operations(store: StateStore) -> None:
    run = store.create_run("config")
    before = store.events(run)
    with pytest.raises(RuntimeError, match="crash"):
        with store.transaction():
            task = store.start_task(run, "repo-1", "validation")
            store.finish_task(task, "rejected")
            raise RuntimeError("crash")
    assert store.events(run) == before
    task = store.start_task(run, "repo-1", "validation")
    assert store.get_task(task).attempt == 1


def test_crash_after_artifact_registration_does_not_lose_evidence(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite"
    artifacts = ArtifactStore(tmp_path / "artifacts")
    digest = artifacts.put(b"durable evidence")
    with StateStore(path, artifacts=artifacts) as state:
        run = state.create_run("config")
        task = state.start_task(run, "repo-1", "validation")
        state.register_artifact(digest)
        with pytest.raises(RuntimeError):
            with state.transaction():
                state.finish_task(task, "completed", evidence_ids=(digest,))
                raise RuntimeError("crash before commit")
    with StateStore(path, artifacts=artifacts) as state:
        assert state.get_task(task).outcome == "running"
        assert not any(event.kind == "task_completed" for event in state.events(run))
        state.mark_interrupted(task, explanation="Process lost")
        assert state.start_task(run, "repo-1", "validation") == task
        state.finish_task(task, "completed", evidence_ids=(digest,))
        assert state.task_evidence(task) == (digest,)
        assert artifacts.get(digest) == b"durable evidence"


def test_missing_evidence_rolls_back_finish_and_assessment(store: StateStore) -> None:
    run = store.create_run("config")
    task = store.start_task(run, "repo-1", "validation")
    before = store.events(run)
    with pytest.raises(sqlite3.IntegrityError):
        store.finish_task(task, "completed", evidence_ids=("a" * 64,))
    with pytest.raises(sqlite3.IntegrityError):
        store.append_assessment(
            Assessment("repo-1", "license", "approved", "ok", ("a" * 64,)),
            task_id=task,
            rule_version="1",
            stage="validation",
            explanation="ok",
        )
    assert store.get_task(task).outcome == "running"
    assert store.assessments(task) == []
    assert store.events(run) == before


@pytest.mark.parametrize("via_assessment", [False, True], ids=["direct", "assessment"])
@pytest.mark.parametrize(
    "damage,expected_error",
    [("missing", FileNotFoundError), ("corrupt", ArtifactIntegrityError)],
)
def test_finish_rechecks_published_evidence_without_partial_changes(
    tmp_path: Path,
    via_assessment: bool,
    damage: str,
    expected_error: type[Exception],
) -> None:
    path = tmp_path / "state.sqlite"
    artifacts = ArtifactStore(tmp_path / "artifacts")
    content = b"original published evidence"
    with StateStore(path, artifacts=artifacts) as state:
        run = state.create_run("config")
        task = state.start_task(run, "repo-1", "validation")
        digest = artifacts.put(content)
        healthy = artifacts.put(b"healthy evidence linked before the failing check")
        state.register_artifact(digest)
        state.register_artifact(healthy)
        state.append_assessment(
            Assessment("repo-1", "license", "approved", "ok", (digest,) if via_assessment else ()),
            task_id=task,
            rule_version="1",
            stage="validation",
            explanation="Decision recorded before evidence damage",
        )
        before_task = state.get_task(task)
        before_events = state.events(run)
        before_assessments = state.assessments(task)
        before_evidence = state.task_evidence(task)

        artifact_path = artifacts.root / digest
        if damage == "missing":
            artifact_path.unlink()
        else:
            artifact_path.write_bytes(b"x" * len(content))

        # The healthy reference is inserted first and must be rolled back on failure.
        # An assessment's damaged digest is intentionally omitted from finish_task.
        finish_evidence = (healthy,) if via_assessment else (healthy, digest)
        with pytest.raises(expected_error):
            state.finish_task(task, "completed", evidence_ids=finish_evidence)

        assert state.get_task(task) == before_task
        assert state.get_task(task).outcome == "running"
        assert state.events(run) == before_events
        assert state.assessments(task) == before_assessments
        assert state.task_evidence(task) == before_evidence
        assert (
            state.database.connection.execute(
                "SELECT artifact_id FROM task_artifacts WHERE task_id = ?", (task,)
            ).fetchall()
            == []
        )
        assert artifacts.get(healthy) == b"healthy evidence linked before the failing check"

    with StateStore(path, artifacts=artifacts) as state:
        assert state.get_task(task) == before_task
        assert state.events(run) == before_events
        assert state.assessments(task) == before_assessments
        assert state.task_evidence(task) == before_evidence
        assert (
            state.database.connection.execute(
                "SELECT artifact_id FROM task_artifacts WHERE task_id = ?", (task,)
            ).fetchall()
            == []
        )


def test_registration_requires_published_verified_bytes(tmp_path: Path) -> None:
    artifacts = ArtifactStore(tmp_path / "artifacts")
    with StateStore(tmp_path / "state.sqlite", artifacts=artifacts) as state:
        with pytest.raises(FileNotFoundError):
            state.register_artifact("a" * 64)
        digest = artifacts.put(b"evidence")
        assert state.register_artifact(digest) == digest
        assert state.register_artifact(digest) == digest
        run = state.create_run("config")
        task = state.start_task(run, "repo-1", "validation")
        state.finish_task(task, "completed", evidence_ids=(digest,))
        with pytest.raises(sqlite3.IntegrityError):
            with state.transaction():
                state.database.connection.execute("DELETE FROM artifacts WHERE id = ?", (digest,))
        assert artifacts.get(digest) == b"evidence"


def test_assessment_envelope_validates_identity_and_keeps_not_evaluated(store: StateStore) -> None:
    run = store.create_run("config")
    task = store.start_task(run, "repo-1", "validation")
    decision = Assessment("repo-1", "license", "not_evaluated", "no_data", ())
    observed = datetime(2026, 1, 2, 10, tzinfo=timezone(timedelta(hours=2)))
    store.append_assessment(
        decision,
        task_id=task,
        rule_version="v2",
        stage="validation",
        explanation="Unavailable",
        observed_at=observed,
    )
    envelope = store.assessments(task)[0]
    assert envelope.assessment == decision
    assert envelope.observed_at == observed.astimezone(UTC)
    assert envelope.rule_version == "v2"
    assert envelope.explanation == "Unavailable"
    with pytest.raises(ValueError):
        store.append_assessment(
            decision, task_id=task, rule_version="v2", stage="other", explanation="wrong stage"
        )
    with pytest.raises(ValueError):
        store.append_assessment(
            decision,
            task_id=task,
            rule_version="v2",
            stage="validation",
            explanation="naive",
            observed_at=datetime(2026, 1, 2),
        )


def test_rejection_cannot_be_rewritten_or_followed_by_approval(store: StateStore) -> None:
    run = store.create_run("config")
    task = store.start_task(run, "repo-1", "validation")
    rejected = Assessment("repo-1", "license", "rejected", "invalid", ())
    store.append_assessment(
        rejected, task_id=task, rule_version="1", stage="validation", explanation="Rejected"
    )
    for status in ("approved", "not_evaluated"):
        with pytest.raises(sqlite3.IntegrityError):
            store.append_assessment(
                Assessment("repo-1", "license", status, "new", ()),  # type: ignore[arg-type]
                task_id=task,
                rule_version="2",
                stage="validation",
                explanation="Rewrite",
            )
    assert [item.assessment for item in store.assessments(task)] == [rejected]
    with pytest.raises(sqlite3.IntegrityError):
        with store.transaction():
            store.database.connection.execute("UPDATE assessments SET status = 'approved'")


def test_configuration_is_immutable_and_new_configuration_gets_new_run(store: StateStore) -> None:
    run = store.create_run("config-a", config_json='{"limit":1}')
    another = store.create_run("config-b", config_json='{"limit":2}')
    assert another != run
    with pytest.raises(sqlite3.IntegrityError):
        with store.transaction():
            store.database.connection.execute(
                "UPDATE runs SET config_json = ? WHERE id = ?", ('{"limit":2}', run)
            )
    assert store.get_run(run).config_hash == "config-a"
    with pytest.raises(ValueError):
        store.create_run("bad", config_json="not json")


def test_foreign_keys_and_enum_constraints(store: StateStore) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        store.start_task("missing-run", "repo-1", "validation")
    run = store.create_run("config")
    task = store.start_task(run, "repo-1", "validation")
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        store.finish_task(task, "approved")  # type: ignore[arg-type]
    with pytest.raises(InvalidTransition):
        store.finish_task(task, "running")  # type: ignore[arg-type]
    with pytest.raises(sqlite3.IntegrityError):
        with store.transaction():
            store.database.connection.execute(
                "UPDATE tasks SET outcome = 'bogus' WHERE id = ?", (task,)
            )
    with pytest.raises(sqlite3.IntegrityError):
        store.append_assessment(
            Assessment("repo-1", "license", "bogus", "bad", ()),  # type: ignore[arg-type]
            task_id=task,
            rule_version="1",
            stage="validation",
            explanation="bad enum",
        )
    assert store.get_task(task).outcome == "running"


def test_migration_is_versioned_and_reopening_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite"
    expected = {
        "runs",
        "repositories",
        "tasks",
        "assessments",
        "artifacts",
        "candidates",
        "suite_runs",
        "case_results",
        "measurements",
        "duplicate_groups",
        "events",
    }
    for _ in range(2):
        with Database(path) as database:
            assert database.connection.execute("PRAGMA user_version").fetchone()[0] == 1
            assert database.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            tables = {
                row[0]
                for row in database.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            assert expected <= tables
            assert database.connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_incomplete_migration_rolls_back_schema_and_version(tmp_path: Path) -> None:
    with sqlite3.connect(tmp_path / "broken.sqlite") as connection:
        with pytest.raises(sqlite3.OperationalError):
            apply_migration(connection, "CREATE TABLE partial(id TEXT); INVALID SQL;", version=1)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert (
            connection.execute("SELECT name FROM sqlite_master WHERE name='partial'").fetchall()
            == []
        )


def test_concurrent_initializers_recheck_version_after_acquiring_lock(tmp_path: Path) -> None:
    path = tmp_path / "concurrent.sqlite"
    first_creating_schema = Event()
    second_acquiring_lock = Event()
    release_first = Event()

    def initialize(*, first: bool) -> list[tuple[str, str]]:
        def trace(statement: str) -> None:
            if first and statement.lstrip().startswith("CREATE TABLE runs"):
                first_creating_schema.set()
                release_first.wait(timeout=10)
            elif not first and statement.strip().rstrip(";") == "BEGIN IMMEDIATE":
                second_acquiring_lock.set()

        class ObservedDatabase(Database):
            def _migrate(self) -> None:
                # Observe real SQLite execution; neither connections nor locks are mocked.
                self.connection.set_trace_callback(trace)
                try:
                    super()._migrate()
                finally:
                    self.connection.set_trace_callback(None)

        with ObservedDatabase(path) as database:
            assert database.connection.execute("PRAGMA user_version").fetchone()[0] == 1
            assert database.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert database.connection.execute("PRAGMA foreign_key_check").fetchall() == []
            return [
                (row["name"], row["sql"])
                for row in database.connection.execute(
                    "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
                )
            ]

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(initialize, first=True)
        try:
            assert first_creating_schema.wait(timeout=5), "first initializer did not reach DDL"
            second = executor.submit(initialize, first=False)
            assert second_acquiring_lock.wait(timeout=5), "second initializer did not request lock"
        finally:
            release_first.set()
        first_schema = first.result(timeout=10)
        second_schema = second.result(timeout=10)

    with Database(path) as database:
        final_schema = [
            (row["name"], row["sql"])
            for row in database.connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
            )
        ]
    assert first_schema == second_schema == final_schema
    assert any(name == "tasks_valid_transition" for name, _ in final_schema)


def test_sql_values_are_parameters(store: StateStore) -> None:
    run = store.create_run("x'); DROP TABLE runs; --")
    task = store.start_task(run, "repo'; --", "validation")
    assert store.get_task(task).entity_id == "repo'; --"
    assert store.get_run(run).config_hash == "x'); DROP TABLE runs; --"


def test_failure_writing_final_event_rolls_back_outcome_and_evidence(tmp_path: Path) -> None:
    artifacts = ArtifactStore(tmp_path / "artifacts")
    with StateStore(tmp_path / "state.sqlite", artifacts=artifacts) as state:
        run = state.create_run("config")
        task = state.start_task(run, "repo-1", "validation")
        digest = artifacts.put(b"evidence survives failed transaction")
        state.register_artifact(digest)
        before = state.events(run)
        state.database.connection.execute(
            "CREATE TRIGGER fail_completion BEFORE INSERT ON events "
            "WHEN NEW.kind = 'task_completed' "
            "BEGIN SELECT RAISE(ABORT, 'simulated event failure'); END"
        )
        with pytest.raises(sqlite3.IntegrityError, match="simulated event failure"):
            state.finish_task(task, "completed", evidence_ids=(digest,))
        assert state.get_task(task).outcome == "running"
        assert state.task_evidence(task) == ()
        assert state.events(run) == before
        assert artifacts.get(digest) == b"evidence survives failed transaction"


@pytest.mark.parametrize("table", ["assessment_evidence", "task_artifacts"])
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_evidence_links_cannot_be_rewritten(
    tmp_path: Path,
    table: str,
    operation: str,
) -> None:
    artifacts = ArtifactStore(tmp_path / "artifacts")
    with StateStore(tmp_path / "state.sqlite", artifacts=artifacts) as state:
        run = state.create_run("config")
        task = state.start_task(run, "repo-1", "validation")
        digest = artifacts.put(b"immutable evidence")
        state.register_artifact(digest)
        state.append_assessment(
            Assessment("repo-1", "license", "rejected", "bad", (digest,)),
            task_id=task,
            rule_version="1",
            stage="validation",
            explanation="Rejected",
        )
        state.finish_task(task, "rejected", evidence_ids=(digest,))
        # Identifiers below are fixed test parameters, never application inputs.
        sql = (
            f"UPDATE {table} SET artifact_id = artifact_id"
            if operation == "UPDATE"
            else f"DELETE FROM {table}"
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            with state.transaction():
                state.database.connection.execute(sql)
        assert state.task_evidence(task) == (digest,)


def test_nested_failure_can_be_caught_without_poisoning_outer_transaction(
    store: StateStore,
) -> None:
    run = store.create_run("config")
    with store.transaction():
        task = store.start_task(run, "repo-1", "validation")
        with pytest.raises(sqlite3.IntegrityError):
            store.finish_task(task, "completed", evidence_ids=("a" * 64,))
        store.finish_task(task, "unsupported")
    assert store.get_task(task).outcome == "unsupported"
    assert [event.kind for event in store.events(run)] == [
        "run_created",
        "task_started",
        "task_unsupported",
    ]
