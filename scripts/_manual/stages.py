"""Service orchestration shared by individual and combined manual actions."""

import sqlite3
from pathlib import Path
from uuid import uuid4

import httpx

from robo_minerador.config import DiscoveryConfig, load_config
from robo_minerador.discovery.github import GitHubClient
from robo_minerador.discovery.partitions import initial_partitions
from robo_minerador.discovery.service import DiscoveryService, load_manifest
from robo_minerador.identity import stable_digest
from robo_minerador.screening.service import ScreeningService, load_report
from robo_minerador.state.store import StateStore

from .checkpoints import (
    CheckpointIntegrityError,
    LocalArtifacts,
    latest_checkpoint,
    publish,
    read_checkpoint,
    save,
)
from .errors import MissingGitHubToken


def select(value, limit):
    repositories = value["discovery"]["repositories"]
    selected = sorted(
        (r for r in repositories if r["status"] == "complete" and r["commit_sha"]),
        key=lambda r: r["repo_id"],
    )[:limit]
    value["processing_scope"] = {
        "purpose": "partial_demonstration",
        "selection": "complete_sha_sorted_repo_id",
        "limit": limit,
        "selected_ids": [r["repo_id"] for r in selected],
        "discovered_count": len(repositories),
        "not_processed_count": len(repositories),
    }
    return selected


def run_stage(
    action,
    root,
    output,
    *,
    token="",
    input_json=None,
    max_repos=None,
    created_from="2024-01-01T00:00:00Z",
    created_until="2024-01-01T00:05:00Z",
    build_analyzer=False,
):
    root, output = Path(root).resolve(strict=True), Path(output).resolve()
    if action not in {"discovery", "screening", "extraction", "all"}:
        raise ValueError("unknown action")
    if max_repos is not None and (type(max_repos) is not int or max_repos < 1):
        raise ValueError("positive max_repos required")
    if action != "extraction" and not token:
        raise MissingGitHubToken()
    if action in {"discovery", "all"}:
        if input_json:
            raise ValueError("discovery cannot consume a dependent-stage checkpoint")
        config = load_config(root / "config/pilot.toml")
        discovery = config.discovery.model_dump(mode="json")
        discovery.update(created_from=created_from, created_until=created_until)
        config = config.model_copy(update={"discovery": DiscoveryConfig.model_validate(discovery)})
        initial_partitions(config.discovery)
        execution_id = uuid4().hex
        directory = output / "runs" / execution_id
        value = {
            "schema_version": "1.0",
            "purpose": "manual",
            "not_final_dataset": True,
            "source_kind": "github",
            "execution_id": execution_id,
            "project_root": str(root),
            "effective_config": config.model_dump(mode="json"),
            "config_hash": stable_digest(config.model_dump(mode="json")),
            "storage": {"state": "state.sqlite3", "artifacts": "artifacts"},
            "run_id": None,
            "stage": "discovery",
            "status": "running",
            "error": None,
            "discovery_ref": None,
            "discovery": None,
            "screening": [],
            "extraction": [],
            "processing_scope": {},
        }
        directory.mkdir(parents=True, exist_ok=False)
        publish(
            directory / "owner.json",
            {k: value[k] for k in ("execution_id", "project_root", "config_hash")},
            token,
        )
    else:
        previous = "discovery" if action == "screening" else "screening"
        input_json = input_json or latest_checkpoint(root, output, previous)
        value, directory, config = read_checkpoint(Path(input_json), root, output)
        existing = directory / "screening.json"
        if action == "screening" and existing.is_file():
            anchor = value
            value, directory, config = read_checkpoint(
                existing, root, output, resume_screening=anchor["stage"] == "discovery"
            )
            if value["stage"] != "screening" or any(
                value[key] != anchor[key]
                for key in ("execution_id", "run_id", "config_hash", "discovery_ref")
            ):
                raise CheckpointIntegrityError("screening sidecar differs from its discovery")
        if action == "screening" and value["stage"] not in {"discovery", "screening"}:
            raise ValueError("screening requires discovery or screening checkpoint")
        if action == "extraction" and value["stage"] not in {"screening", "extraction"}:
            raise ValueError("extraction requires screening checkpoint")
        if (
            value["screening"]
            and max_repos is not None
            and (max_repos != value["processing_scope"]["limit"])
        ):
            raise ValueError("selection already frozen")
        if (
            action == "screening"
            and value["stage"] == "screening"
            and value["status"] != "interrupted"
        ):
            return directory / "screening.json"
        existing_extraction = directory / "extraction.json"
        if action == "extraction" and existing_extraction.is_file():
            finished, _, _ = read_checkpoint(
                existing_extraction, root, output, resume_extraction=True
            )
            if finished["stage"] != "extraction" or any(
                finished[key] != value[key]
                for key in (
                    "execution_id",
                    "run_id",
                    "config_hash",
                    "discovery_ref",
                    "processing_scope",
                    "screening",
                )
            ):
                raise CheckpointIntegrityError("extraction differs from its screening checkpoint")
            if finished["status"] != "interrupted":
                return existing_extraction
        if value["status"] == "interrupted":
            # The sidecar freezes selection and records progress, never completion.
            # Reconstruct through production replay so completed reports need no HTTP.
            value["status"] = (
                "completed" if value["discovery"]["status"] == "complete" else "partial"
            )
            value["error"] = None
            value["screening"] = []
    artifacts = LocalArtifacts(directory / "artifacts")
    state = StateStore(directory / "state.sqlite3", artifacts=artifacts)
    try:
        with httpx.Client(timeout=60, trust_env=False) as http:
            github = GitHubClient(config.discovery, http=http, token=token)
            if action in {"discovery", "all"}:
                ref = DiscoveryService(github, state, artifacts).discover(None, config)
                value["run_id"], value["discovery_ref"] = ref.run_id, ref.digest
                value["discovery"] = load_manifest(artifacts, ref.digest)
                select(value, max_repos or 10)
                value["status"] = (
                    "completed" if value["discovery"]["status"] == "complete" else "partial"
                )
                save(directory, value, token)
            if action in {"screening", "all"}:
                value["stage"] = "screening"
                selected = select(value, max_repos or value["processing_scope"]["limit"])
                for repository in selected:
                    ref = ScreeningService(github, state, artifacts).screen(
                        value["run_id"], repository, config.execution
                    )
                    value["screening"].append(
                        {
                            "report_ref": ref.digest,
                            "task_id": ref.task_id,
                            "report": load_report(artifacts, ref.digest),
                        }
                    )
                    value["processing_scope"]["not_processed_count"] -= 1
                save(directory, value, token)
            if action in {"extraction", "all"}:
                from .extraction import extract_repositories

                value["stage"] = "extraction"
                value["extraction"] = extract_repositories(
                    value, config, root, state, artifacts, build_analyzer=build_analyzer
                )
                save(directory, value, token)
        return directory / (value["stage"] + ".json")
    except (Exception, KeyboardInterrupt) as error:
        value["status"] = "interrupted"
        value["error"] = {
            "code": "integrity_failure"
            if isinstance(error, ValueError | KeyError | FileNotFoundError | sqlite3.Error)
            else "infrastructure_interrupted",
            "error_type": type(error).__name__,
            "phase": value["stage"],
        }
        if value["run_id"] is None:
            row = state.database.connection.execute("SELECT id FROM runs").fetchone()
            if row:
                value["run_id"] = row["id"]
        return save(directory, value, token)
    finally:
        state.close()
