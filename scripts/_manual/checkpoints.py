"""Owned local manual checkpoints; per-file atomic publication, not a dataset schema."""

import json
import os
import re
import sqlite3
import tempfile
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from robo_minerador.config import MiningConfig
from robo_minerador.discovery.service import load_manifest
from robo_minerador.identity import stable_digest
from robo_minerador.screening.service import load_report
from robo_minerador.state.artifacts import ArtifactStore
from robo_minerador.state.store import StateStore

from .errors import CheckpointNotFound


class CheckpointIntegrityError(ValueError):
    """Manual checkpoint cannot be safely reused; exception text stays private."""


class LocalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class StorageReferences(LocalModel):
    state: Literal["state.sqlite3"]
    artifacts: Literal["artifacts"]


class ProcessingScope(LocalModel):
    purpose: Literal["partial_demonstration"]
    selection: Literal["complete_sha_sorted_repo_id"]
    limit: int = Field(gt=0)
    selected_ids: list[int]
    discovered_count: int = Field(ge=0)
    not_processed_count: int = Field(ge=0)


class ScreeningEntry(LocalModel):
    report_ref: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_id: str = Field(min_length=1)
    report: dict


class ManualCheckpoint(LocalModel):
    """Local envelope only; nested backend manifests retain their original schema."""

    schema_version: Literal["1.0"]
    purpose: Literal["manual"]
    not_final_dataset: Literal[True]
    source_kind: Literal["github", "fixture"]
    execution_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    project_root: str
    effective_config: dict
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    storage: StorageReferences
    run_id: str
    stage: Literal["discovery", "screening", "extraction"]
    status: Literal["completed", "partial"]
    error: None
    discovery_ref: str = Field(pattern=r"^[0-9a-f]{64}$")
    discovery: dict
    screening: list[ScreeningEntry]
    extraction: list[dict]
    processing_scope: ProcessingScope
    finished_at: str
    checkpoint_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ScreeningInterruption(LocalModel):
    code: Literal["infrastructure_interrupted"]
    error_type: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    phase: Literal["screening"]


class ResumableScreeningCheckpoint(ManualCheckpoint):
    """Internal sidecar only, anchored to a separately validated discovery input."""

    stage: Literal["screening"]
    status: Literal["interrupted"]
    error: ScreeningInterruption
    extraction: list[dict] = Field(max_length=0)


class ExtractionInterruption(ScreeningInterruption):
    phase: Literal["extraction"]


class ResumableExtractionCheckpoint(ManualCheckpoint):
    stage: Literal["extraction"]
    status: Literal["interrupted"]
    error: ExtractionInterruption


class LocalArtifacts(ArtifactStore):
    """Reject linked CAS objects before the production store reads/writes bytes."""

    def _path(self, digest):
        path = super()._path(digest)
        if path.is_symlink() or path.resolve() != path.absolute():
            raise ValueError("non-owned CAS path")
        return path


