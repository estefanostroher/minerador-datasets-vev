"""Discovery orchestration and offline replay of immutable manifest evidence.

Each HTTP attempt is published and registered before processing its results.
Terminal request checkpoints are reused after reopening SQLite. Partition and
repository snapshots are derivable from those responses; no mutable branch name
is substituted for an observed commit SHA. This stage makes no H-R05 assessment.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, cast

from robo_minerador.config import MiningConfig
from robo_minerador.discovery.github import (
    Envelope,
    GitHubClient,
    GitHubError,
    GitHubResponse,
    replay_response,
)
from robo_minerador.discovery.partitions import (
    Partition,
    PartitionLimit,
    initial_partitions,
    timestamp,
)
from robo_minerador.identity import stable_digest
from robo_minerador.state.artifacts import ArtifactStore
from robo_minerador.state.store import StateStore


def _bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


@dataclass(frozen=True)
class DiscoveryManifest:
    run_id: str
    digest: str
    path: Path


def load_manifest(artifacts: ArtifactStore, digest: str) -> dict[str, Any]:
    """Load and verify a discovery manifest and all evidence, entirely offline."""
    model = json.loads(artifacts.get(digest))
    if (
        not isinstance(model, dict)
        or model.get("kind") != "discovery"
        or model.get("schema_version") != "1.0"
    ):
        raise ValueError("unsupported discovery manifest")
    for reference in model["artifacts"]:
        artifacts.get(reference)
    return cast(dict[str, Any], model)


class DiscoveryService:
    def __init__(self, github: GitHubClient, state: StateStore, artifacts: ArtifactStore) -> None:
        self.github, self.state, self.artifacts = github, state, artifacts

    def discover(self, run_id: str | None, config: MiningConfig) -> DiscoveryManifest:
        """Create a run when ID is None, otherwise resume its immutable configuration."""
        config = config.model_copy(deep=True)
        config_model = config.model_dump(mode="json")
        config_hash = stable_digest(config_model)
        # Validate second-resolution scopes before creating any run or making HTTP calls.
        partitions = initial_partitions(config.discovery)
        if self.github.config != config.discovery:
            raise ValueError("GitHub client configuration differs from discovery configuration")
        if run_id is None:
            run_id = self.state.create_run(config_hash, config_json=_bytes(config_model).decode())
        run = self.state.get_run(run_id)
        if run.config_hash != config_hash or (
            run.config_json is not None and json.loads(run.config_json) != config_model
        ):
            raise ValueError("run configuration is immutable; create a new run")
        session = _DiscoveryRun(self, run_id)
        if "manifest" in session.checkpoints:
            digest = session.checkpoints["manifest"]
            load_manifest(self.artifacts, digest)
            return DiscoveryManifest(run_id, digest, self.artifacts.root / digest)
        config_artifact = session.save("config", config_model)
        for partition in partitions:
            session.partition(partition)
        complete = not session.warnings
        model = {
            "schema_version": "1.0",
            "kind": "discovery",
            "run_id": run_id,
            "created_at": run.created_at.isoformat(),
            "config_hash": config_hash,
            "config_artifact": config_artifact,
            "status": "complete" if complete else "partial",
            "exhaustive": complete,
            "H-R05": "not_evaluated",
            "steps": {
                "discovery": "complete" if complete else "partial",
                "readme_triage": "not_evaluated",
                "H-R05": "not_evaluated",
            },
            "repositories": [session.repos[id] for id in sorted(session.repos)],
            "partitions": session.partitions,
            "queries": session.queries,
            "warnings": session.warnings,
            "artifacts": sorted(set(session.checkpoints.values())),
        }
        digest = session.save("manifest", model)
        return DiscoveryManifest(run_id, digest, self.artifacts.root / digest)


class _DiscoveryRun:
    def __init__(self, service: DiscoveryService, run_id: str) -> None:
        self.github, self.state, self.artifacts = service.github, service.state, service.artifacts
        self.run_id = run_id
        self.checkpoints = self.state.discovery_checkpoints(run_id)
        self.repos: dict[int, dict[str, Any]] = {}
        self.partitions: list[dict[str, Any]] = []
        self.queries: list[dict[str, Any]] = []
        self.warnings: list[dict[str, Any]] = []

    def save(self, key: str, model: object) -> str:
        digest = self.artifacts.put(_bytes(model))
        self.state.register_artifact(digest)
        self.state.record_discovery_checkpoint(self.run_id, key, digest)
        self.checkpoints[key] = digest
        return digest

    def fetch(self, key: str, request: Callable[[], GitHubResponse]) -> tuple[GitHubResponse, str]:
        key = "http/" + key
        if key in self.checkpoints:
            digest = self.checkpoints[key]
            return replay_response(json.loads(self.artifacts.get(digest))), digest
        previous = self.github.on_response

        def record(envelope: Envelope) -> None:
            checkpoint = key if envelope["terminal"] else "attempt/" + stable_digest(envelope)
            self.save(checkpoint, envelope)
            if previous:
                previous(envelope)

        self.github.on_response = record
        try:
            response = request()
            return response, self.checkpoints[key]
        finally:
            self.github.on_response = previous

    def warn(self, code: str, **context: object) -> None:
        self.warnings.append({"code": code, **context})

    def partition(self, partition: Partition) -> bool:
        record: dict[str, Any] = {
            "id": partition.id,
            "start": timestamp(partition.window.start),
            "end": timestamp(partition.window.end),
            "license": partition.license,
            "archived": partition.archived,
            "query": partition.query,
            "status": "complete",
        }
        self.partitions.append(record)
        seen_ids: set[int] = set()
        duplicate_items = 0
        observed_totals: list[int] = []
        page = 1
        complete = True
        while True:
            key = f"search/{partition.id}/{page}"
            try:
                response, digest = self.fetch(
                    key, partial(self.github.search_page, partition.query, page)
                )
            except GitHubError as error:
                digest = self.checkpoints["http/" + key]
                self.query_record(record, page, digest, error.envelope, {})
                self.warn("search_failed", partition=partition.id, error=error.envelope["error"])
                complete = False
                break
            data = response.data
            self.query_record(record, page, digest, response.envelope, data)
            total, incomplete, items = (
                data.get("total_count"),
                data.get("incomplete_results"),
                data.get("items"),
            )
            if (
                type(total) is not int
                or total < 0
                or type(incomplete) is not bool
                or not isinstance(items, list)
                or len(items) > self.github.config.per_page
            ):
                self.warn("invalid_search_response", partition=partition.id, page=page)
                complete = False
                break
            # Preserve all observed IDs, including parent results and earlier pages,
            # even when GitHub later reports an overflowing or incomplete partition.
            observed_totals.append(total)
            for item in items:
                if isinstance(item, dict) and type(item.get("id")) is int and item["id"] > 0:
                    if item["id"] in seen_ids:
                        duplicate_items += 1
                    seen_ids.add(item["id"])
                self.repository(item, digest)
            if total > 1000 or incomplete:
                try:
                    children = partition.children()
                except PartitionLimit:
                    self.warn(
                        "minimum_window",
                        partition=partition.id,
                        total=total,
                        incomplete_results=incomplete,
                    )
                    complete = False
                else:
                    record["status"] = "split"
                    record["children"] = [child.id for child in children]
                    results = [self.partition(child) for child in children]
                    complete = all(results)
                break
            seen = len(seen_ids)
            expected = max(observed_totals)
            next_link = 'rel="next"' in response.envelope["headers"].get("link", "")
            if not items or (len(items) < self.github.config.per_page and not next_link):
                if seen < expected:
                    self.warn(
                        "pagination_shortfall", partition=partition.id, seen=seen, total=expected
                    )
                    complete = False
                break
            if page * self.github.config.per_page >= 1000:
                if next_link or seen < expected:
                    self.warn("pagination_limit", partition=partition.id)
                    complete = False
                break
            page += 1
        if record["status"] != "split":
            record["status"] = "complete" if complete else "partial"
        record.update(
            unique_ids=len(seen_ids),
            duplicate_items=duplicate_items,
            observed_totals=observed_totals,
        )
        self.save("partition/" + partition.id, record)
        return complete

    def query_record(
        self,
        partition: dict[str, Any],
        page: int,
        digest: str,
        envelope: Envelope,
        data: dict[str, Any],
    ) -> None:
        self.queries.append(
            {
                "partition_id": partition["id"],
                "start": partition["start"],
                "end": partition["end"],
                "page": page,
                "query": partition["query"],
                "api": envelope["api"],
                "api_version": envelope["api_version"],
                "sort": "updated",
                "order": "asc",
                "per_page": self.github.config.per_page,
                "total": data.get("total_count"),
                "incomplete_results": data.get("incomplete_results"),
                "response_artifact": digest,
                "error": envelope["error"],
                "status": envelope["status"],
                "observed_at": envelope["observed_at"],
            }
        )

    def repository(self, item: Any, source: str) -> None:
        if not isinstance(item, dict) or type(item.get("id")) is not int or item["id"] < 1:
            self.warn("invalid_repository", response_artifact=source)
            return
        id = item["id"]
        if id in self.repos:
            return
        model: dict[str, Any] = {
            "repo_id": id,
            "full_name": item.get("full_name"),
            "url": item.get("html_url"),
            "license": item.get("license"),
            "stars": item.get("stargazers_count"),
            "created_at": item.get("created_at"),
            "archived": item.get("archived"),
            "fork": item.get("fork"),
            "default_branch": item.get("default_branch"),
            "commit_sha": None,
            "readme": "not_evaluated",
            "H-R05": "not_evaluated",
            "status": "complete",
            "source_response": source,
        }
        stage = "repository"
        key = f"repository/{id}"
        try:
            if not model["full_name"] or not model["default_branch"]:
                response, digest = self.fetch(key, lambda: self.github.get_repository(id))
                detail = response.data
                if detail.get("id") != id:
                    raise ValueError("repository identity mismatch")
                for field, api_field in {
                    "full_name": "full_name",
                    "default_branch": "default_branch",
                    "url": "html_url",
                    "license": "license",
                    "stars": "stargazers_count",
                    "created_at": "created_at",
                    "archived": "archived",
                    "fork": "fork",
                }.items():
                    model[field] = detail.get(api_field)
                model["repository_response"] = digest
            name, ref = model["full_name"], model["default_branch"]
            if not isinstance(name, str) or not isinstance(ref, str) or not name or not ref:
                raise ValueError("missing repository name or default branch")
            model["commit_ref"] = ref
            stage, key = "commit", f"commit/{id}"
            response, digest = self.fetch(key, lambda: self.github.get_commit(name, ref))
            model["commit_response"] = digest
            sha = response.data.get("sha")
            if not isinstance(sha, str) or re.fullmatch(r"[0-9a-fA-F]{40}", sha) is None:
                raise ValueError("invalid commit SHA")
            model.update(
                commit_sha=sha,
                commit_ref=ref,
                commit_response=digest,
                commit_observed_at=response.envelope["observed_at"],
            )
        except GitHubError as error:
            inaccessible = error.envelope["error"] == "http_permanent" and (
                error.envelope["status"] in (403, 404, 410, 451)
            )
            model["status"] = "inaccessible" if inaccessible else "unresolved"
            model["error_stage"] = stage
            model[stage + "_response"] = self.checkpoints["http/" + key]
            model["error"] = error.envelope["error"]
            self.warn("repository_" + model["status"], repo_id=id, error=model["error"])
        except ValueError:
            model["status"] = "unresolved"
            self.warn("invalid_repository_metadata", repo_id=id)
        name = model["full_name"]
        if isinstance(name, str) and name:
            model["state_id"] = self.state.upsert_repository(
                self.run_id, id, name, model["commit_sha"]
            )
        self.repos[id] = model
        self.save(f"repository/{id}", model)
