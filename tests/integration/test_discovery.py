import json
import sqlite3
from pathlib import Path

import httpx
import pytest

from robo_minerador.config import MiningConfig
from robo_minerador.discovery.github import GitHubClient
from robo_minerador.discovery.service import DiscoveryService, load_manifest
from robo_minerador.identity import stable_digest
from robo_minerador.state.artifacts import ArtifactStore
from robo_minerador.state.store import StateStore


def configuration(tmp_path: Path, **values: object) -> MiningConfig:
    return MiningConfig.model_validate(
        {
            "workspace": str(tmp_path),
            "environment_catalog": "catalog.json",
            "methodology_files": ["method.md"],
            "discovery": {
                "created_from": "2020-01-01T00:00:00Z",
                "created_until": "2020-01-01T00:00:02Z",
                "per_page": 2,
                "include_archived": False,
                **values,
            },
        }
    )


def repository(id: int, name: str | None = None) -> dict[str, object]:
    return {
        "id": id,
        "full_name": name or f"owner/repo{id}",
        "default_branch": "main",
        "html_url": f"https://github.com/owner/repo{id}",
        "license": {"spdx_id": "MIT"},
        "stargazers_count": 7,
        "created_at": "2020-01-01T00:00:00Z",
        "archived": False,
    }


def test_pagination_dedup_sha_manifest_and_reopen(tmp_path: Path) -> None:
    cfg = configuration(tmp_path)
    artifacts = ArtifactStore(tmp_path / "objects")
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/search/repositories":
            if "license:MIT " not in request.url.params["q"]:
                return httpx.Response(
                    200, json={"total_count": 0, "incomplete_results": False, "items": []}
                )
            page = int(request.url.params["page"])
            items = {
                1: [repository(2), repository(1)],
                2: [repository(2, "renamed/repo"), repository(3)],
                3: [],
            }[page]
            return httpx.Response(
                200, json={"total_count": 3, "incomplete_results": False, "items": items}
            )
        if "/commits/" in request.url.path:
            return httpx.Response(200, json={"sha": "a" * 40})
        raise AssertionError(request.url)

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
            service = DiscoveryService(GitHubClient(cfg.discovery, http=http), state, artifacts)
            manifest = service.discover(None, cfg)
            run_id = manifest.run_id
            data = load_manifest(artifacts, manifest.digest)
            assert data["status"] == "complete"
            assert data["exhaustive"] is True
            assert [r["repo_id"] for r in data["repositories"]] == [1, 2, 3]
            assert all(r["commit_sha"] == "a" * 40 for r in data["repositories"])
            assert all(r["H-R05"] == "not_evaluated" for r in data["repositories"])
            assert all(r["readme"] == "not_evaluated" for r in data["repositories"])
            assert len([c for c in calls if "/commits/" in c]) == 3
            assert len(state.repositories(run_id)) == 3
            assert data["queries"][0]["response_artifact"]
            assert data["config_hash"] == stable_digest(cfg.model_dump(mode="json"))
        count = len(calls)
        with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
            replayed = DiscoveryService(
                GitHubClient(cfg.discovery, http=http), state, artifacts
            ).discover(run_id, cfg)
            assert replayed.digest == manifest.digest
            assert len(calls) == count
            changed = cfg.model_copy(update={"workspace": tmp_path / "other"})
            with pytest.raises(ValueError, match="configuration"):
                DiscoveryService(GitHubClient(cfg.discovery, http=http), state, artifacts).discover(
                    run_id, changed
                )