def publish(path, value, token=""):
    text = json.dumps(value, ensure_ascii=True, indent=2, allow_nan=False) + "\n"
    if token and token in text:
        # Fail closed instead of silently changing evidence bytes or identities.
        raise ValueError("credential in publication")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = stream.name
            stream.write(text.encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def save(directory, value, token=""):
    value["finished_at"] = datetime.now(UTC).isoformat()
    value["checkpoint_hash"] = stable_digest(
        {k: v for k, v in value.items() if k != "checkpoint_hash"}
    )
    path = directory / (value["stage"] + ".json")
    publish(path, value, token)
    publish(directory / "result.json", value, token)
    return path


def read_checkpoint(path, root, output, *, resume_screening=False, resume_extraction=False):
    """Validate paths before opening SQLite/CAS; validate evidence before reuse."""
    try:
        path = path.absolute()
        directory = path.parent
        if (
            directory.parent != output.resolve() / "runs"
            or directory.resolve() != directory
            or path.is_symlink()
            or path.name
            not in {"discovery.json", "screening.json", "extraction.json", "result.json"}
        ):
            raise ValueError("non-owned checkpoint path")
        value = json.loads(path.read_bytes())
        if not isinstance(value, dict):
            raise ValueError("manual checkpoint must be an object")
        resuming_screening = resume_screening and value.get("status") == "interrupted"
        resuming_extraction = resume_extraction and value.get("status") == "interrupted"
        resuming = resuming_screening or resuming_extraction
        model = (
            ResumableScreeningCheckpoint
            if resuming_screening
            else ResumableExtractionCheckpoint
            if resuming_extraction
            else ManualCheckpoint
        )
        model.model_validate(value)
        if (
            value["schema_version"] != "1.0"
            or value["purpose"] != "manual"
            or value["not_final_dataset"] is not True
            or (value["status"] not in {"completed", "partial"} and not resuming)
            or value["project_root"] != str(root.resolve())
            or value["storage"] != {"state": "state.sqlite3", "artifacts": "artifacts"}
            or not re.fullmatch(r"[0-9a-f]{32}", value["execution_id"])
            or directory.name != value["execution_id"]
            or value["checkpoint_hash"]
            != stable_digest({k: v for k, v in value.items() if k != "checkpoint_hash"})
        ):
            raise ValueError("invalid manual checkpoint")
        for name in ("state.sqlite3", "artifacts", "owner.json"):
            local = directory / name
            if not local.exists() or local.is_symlink() or local.resolve() != local:
                raise ValueError("non-owned storage")
        owner = json.loads((directory / "owner.json").read_bytes())
        if owner != {k: value[k] for k in ("execution_id", "project_root", "config_hash")}:
            raise ValueError("checkpoint ownership mismatch")
        config = MiningConfig.model_validate(value["effective_config"])
        if stable_digest(config.model_dump(mode="json")) != value["config_hash"]:
            raise ValueError("configuration mismatch")
        artifacts = LocalArtifacts(directory / "artifacts")
        manifest = load_manifest(artifacts, value["discovery_ref"])
        if manifest != value["discovery"] or manifest["run_id"] != value["run_id"]:
            raise ValueError("discovery mismatch")
        if manifest["config_hash"] != value["config_hash"]:
            raise ValueError("manifest configuration mismatch")
        with StateStore(directory / "state.sqlite3", artifacts=artifacts) as state:
            run = state.get_run(value["run_id"])
            if (
                run.config_hash != value["config_hash"]
                or json.loads(run.config_json) != (value["effective_config"])
            ):
                raise ValueError("run configuration mismatch")
            if (
                state.discovery_checkpoints(value["run_id"]).get("manifest")
                != value["discovery_ref"]
            ):
                raise ValueError("discovery not owned by run")
            evidence = {
                digest
                for event in state.events(value["run_id"])
                if event.task_id
                for digest in state.task_evidence(event.task_id)
            }
            ids = {r["repo_id"]: r for r in manifest["repositories"]}
            selection = value["processing_scope"]["selected_ids"]
            limit = value["processing_scope"]["limit"]
            expected = sorted(
                r["repo_id"] for r in ids.values() if r["status"] == "complete" and r["commit_sha"]
            )[:limit]
            if type(limit) is not int or limit < 1 or selection != expected:
                raise ValueError("invalid selection")
            rows = {r["id"]: r for r in state.repositories(value["run_id"])}
            for repository in ids.values():
                row = rows[repository["state_id"]]
                if (
                    row["external_id"] != str(repository["repo_id"])
                    or row["commit_sha"] != repository["commit_sha"]
                ):
                    raise ValueError("repository identity differs from state")
            screened_ids = []
            for entry in value["screening"]:
                report = load_report(artifacts, entry["report_ref"])
                if report != entry["report"] or entry["report_ref"] not in evidence:
                    raise ValueError("screening evidence mismatch")
                repo = report["repository"]
                task = state.get_task(entry["task_id"])
                if (
                    task.stage != "screening"
                    or task.entity_id != repo["state_id"]
                    or entry["report_ref"] not in state.task_evidence(task.id)
                ):
                    raise ValueError("screening task identity mismatch")
                screened_ids.append(repo["repo_id"])
                if repo != ids[repo["repo_id"]] or repo["repo_id"] not in selection:
                    raise ValueError("screening identity mismatch")
            if len(set(screened_ids)) != len(screened_ids):
                raise ValueError("duplicate screening evidence")
            if screened_ids != selection[: len(screened_ids)]:
                raise ValueError("screening evidence must be a prefix of the frozen selection")
            scope = value["processing_scope"]
            if scope["discovered_count"] != len(ids) or scope["not_processed_count"] != len(
                ids
            ) - len(screened_ids):
                raise ValueError("processing scope count mismatch")
            if (
                not resuming_screening
                and value["stage"] in {"screening", "extraction"}
                and len(value["screening"]) != len(selection)
            ):
                raise ValueError("incomplete screening checkpoint")
            for entry in value["extraction"]:
                if entry["accepted"] is not False or entry["coverage"] is not None:
                    raise ValueError("manual evidence cannot claim acceptance")
                if entry.get("extraction_ref"):
                    from robo_minerador.extraction.service import load_extraction

                    extracted = load_extraction(artifacts, entry["extraction_ref"])
                    if (
                        entry["extraction_ref"] not in evidence
                        or extracted.model_dump(mode="json") != entry["extraction_manifest"]
                    ):
                        raise ValueError("extraction evidence mismatch")
            if value["stage"] == "extraction":
                from .extraction import verify_extraction_results

                verify_extraction_results(
                    value, config, root, state, artifacts, allow_partial=resuming_extraction
                )
        return value, directory, config
    except (OSError, KeyError, TypeError, ValueError, sqlite3.Error) as error:
        raise CheckpointIntegrityError("manual checkpoint integrity failure") from error


def latest_checkpoint(root, output, stage):
    paths = sorted(
        (output.resolve() / "runs").glob(f"*/{stage}.json"),
        key=lambda p: p.lstat().st_mtime_ns,
        reverse=True,
    )
    for path in paths:
        try:
            value, _, _ = read_checkpoint(path, root, output)
            if value["stage"] == stage:
                return path
        except ValueError:
            continue
    raise CheckpointNotFound(stage)
