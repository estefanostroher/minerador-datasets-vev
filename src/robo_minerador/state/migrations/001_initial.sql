CREATE TABLE runs (
    id TEXT PRIMARY KEY,
    config_hash TEXT NOT NULL CHECK(length(trim(config_hash)) > 0),
    config_json TEXT CHECK(config_json IS NULL OR (json_valid(config_json) AND json_type(config_json) = 'object')),
    meta_json TEXT NOT NULL CHECK(json_valid(meta_json) AND json_type(meta_json) = 'object'),
    created_at TEXT NOT NULL
);

CREATE TRIGGER runs_config_immutable BEFORE UPDATE ON runs
BEGIN SELECT RAISE(ABORT, 'collection configuration is immutable'); END;

CREATE TABLE repositories (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    external_id TEXT NOT NULL,
    full_name TEXT NOT NULL,
    commit_sha TEXT,
    UNIQUE(run_id, external_id),
    UNIQUE(id, run_id)
);

CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    entity_id TEXT NOT NULL CHECK(length(trim(entity_id)) > 0),
    stage TEXT NOT NULL CHECK(length(trim(stage)) > 0),
    outcome TEXT NOT NULL CHECK(outcome IN ('running', 'completed', 'rejected', 'unsupported', 'interrupted')),
    attempt INTEGER NOT NULL CHECK(attempt >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id, entity_id, stage),
    UNIQUE(id, run_id),
    UNIQUE(id, run_id, entity_id, stage)
);

CREATE TRIGGER tasks_valid_transition BEFORE UPDATE OF outcome, attempt ON tasks
WHEN NOT (
    (OLD.outcome = 'running' AND NEW.outcome IN ('completed', 'rejected', 'unsupported', 'interrupted') AND NEW.attempt = OLD.attempt)
    OR (OLD.outcome = 'interrupted' AND NEW.outcome = 'running' AND NEW.attempt = OLD.attempt + 1)
)
BEGIN SELECT RAISE(ABORT, 'invalid task transition'); END;

CREATE TRIGGER tasks_identity_immutable BEFORE UPDATE OF id, run_id, entity_id, stage ON tasks
BEGIN SELECT RAISE(ABORT, 'task identity is immutable'); END;

CREATE TABLE artifacts (
    id TEXT PRIMARY KEY CHECK(length(id) = 64 AND id NOT GLOB '*[^0-9a-f]*'),
    path TEXT NOT NULL UNIQUE,
    byte_size INTEGER NOT NULL CHECK(byte_size >= 0),
    created_at TEXT NOT NULL
);

CREATE TRIGGER artifacts_immutable BEFORE UPDATE ON artifacts
BEGIN SELECT RAISE(ABORT, 'artifact registry is immutable'); END;

CREATE TABLE task_artifacts (
    task_id TEXT NOT NULL REFERENCES tasks(id),
    artifact_id TEXT NOT NULL REFERENCES artifacts(id),
    PRIMARY KEY(task_id, artifact_id)
);
CREATE TRIGGER task_artifacts_no_update BEFORE UPDATE ON task_artifacts
BEGIN SELECT RAISE(ABORT, 'evidence links are append-only'); END;
CREATE TRIGGER task_artifacts_no_delete BEFORE DELETE ON task_artifacts
BEGIN SELECT RAISE(ABORT, 'evidence links are append-only'); END;

CREATE TABLE assessments (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    criterion_id TEXT NOT NULL CHECK(length(trim(criterion_id)) > 0),
    status TEXT NOT NULL CHECK(status IN ('approved', 'rejected', 'not_evaluated')),
    reason_code TEXT NOT NULL CHECK(length(trim(reason_code)) > 0),
    rule_version TEXT NOT NULL CHECK(length(trim(rule_version)) > 0),
    stage TEXT NOT NULL,
    explanation TEXT NOT NULL CHECK(length(trim(explanation)) > 0),
    observed_at TEXT NOT NULL,
    FOREIGN KEY(task_id, run_id, entity_id, stage) REFERENCES tasks(id, run_id, entity_id, stage)
);

