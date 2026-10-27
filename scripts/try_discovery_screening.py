"""Manual discovery/static-screening smoke run; never execute repository code."""

import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from pydantic import ValidationError

from robo_minerador.config import DiscoveryConfig, load_config
from robo_minerador.discovery.github import GitHubClient
from robo_minerador.discovery.partitions import initial_partitions
from robo_minerador.discovery.service import DiscoveryService, load_manifest
from robo_minerador.screening.service import ScreeningService, load_report
from robo_minerador.state.artifacts import ArtifactIntegrityError, ArtifactStore
from robo_minerador.state.store import InvalidTransition, StateStore


def utc_now():
    return datetime.now(UTC).isoformat()


def publish(path, model, token):
    """Publish only summary/report data, never HTTP envelopes or exception messages."""
    text = json.dumps(model, ensure_ascii=True, indent=2, allow_nan=False)
    text = text.replace(token, "[REDACTED]")
    text = re.sub(r"https?://[^/\s\"]+@", "https://[REDACTED]@", text)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write((text + "\n").encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--created-from", default="2024-01-01T00:00:00Z")
    parser.add_argument("--created-until", default="2024-01-01T00:05:00Z")
    parser.add_argument("--max-repos", type=int, default=3)
    args = parser.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        print("GITHUB_TOKEN ausente; use o script PowerShell no seu terminal.", file=sys.stderr)
        return 2
    try:
        if args.max_repos < 1:
            raise ValueError
        root = args.project_root.resolve(strict=True)
        config = load_config(root / "config" / "pilot.toml")
        discovery = config.discovery.model_dump(mode="json")
        discovery.update(created_from=args.created_from, created_until=args.created_until)
        config = config.model_copy(update={"discovery": DiscoveryConfig.model_validate(discovery)})
        initial_partitions(config.discovery)  # Validate interval precision before any writes.
    except (OSError, ValueError, ValidationError):
        print("Configuração/argumentos inválidos; nenhum teste iniciado.", file=sys.stderr)
        return 2

    output = root / "results" / "manual-smoke"
    execution_id = uuid4().hex
    run_directory = output / "runs" / execution_id
    result = {
        "schema_version": "1.0",
        "purpose": "smoke",
        "not_final_dataset": True,
        "execution_id": execution_id,
        "run_id": None,
        "started_at": utc_now(),
        "finished_at": None,
        "status": "running",
        "stage_completed": None,
        "extraction": "not_evaluated",
        "effective_config": config.model_dump(mode="json"),
        "discovery": None,
        "processing_scope": {
            "purpose": "demonstration",
            "selection": "first_complete_with_sha_sorted_by_repo_id",
            "limit": args.max_repos,
            "selected_ids": [],
            "not_processed_count": None,
        },
        "candidates_selected": 0,
        "candidates_processed": 0,
        "pending_ids": [],
        "assessment_status_counts": {},
        "repositories": [],
        "error": None,
    }
    state = None
    phase = "initialization"
    exit_code = 0
    try:
        run_directory.mkdir(parents=True, exist_ok=False)
        artifacts = ArtifactStore(run_directory / "artifacts")
        state = StateStore(run_directory / "state.sqlite3", artifacts=artifacts)
        with httpx.Client(timeout=60.0, trust_env=False) as http:
            github = GitHubClient(config.discovery, http=http, token=token)
            phase = "discovery"
            manifest_ref = DiscoveryService(github, state, artifacts).discover(None, config)
            result["run_id"] = manifest_ref.run_id
            manifest = load_manifest(artifacts, manifest_ref.digest)
            repositories = manifest["repositories"]
            result["discovery"] = {
                "manifest_digest": manifest_ref.digest,
                "run_id": manifest_ref.run_id,
                "discovered_count": len(repositories),
                "status": manifest["status"],
                "exhaustive": manifest["exhaustive"],
                "warnings": manifest["warnings"],
                "repository_status_counts": dict(Counter(r["status"] for r in repositories)),
            }
            result["stage_completed"] = "discovery"
            eligible = sorted(
                (r for r in repositories if r["status"] == "complete" and r["commit_sha"]),
                key=lambda r: r["repo_id"],
            )
            selected = eligible[:args.max_repos]
            selected_ids = [r["repo_id"] for r in selected]
            result["processing_scope"]["selected_ids"] = selected_ids
            result["processing_scope"]["not_processed_count"] = len(repositories)
            result["candidates_selected"] = len(selected)
            result["pending_ids"] = selected_ids.copy()
            screening = ScreeningService(github, state, artifacts)
            phase = "screening"
            for repository in selected:
                report_ref = screening.screen(manifest_ref.run_id, repository, config.execution)
                report = load_report(artifacts, report_ref.digest)
                result["repositories"].append({
                    "name": repository["full_name"],
                    "id": repository["repo_id"],
                    "sha": repository["commit_sha"],
                    "report_digest": report_ref.digest,
                    "snapshot_digest": report["snapshot_manifest"],
                    "assessments": report["assessments"],
                    "command_plan": report["commands"],
                    "environment_status": next(
                        a for a in report["assessments"] if a["criterion_id"] == "H-R03"
                    ),
                    "report": report,
                })
                result["candidates_processed"] += 1
                result["pending_ids"].remove(repository["repo_id"])
                result["processing_scope"]["not_processed_count"] -= 1
            result["stage_completed"] = "screening"
            # Partial discovery is factual evidence, not a successful exhaustive run.
            if manifest["status"] != "complete":
                result["status"] = "partial"
                result["error"] = {"code": "discovery_incomplete", "context": "discovery"}
                exit_code = 3
            else:
                result["status"] = "completed"
    except (Exception, KeyboardInterrupt) as error:
        integrity = isinstance(
            error, ArtifactIntegrityError | InvalidTransition | sqlite3.IntegrityError | ValueError
        )
        exit_code = 4 if integrity else 3
        result["status"] = "interrupted"
        result["error"] = {
            "code": "integrity_failure" if integrity else "infrastructure_interrupted",
            "type": type(error).__name__,
            "context": phase,
        }
    finally:
        if state is not None:
            # Discovery creates the run internally; retain its ID even on interruption.
            if result["run_id"] is None:
                row = state.database.connection.execute("SELECT id FROM runs").fetchone()
                if row is not None:
                    result["run_id"] = row["id"]
            state.close()

    result["finished_at"] = utc_now()
    result["assessment_status_counts"] = dict(Counter(
        assessment["status"]
        for repository in result["repositories"]
        for assessment in repository["assessments"]
    ))
    try:
        if not run_directory.is_dir():
            raise OSError
        publish(run_directory / "result.json", result, token)
        publish(output / "result.json", result, token)
    except OSError:
        print("Falha ao publicar JSON do teste manual.", file=sys.stderr)
        return 3
    print(f"status={result['status']} selecionados={result['candidates_selected']} "
          f"processados={result['candidates_processed']} pendentes={len(result['pending_ids'])}")
    print(f"JSON: {output / 'result.json'}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
