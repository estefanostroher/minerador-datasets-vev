"""Typed collection state and append-only decisions, with explicit crash recovery.

Publish bytes using ArtifactStore.put, then register_artifact(digest), then append
decisions/finish tasks referencing that digest. Registration verifies published
bytes; transitions verify them again. Missing registry references raise
sqlite3.IntegrityError; missing/corrupt files propagate ArtifactStore errors.
Every operation is atomic and can participate in a larger transaction(). File
publication is deliberately outside SQLite: rollback may leave an unreferenced
object, never delete evidence. There is no evidence deletion API.
"""

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Literal, cast
from uuid import uuid4

from robo_minerador.domain import Assessment
from robo_minerador.identity import stable_digest
from robo_minerador.state.artifacts import ArtifactStore
from robo_minerador.state.database import Database

TaskOutcome = Literal["running", "completed", "rejected", "unsupported", "interrupted"]


class InvalidTransition(ValueError):
    """A task cannot make the requested transition from its persisted state."""


@dataclass(frozen=True)
class Run:
    id: str
    config_hash: str
    config_json: str | None
    meta: dict[str, object]
    created_at: datetime


@dataclass(frozen=True)
class Task:
    id: str
    run_id: str
    entity_id: str
    stage: str
    outcome: TaskOutcome
    attempt: int


@dataclass(frozen=True)
class StoredAssessment:
    id: str
    task_id: str
    assessment: Assessment
    rule_version: str
    stage: str
    explanation: str
    observed_at: datetime


@dataclass(frozen=True)
class Event:
    sequence: int
    run_id: str
    task_id: str | None
    attempt: int | None
    kind: str
    observed_at: datetime
    payload: dict[str, object]


@dataclass(frozen=True)
class StoredDuplicateGroup:
    id: str
    run_id: str
    method: str
    fingerprint: str


@dataclass(frozen=True)
class DeduplicationCandidate:
    id: str
    run_id: str
    repository_id: str
    commit_sha: str
    path: str
    start_offset: int
    end_offset: int
    symbol_name: str | None
    source_artifact_id: str | None
    duplicate_group_id: str | None


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _utc(value: datetime | None = None) -> str:
    observed = value if value is not None else datetime.now(UTC)
    if observed.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    return observed.astimezone(UTC).isoformat()


def _task(row: sqlite3.Row) -> Task:
    return Task(
        row["id"], row["run_id"], row["entity_id"], row["stage"], row["outcome"], row["attempt"]
    )