CREATE INDEX assessments_entity ON assessments(run_id, entity_id, stage, criterion_id);
CREATE TRIGGER assessments_preserve_rejection BEFORE INSERT ON assessments
WHEN NEW.status != 'rejected' AND EXISTS (
    SELECT 1 FROM assessments WHERE run_id = NEW.run_id AND entity_id = NEW.entity_id
    AND stage = NEW.stage AND criterion_id = NEW.criterion_id AND status = 'rejected'
)
BEGIN SELECT RAISE(ABORT, 'criterion rejection is terminal for this collection'); END;
CREATE TRIGGER assessments_no_update BEFORE UPDATE ON assessments
BEGIN SELECT RAISE(ABORT, 'assessments are append-only'); END;
CREATE TRIGGER assessments_no_delete BEFORE DELETE ON assessments
BEGIN SELECT RAISE(ABORT, 'assessments are append-only'); END;

CREATE TABLE assessment_evidence (
    assessment_id TEXT NOT NULL REFERENCES assessments(id),
    artifact_id TEXT NOT NULL REFERENCES artifacts(id),
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
    PRIMARY KEY(assessment_id, ordinal)
);
CREATE TRIGGER assessment_evidence_no_update BEFORE UPDATE ON assessment_evidence
BEGIN SELECT RAISE(ABORT, 'evidence links are append-only'); END;
CREATE TRIGGER assessment_evidence_no_delete BEFORE DELETE ON assessment_evidence
BEGIN SELECT RAISE(ABORT, 'evidence links are append-only'); END;

CREATE TABLE duplicate_groups (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    method TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    UNIQUE(run_id, method, fingerprint),
    UNIQUE(id, run_id)
);

CREATE TABLE candidates (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    path TEXT NOT NULL,
    start_offset INTEGER NOT NULL CHECK(start_offset >= 0),
    end_offset INTEGER NOT NULL CHECK(end_offset >= start_offset),
    symbol_name TEXT,
    source_artifact_id TEXT REFERENCES artifacts(id),
    duplicate_group_id TEXT,
    FOREIGN KEY(repository_id, run_id) REFERENCES repositories(id, run_id),
    FOREIGN KEY(duplicate_group_id, run_id) REFERENCES duplicate_groups(id, run_id),
    UNIQUE(run_id, repository_id, commit_sha, path, start_offset),
    UNIQUE(id, run_id)
);

CREATE TABLE suite_runs (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    candidate_id TEXT,
    command_json TEXT NOT NULL CHECK(json_valid(command_json) AND json_type(command_json) = 'array'),
    environment_hash TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK(outcome IN ('running', 'completed', 'rejected', 'unsupported', 'interrupted')),
    exit_code INTEGER,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    stdout_artifact_id TEXT REFERENCES artifacts(id),
    stderr_artifact_id TEXT REFERENCES artifacts(id),
    FOREIGN KEY(task_id, run_id) REFERENCES tasks(id, run_id),
    FOREIGN KEY(candidate_id, run_id) REFERENCES candidates(id, run_id)
);

CREATE TABLE case_results (
    id TEXT PRIMARY KEY,
    suite_run_id TEXT NOT NULL REFERENCES suite_runs(id),
    case_name TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK(outcome IN ('passed', 'failed', 'skipped', 'error')),
    duration_ms REAL CHECK(duration_ms >= 0),
    evidence_artifact_id TEXT REFERENCES artifacts(id),
    UNIQUE(suite_run_id, case_name)
);

CREATE TABLE measurements (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    candidate_id TEXT,
    name TEXT NOT NULL,
    value REAL,
    availability TEXT NOT NULL CHECK(availability IN ('available', 'unavailable', 'not_applicable')),
    method TEXT NOT NULL,
    reason_code TEXT,
    observed_at TEXT NOT NULL,
    evidence_artifact_id TEXT REFERENCES artifacts(id),
    FOREIGN KEY(task_id, run_id) REFERENCES tasks(id, run_id),
    FOREIGN KEY(candidate_id, run_id) REFERENCES candidates(id, run_id),
    CHECK((availability = 'available' AND value IS NOT NULL AND abs(value) <= 1.7976931348623157e308)
        OR (availability != 'available' AND value IS NULL AND reason_code IS NOT NULL AND length(trim(reason_code)) > 0))
);

CREATE TABLE events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id),
    task_id TEXT,
    attempt INTEGER CHECK(attempt >= 1),
    kind TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json) AND json_type(payload_json) = 'object'),
    FOREIGN KEY(task_id, run_id) REFERENCES tasks(id, run_id),
    CHECK((task_id IS NULL AND attempt IS NULL) OR (task_id IS NOT NULL AND attempt IS NOT NULL))
);
CREATE INDEX events_by_run ON events(run_id, sequence);
CREATE TRIGGER events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