@pytest.mark.parametrize("overflow,incomplete", [(1001, False), (1, True)])
def test_split_before_pagination_and_terminal_one_second(
    tmp_path: Path,
    overflow: int,
    incomplete: bool,
) -> None:
    cfg = configuration(tmp_path)
    artifacts = ArtifactStore(tmp_path / "objects")
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.params["page"] == "1"
        return httpx.Response(
            200, json={"total_count": overflow, "incomplete_results": incomplete, "items": []}
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
            result = DiscoveryService(
                GitHubClient(cfg.discovery, http=http), state, artifacts
            ).discover(None, cfg)
    data = load_manifest(artifacts, result.digest)
    assert len(calls) == 15  # five roots, each with two terminal one-second children
    assert data["status"] == "partial"
    assert data["exhaustive"] is False
    assert sum(p["status"] == "split" for p in data["partitions"]) == 5
    assert sum(p["status"] == "partial" for p in data["partitions"]) == 10
    assert "minimum_window" in {w["code"] for w in data["warnings"]}


def test_split_1001_collects_all_children_without_truncating(tmp_path: Path) -> None:
    cfg = configuration(tmp_path)
    artifacts = ArtifactStore(tmp_path / "objects")

    def handle(request: httpx.Request) -> httpx.Response:
        if "/commits/" in request.url.path:
            return httpx.Response(200, json={"sha": "b" * 40})
        query = request.url.params["q"]
        root = "00:00:00Z..2020-01-01T00:00:01Z" in query
        mit = "license:MIT " in query
        id = 1 if "00:00:00Z.." in query else 2
        return httpx.Response(
            200,
            json={
                "total_count": 1001 if root and mit else int(mit),
                "incomplete_results": False,
                "items": [repository(id)] if mit and not root else [],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
            result = DiscoveryService(
                GitHubClient(cfg.discovery, http=http), state, artifacts
            ).discover(None, cfg)
    data = load_manifest(artifacts, result.digest)
    assert data["exhaustive"] is True
    assert [r["repo_id"] for r in data["repositories"]] == [1, 2]


def test_commit_failure_is_partial_not_h_r05_rejection(tmp_path: Path) -> None:
    cfg = configuration(tmp_path)
    artifacts = ArtifactStore(tmp_path / "objects")

    def handle(request: httpx.Request) -> httpx.Response:
        if "/commits/" in request.url.path:
            return httpx.Response(404, json={"message": "inaccessible"})
        return httpx.Response(
            200, json={"total_count": 1, "incomplete_results": False, "items": [repository(1)]}
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
            result = DiscoveryService(
                GitHubClient(cfg.discovery, http=http), state, artifacts
            ).discover(None, cfg)
            assert (
                state.database.connection.execute("SELECT count(*) FROM assessments").fetchone()[0]
                == 0
            )
    data = load_manifest(artifacts, result.digest)
    assert data["status"] == "partial"
    repo = data["repositories"][0]
    assert repo["commit_sha"] is None
    assert repo["status"] == "inaccessible"
    assert repo["H-R05"] == "not_evaluated"


def test_resume_after_response_checkpoint_without_repeating_http(tmp_path: Path) -> None:
    cfg = configuration(tmp_path)
    artifacts = ArtifactStore(tmp_path / "objects")
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "/commits/" in request.url.path:
            return httpx.Response(200, json={"sha": "c" * 40})
        return httpx.Response(
            200, json={"total_count": 1, "incomplete_results": False, "items": [repository(1)]}
        )

    class CrashState(StateStore):
        def upsert_repository(
            self, run_id: str, external_id: int, full_name: str, commit_sha: str | None
        ) -> str:
            raise InterruptedError("simulated process crash")

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with CrashState(tmp_path / "state.db", artifacts=artifacts) as state:
            run_id = state.create_run(
                stable_digest(cfg.model_dump(mode="json")), config_json=cfg.model_dump_json()
            )
            with pytest.raises(InterruptedError):
                DiscoveryService(GitHubClient(cfg.discovery, http=http), state, artifacts).discover(
                    run_id, cfg
                )
        before = len(calls)
        with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
            result = DiscoveryService(
                GitHubClient(cfg.discovery, http=http), state, artifacts
            ).discover(run_id, cfg)
    assert load_manifest(artifacts, result.digest)["status"] == "complete"
    assert len(calls) == before + 4  # only the other license queries remain


def test_state_checkpoints_evidence_fk_and_identity(tmp_path: Path) -> None:
    artifacts = ArtifactStore(tmp_path / "objects")
    with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
        a, b = state.create_run("a"), state.create_run("b")
        first = state.upsert_repository(a, 1, "old/name", None)
        assert state.upsert_repository(a, 1, "new/name", "a" * 40) == first
        assert state.upsert_repository(a, 2, "new/name", None) != first
        assert state.upsert_repository(b, 1, "new/name", None) != first
        with pytest.raises(sqlite3.IntegrityError):
            state.upsert_repository("missing", 1, "x/y", None)
        with pytest.raises(sqlite3.IntegrityError):
            state.record_discovery_checkpoint(a, "response", "0" * 64)
        digest = state.register_artifact(artifacts.put(b"{}"))
        state.record_discovery_checkpoint(a, "response", digest)
        assert state.discovery_checkpoints(a) == {"response": digest}
        assert state.discovery_checkpoints(b) == {}
        other = state.register_artifact(artifacts.put(b"[]"))
        with pytest.raises(ValueError):
            state.record_discovery_checkpoint(a, "response", other)


def test_replay_validates_referenced_evidence(tmp_path: Path) -> None:
    artifacts = ArtifactStore(tmp_path / "objects")
    missing = "0" * 64
    digest = artifacts.put(
        json.dumps({"schema_version": "1.0", "kind": "discovery", "artifacts": [missing]}).encode()
    )
    with pytest.raises(FileNotFoundError):
        load_manifest(artifacts, digest)


def test_hundred_item_page_and_changing_total(tmp_path: Path) -> None:
    cfg = configuration(tmp_path, per_page=100)
    artifacts = ArtifactStore(tmp_path / "objects")
    pages = []

    def handle(request: httpx.Request) -> httpx.Response:
        if "/commits/" in request.url.path:
            return httpx.Response(200, json={"sha": "a" * 40})
        if "license:MIT " not in request.url.params["q"]:
            return httpx.Response(
                200, json={"total_count": 0, "incomplete_results": False, "items": []}
            )
        page = int(request.url.params["page"])
        pages.append(page)
        assert request.url.params["per_page"] == "100"
        return httpx.Response(
            200,
            json={
                "total_count": 100 if page == 1 else 101,
                "incomplete_results": False,
                "items": [repository(i) for i in range(1, 101)] if page == 1 else [repository(101)],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
            result = DiscoveryService(
                GitHubClient(cfg.discovery, http=http), state, artifacts
            ).discover(None, cfg)
    data = load_manifest(artifacts, result.digest)
    assert data["exhaustive"] is True
    assert len(data["repositories"]) == 101
    assert pages == [1, 2]


def test_rate_limit_commit_failure_is_unresolved_with_observed_ref(tmp_path: Path) -> None:
    cfg = configuration(tmp_path, transport_attempts=1)
    artifacts = ArtifactStore(tmp_path / "objects")

    def handle(request: httpx.Request) -> httpx.Response:
        if "/commits/" in request.url.path:
            return httpx.Response(403, headers={"Retry-After": "60"})
        return httpx.Response(
            200, json={"total_count": 1, "incomplete_results": False, "items": [repository(1)]}
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
            result = DiscoveryService(
                GitHubClient(cfg.discovery, http=http), state, artifacts
            ).discover(None, cfg)
    data = load_manifest(artifacts, result.digest)
    repo = data["repositories"][0]
    assert repo["status"] == "unresolved"
    assert repo["commit_ref"] == "main"
    assert repo["commit_response"] in data["artifacts"]
    assert repo["error_stage"] == "commit"
    assert repo["H-R05"] == "not_evaluated"


def test_missing_search_metadata_uses_repository_id_endpoint(tmp_path: Path) -> None:
    cfg = configuration(tmp_path)
    artifacts = ArtifactStore(tmp_path / "objects")
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if "/commits/" in request.url.path:
            assert request.url.path == "/repos/renamed/repo/commits/main"
            return httpx.Response(200, json={"sha": "d" * 40})
        if request.url.path == "/repositories/1":
            return httpx.Response(200, json=repository(1, "renamed/repo"))
        return httpx.Response(
            200, json={"total_count": 1, "incomplete_results": False, "items": [{"id": 1}]}
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
            result = DiscoveryService(
                GitHubClient(cfg.discovery, http=http), state, artifacts
            ).discover(None, cfg)
    repo = load_manifest(artifacts, result.digest)["repositories"][0]
    assert repo["status"] == "complete"
    assert repo["url"] == repository(1)["html_url"]
    assert repo["license"] == {"spdx_id": "MIT"}
    assert repo["stars"] == 7
    assert calls.count("/repositories/1") == 1


def test_failed_search_is_terminal_partial_and_replayed_offline(tmp_path: Path) -> None:
    cfg = configuration(tmp_path)
    artifacts = ArtifactStore(tmp_path / "objects")
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(403, json={"message": "permission denied"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
            service = DiscoveryService(GitHubClient(cfg.discovery, http=http), state, artifacts)
            result = service.discover(None, cfg)
            data = load_manifest(artifacts, result.digest)
            assert data["status"] == "partial"
            assert data["exhaustive"] is False
            assert all(p["status"] == "partial" for p in data["partitions"])
            assert all(q["error"] == "http_permanent" for q in data["queries"])
            assert len(calls) == 5
            assert service.discover(result.run_id, cfg) == result
            assert len(calls) == 5


def test_short_page_below_observed_total_reports_partial(tmp_path: Path) -> None:
    cfg = configuration(tmp_path)
    artifacts = ArtifactStore(tmp_path / "objects")

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"total_count": 5, "incomplete_results": False, "items": []}
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
            result = DiscoveryService(
                GitHubClient(cfg.discovery, http=http), state, artifacts
            ).discover(None, cfg)
    data = load_manifest(artifacts, result.digest)
    assert data["status"] == "partial"
    assert {w["code"] for w in data["warnings"]} == {"pagination_shortfall"}


def test_duplicate_pages_require_unique_ids_per_partition(tmp_path: Path) -> None:
    cfg = configuration(tmp_path)
    artifacts = ArtifactStore(tmp_path / "objects")

    def handle(request: httpx.Request) -> httpx.Response:
        if "/commits/" in request.url.path:
            return httpx.Response(200, json={"sha": "a" * 40})
        query, page = request.url.params["q"], int(request.url.params["page"])
        if "license:MIT " in query:
            ids, total = {1: [1, 2], 2: [2, 3], 3: []}[page], 4
        else:
            # ID 4 is globally known but must not fill the MIT partition's gap.
            ids, total = ([4] if page == 1 else []), 1
        return httpx.Response(
            200,
            json={
                "total_count": total,
                "incomplete_results": False,
                "items": [repository(id) for id in ids],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
            result = DiscoveryService(
                GitHubClient(cfg.discovery, http=http), state, artifacts
            ).discover(None, cfg)
    data = load_manifest(artifacts, result.digest)
    assert data["status"] == "partial"
    assert data["exhaustive"] is False
    mit = next(p for p in data["partitions"] if p["license"] == "MIT")
    assert mit["status"] == "partial"
    assert mit["unique_ids"] == 3
    assert mit["duplicate_items"] == 1
    assert mit["observed_totals"] == [4, 4, 4]
    shortfall = next(w for w in data["warnings"] if w["code"] == "pagination_shortfall")
    assert shortfall["seen"] == 3 and shortfall["total"] == 4
    assert [r["repo_id"] for r in data["repositories"]] == [1, 2, 3, 4]
    assert len([q for q in data["queries"] if q["partition_id"] == mit["id"]]) == 3


def test_decreasing_total_cannot_hide_partition_shortfall(tmp_path: Path) -> None:
    cfg = configuration(tmp_path)
    artifacts = ArtifactStore(tmp_path / "objects")

    def handle(request: httpx.Request) -> httpx.Response:
        if "/commits/" in request.url.path:
            return httpx.Response(200, json={"sha": "a" * 40})
        page = int(request.url.params["page"])
        return httpx.Response(
            200,
            json={
                "total_count": 4 if page == 1 else 2,
                "incomplete_results": False,
                "items": [repository(1), repository(2)] if page == 1 else [],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
            result = DiscoveryService(
                GitHubClient(cfg.discovery, http=http), state, artifacts
            ).discover(None, cfg)
    data = load_manifest(artifacts, result.digest)
    assert data["exhaustive"] is False
    assert all(p["observed_totals"] == [4, 2] for p in data["partitions"])
    assert all(p["status"] == "partial" for p in data["partitions"])