class StateStore:
    """State API with one owned connection and an optional verified artifact store."""

    def __init__(self, path: Path, *, artifacts: ArtifactStore | None = None) -> None:
        self.database = Database(path)
        self._artifacts = artifacts

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Group state operations atomically; nested failures use savepoints."""
        with self.database.transaction():
            yield

    def create_run(
        self,
        config_hash: str,
        *,
        config_json: str | None = None,
        meta: Mapping[str, object] | None = None,
    ) -> str:
        """Freeze a configuration snapshot. Changed configurations require a new run."""
        if config_json is not None and not isinstance(json.loads(config_json), dict):
            raise ValueError("config_json must contain a JSON object")
        run_id = uuid4().hex
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO runs(id, config_hash, config_json, meta_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (run_id, config_hash, config_json, _json(dict(meta or {})), _utc()),
            )
            self._event(run_id, "run_created", payload={"config_hash": config_hash})
        return run_id

    def get_run(self, run_id: str) -> Run:
        row = self.database.connection.execute(
            "SELECT * FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return Run(
            row["id"],
            row["config_hash"],
            row["config_json"],
            json.loads(row["meta_json"]),
            datetime.fromisoformat(row["created_at"]),
        )

    def start_task(self, run_id: str, entity_id: str, stage: str) -> str:
        """Start once, or explicitly resume an interrupted task with the same ID."""
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE run_id = ? AND entity_id = ? AND stage = ?",
                (run_id, entity_id, stage),
            ).fetchone()
            if row is not None:
                task = _task(row)
                if task.outcome != "interrupted":
                    raise InvalidTransition(f"cannot start {task.outcome} task {task.id}")
                connection.execute(
                    "UPDATE tasks SET outcome = 'running', attempt = attempt + 1, updated_at = ? "
                    "WHERE id = ?",
                    (_utc(), task.id),
                )
                self._event(run_id, "task_resumed", task_id=task.id, attempt=task.attempt + 1)
                return task.id
            task_id = uuid4().hex
            now = _utc()
            connection.execute(
                "INSERT INTO tasks(id, run_id, entity_id, stage, outcome, attempt, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, 'running', 1, ?, ?)",
                (task_id, run_id, entity_id, stage, now, now),
            )
            self._event(run_id, "task_started", task_id=task_id, attempt=1)
            return task_id

    def get_task(self, task_id: str) -> Task:
        row = self.database.connection.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise KeyError(task_id)
        return _task(row)

    def pending_tasks(self, run_id: str) -> list[Task]:
        """Only interrupted tasks are resumable; running tasks need explicit recovery."""
        rows = self.database.connection.execute(
            "SELECT * FROM tasks WHERE run_id = ? AND outcome = 'interrupted' ORDER BY rowid",
            (run_id,),
        )
        return [_task(row) for row in rows]

    def finish_task(
        self,
        task_id: str,
        outcome: TaskOutcome,
        *,
        evidence_ids: tuple[str, ...] = (),
        explanation: str = "",
    ) -> None:
        """Atomically link registered evidence, transition a running task and log it."""
        if outcome == "running":
            raise InvalidTransition("finish_task cannot reopen a task")
        if outcome not in ("completed", "rejected", "unsupported", "interrupted"):
            raise ValueError(f"invalid task outcome: {outcome}")
        with self.database.transaction() as connection:
            task = self.get_task(task_id)
            if task.outcome != "running":
                raise InvalidTransition(f"cannot finish {task.outcome} task {task_id}")
            for digest in evidence_ids:
                self._verify_artifact(digest)
                connection.execute(
                    "INSERT OR IGNORE INTO task_artifacts(task_id, artifact_id) VALUES (?, ?)",
                    (task_id, digest),
                )
            for digest in self.task_evidence(task_id):
                self._verify_artifact(digest)
            connection.execute(
                "UPDATE tasks SET outcome = ?, updated_at = ? WHERE id = ?",
                (outcome, _utc(), task_id),
            )
            self._event(
                task.run_id,
                f"task_{outcome}",
                task_id=task_id,
                attempt=task.attempt,
                payload={"explanation": explanation, "evidence_ids": evidence_ids},
            )

    def mark_interrupted(self, task_id: str, *, explanation: str) -> None:
        """Record explicit crash recovery; a later start_task performs the resume."""
        self.finish_task(task_id, "interrupted", explanation=explanation)

    def register_artifact(self, digest: str) -> str:
        """Verify existing bytes and register their SHA-256, absolute path and size."""
        artifacts = self._artifact_store()
        content = artifacts.get(digest)
        path = str((artifacts.root / digest).resolve())
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO artifacts(id, path, byte_size, created_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(id) DO NOTHING",
                (digest, path, len(content), _utc()),
            )
            row = connection.execute(
                "SELECT path FROM artifacts WHERE id = ?", (digest,)
            ).fetchone()
            if row["path"] != path:
                raise ValueError("artifact already registered at a different path")
        return digest

    def _artifact_store(self) -> ArtifactStore:
        if self._artifacts is None:
            raise ValueError("configure ArtifactStore to register or reference evidence")
        return self._artifacts

    def _verify_artifact(self, digest: str) -> None:
        row = self.database.connection.execute(
            "SELECT path, byte_size FROM artifacts WHERE id = ?", (digest,)
        ).fetchone()
        if row is None:
            raise sqlite3.IntegrityError(f"unregistered artifact: {digest}")
        artifacts = self._artifact_store()
        content = artifacts.get(digest)
        if row["path"] != str((artifacts.root / digest).resolve()) or row["byte_size"] != len(
            content
        ):
            raise ValueError("artifact registry does not match the configured ArtifactStore")

    def task_evidence(self, task_id: str) -> tuple[str, ...]:
        rows = self.database.connection.execute(
            "SELECT artifact_id FROM task_artifacts WHERE task_id = ? "
            "UNION SELECT e.artifact_id FROM assessment_evidence e "
            "JOIN assessments a ON a.id = e.assessment_id WHERE a.task_id = ? ORDER BY artifact_id",
            (task_id, task_id),
        )
        return tuple(row[0] for row in rows)

    def append_assessment(
        self,
        assessment: Assessment,
        *,
        task_id: str,
        rule_version: str,
        stage: str,
        explanation: str,
        observed_at: datetime | None = None,
    ) -> str:
        """Append a domain decision and its envelope without replacing prior decisions."""
        timestamp = _utc(observed_at)
        assessment_id = uuid4().hex
        with self.database.transaction() as connection:
            task = self.get_task(task_id)
            if task.outcome != "running":
                raise InvalidTransition("assessments require a running task")
            if assessment.entity_id != task.entity_id or stage != task.stage:
                raise ValueError("assessment entity and stage must match the task")
            connection.execute(
                "INSERT INTO assessments(id, task_id, run_id, entity_id, criterion_id, status, "
                "reason_code, rule_version, stage, explanation, observed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    assessment_id,
                    task_id,
                    task.run_id,
                    assessment.entity_id,
                    assessment.criterion_id,
                    assessment.status,
                    assessment.reason_code,
                    rule_version,
                    stage,
                    explanation,
                    timestamp,
                ),
            )
            for ordinal, digest in enumerate(assessment.evidence_ids):
                self._verify_artifact(digest)
                connection.execute(
                    "INSERT INTO assessment_evidence(assessment_id, artifact_id, ordinal) "
                    "VALUES (?, ?, ?)",
                    (assessment_id, digest, ordinal),
                )
            self._event(
                task.run_id,
                "assessment_appended",
                task_id=task_id,
                attempt=task.attempt,
                payload={"assessment_id": assessment_id, "status": assessment.status},
            )
        return assessment_id

    def assessments(self, task_id: str) -> list[StoredAssessment]:
        rows = self.database.connection.execute(
            "SELECT * FROM assessments WHERE task_id = ? ORDER BY rowid", (task_id,)
        ).fetchall()
        result = []
        for row in rows:
            evidence = self.database.connection.execute(
                "SELECT artifact_id FROM assessment_evidence "
                "WHERE assessment_id = ? ORDER BY ordinal",
                (row["id"],),
            )
            decision = Assessment(
                row["entity_id"],
                row["criterion_id"],
                row["status"],
                row["reason_code"],
                tuple(item[0] for item in evidence),
            )
            result.append(
                StoredAssessment(
                    row["id"],
                    task_id,
                    decision,
                    row["rule_version"],
                    row["stage"],
                    row["explanation"],
                    datetime.fromisoformat(row["observed_at"]),
                )
            )
        return result

    def _event(
        self,
        run_id: str,
        kind: str,
        *,
        task_id: str | None = None,
        attempt: int | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> None:
        self.database.connection.execute(
            "INSERT INTO events(run_id, task_id, attempt, kind, observed_at, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, task_id, attempt, kind, _utc(), _json(dict(payload or {}))),
        )

    def events(self, run_id: str) -> list[Event]:
        rows = self.database.connection.execute(
            "SELECT * FROM events WHERE run_id = ? ORDER BY sequence", (run_id,)
        )
        return [
            Event(
                row["sequence"],
                row["run_id"],
                row["task_id"],
                row["attempt"],
                row["kind"],
                datetime.fromisoformat(row["observed_at"]),
                cast(dict[str, object], json.loads(row["payload_json"])),
            )
            for row in rows
        ]

    def close(self) -> None:
        self.database.close()

    def record_task_checkpoint(self, task_id: str, kind: str, digest: str) -> None:
        """Append a CAS-verified durable checkpoint, linked to the current attempt."""
        with self.transaction():
            task = self.get_task(task_id)
            if task.outcome != "running":
                raise InvalidTransition("checkpoints require a running task")
            self._verify_artifact(digest)
            self.database.connection.execute(
                "INSERT OR IGNORE INTO task_artifacts(task_id, artifact_id) VALUES (?, ?)",
                (task_id, digest),
            )
            self._event(
                task.run_id,
                "task_checkpoint",
                task_id=task_id,
                attempt=task.attempt,
                payload={"kind": kind, "digest": digest},
            )

    def task_checkpoints(self, task_id: str) -> list[Event]:
        task = self.get_task(task_id)
        checkpoints = [
            event
            for event in self.events(task.run_id)
            if event.task_id == task_id and event.kind == "task_checkpoint"
        ]
        for event in checkpoints:
            self._verify_artifact(str(event.payload["digest"]))
        return checkpoints

    def record_suite_run(
        self,
        task_id: str,
        *,
        command: tuple[str, ...],
        environment_hash: str,
        outcome: TaskOutcome,
        exit_code: int | None,
        started_at: datetime,
        finished_at: datetime,
        stdout: str | None = None,
        stderr: str | None = None,
    ) -> str:
        """Append one repository-level execution; checkpoint linkage can share its transaction."""
        identifier = uuid4().hex
        with self.transaction():
            task = self.get_task(task_id)
            if task.outcome != "running":
                raise InvalidTransition("suite runs require a running task")
            for digest in (stdout, stderr):
                if digest is not None:
                    self._verify_artifact(digest)
            self.database.connection.execute(
                "INSERT INTO suite_runs(id, run_id, task_id, command_json, environment_hash, "
                "outcome, exit_code, started_at, finished_at, stdout_artifact_id, "
                "stderr_artifact_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    identifier,
                    task.run_id,
                    task_id,
                    _json(command),
                    environment_hash,
                    outcome,
                    exit_code,
                    _utc(started_at),
                    _utc(finished_at),
                    stdout,
                    stderr,
                ),
            )
        return identifier

    def suite_runs(self, task_id: str) -> list[dict[str, object]]:
        return [
            dict(row)
            for row in self.database.connection.execute(
                "SELECT * FROM suite_runs WHERE task_id = ? ORDER BY rowid", (task_id,)
            )
        ]

    def upsert_repository(
        self, run_id: str, external_id: int, full_name: str, commit_sha: str | None
    ) -> str:
        """Keep run-scoped external identity across renames and name collisions."""
        if type(external_id) is not int or external_id < 1 or not full_name.strip():
            raise ValueError("repository requires a positive external ID and full name")
        repository_id = stable_digest({"run_id": run_id, "repo_id": external_id})
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO repositories(id, run_id, external_id, full_name, commit_sha) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(run_id, external_id) DO UPDATE SET "
                "full_name = excluded.full_name, "
                "commit_sha = COALESCE(repositories.commit_sha, excluded.commit_sha)",
                (repository_id, run_id, str(external_id), full_name, commit_sha),
            )
        return repository_id

    def record_measurement(
        self,
        task_id: str,
        *,
        name: str,
        value: float | None,
        availability: Literal["available", "unavailable", "not_applicable"],
        method: str,
        reason_code: str,
        evidence_id: str,
    ) -> str:
        """Idempotently persist a descriptive metric without appending assessments."""
        identifier = stable_digest(
            {
                "task": task_id,
                "name": name,
                "value": value,
                "availability": availability,
                "method": method,
                "reason": reason_code,
                "evidence": evidence_id,
            }
        )
        with self.transaction():
            task = self.get_task(task_id)
            self._verify_artifact(evidence_id)
            self.database.connection.execute(
                "INSERT OR IGNORE INTO measurements(id, run_id, task_id, name, value, "
                "availability, method, reason_code, observed_at, evidence_artifact_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    identifier,
                    task.run_id,
                    task_id,
                    name,
                    value,
                    availability,
                    method,
                    reason_code,
                    _utc(),
                    evidence_id,
                ),
            )
        return identifier

    def measurements(self, task_id: str) -> list[dict[str, object]]:
        rows = self.database.connection.execute(
            "SELECT * FROM measurements WHERE task_id = ? ORDER BY rowid", (task_id,)
        ).fetchall()
        for row in rows:
            self._verify_artifact(row["evidence_artifact_id"])
        return [dict(row) for row in rows]

    def repositories(self, run_id: str) -> list[dict[str, object]]:
        """Return run-scoped repository identities, independent of mutable names."""
        return [
            dict(row)
            for row in self.database.connection.execute(
                "SELECT * FROM repositories WHERE run_id = ? ORDER BY CAST(external_id AS INTEGER)",
                (run_id,),
            )
        ]

    def record_discovery_checkpoint(self, run_id: str, key: str, digest: str) -> None:
        """Append verified immutable evidence under a run-local discovery key."""
        with self.transaction():
            self._verify_artifact(digest)
            existing = self.database.connection.execute(
                "SELECT json_extract(payload_json, '$.digest') FROM events "
                "WHERE run_id = ? AND kind = 'discovery_checkpoint' "
                "AND json_extract(payload_json, '$.key') = ?",
                (run_id, key),
            ).fetchone()
            if existing is not None:
                if existing[0] != digest:
                    raise ValueError("discovery checkpoint is immutable")
                return
            self._event(run_id, "discovery_checkpoint", payload={"key": key, "digest": digest})

    def record_duplicate_group(self, group: StoredDuplicateGroup) -> None:
        """Insert an immutable group; collisions need distinct caller fingerprints."""
        with self.transaction():
            self.database.connection.execute(
                "INSERT OR IGNORE INTO duplicate_groups(id, run_id, method, fingerprint) "
                "VALUES (:id, :run_id, :method, :fingerprint)",
                asdict(group),
            )
            row = self.database.connection.execute(
                "SELECT * FROM duplicate_groups WHERE id = ?",
                (group.id,),
            ).fetchone()
            if row is None or dict(row) != asdict(group):
                raise ValueError("duplicate group mapping is immutable")

    def record_duplicate_candidate(self, candidate: DeduplicationCandidate) -> None:
        """Preserve original paths/bytes and never remap a recorded membership."""
        with self.transaction():
            if candidate.source_artifact_id is not None:
                self._verify_artifact(candidate.source_artifact_id)
            self.database.connection.execute(
                "INSERT OR IGNORE INTO candidates(id, run_id, repository_id, commit_sha, path, "
                "start_offset, end_offset, symbol_name, source_artifact_id, duplicate_group_id) "
                "VALUES (:id, :run_id, :repository_id, :commit_sha, :path, :start_offset, "
                ":end_offset, :symbol_name, :source_artifact_id, :duplicate_group_id)",
                asdict(candidate),
            )
            row = self.database.connection.execute(
                "SELECT * FROM candidates WHERE id = ?",
                (candidate.id,),
            ).fetchone()
            if row is None or dict(row) != asdict(candidate):
                raise ValueError("candidate duplicate mapping is immutable")

    def deduplication_rows(
        self,
        run_id: str,
        method: str,
        candidate_ids: tuple[str, ...] = (),
    ) -> tuple[list[StoredDuplicateGroup], list[DeduplicationCandidate]]:
        groups = self.database.connection.execute(
            "SELECT * FROM duplicate_groups WHERE run_id = ? AND method = ?",
            (run_id, method),
        )
        # Inspect expected members even after a corrupted NULL/foreign mapping,
        # without treating unrelated, unselected candidates as eligible members.
        candidates = self.database.connection.execute(
            "SELECT c.* FROM candidates c LEFT JOIN duplicate_groups g "
            "ON c.duplicate_group_id = g.id WHERE c.run_id = ? "
            "AND (g.method = ? OR c.id IN (SELECT value FROM json_each(?)))",
            (run_id, method, _json(candidate_ids)),
        )
        return (
            [StoredDuplicateGroup(**dict(row)) for row in groups],
            [DeduplicationCandidate(**dict(row)) for row in candidates],
        )

    def discovery_checkpoints(self, run_id: str) -> dict[str, str]:
        return {
            str(event.payload["key"]): str(event.payload["digest"])
            for event in self.events(run_id)
            if event.kind == "discovery_checkpoint"
        }

    def __enter__(self) -> "StateStore":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
