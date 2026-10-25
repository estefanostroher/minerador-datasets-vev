import base64
import hashlib
import io
import json
import tarfile
from pathlib import Path

import httpx
import pytest

from robo_minerador.config import DiscoveryConfig, ExecutionConfig, MiningConfig
from robo_minerador.discovery.github import GitHubClient, GitHubError
from robo_minerador.identity import stable_digest
from robo_minerador.screening.service import ScreeningService, load_report
from robo_minerador.screening.snapshots import (
    Repository,
    SnapshotError,
    capture_snapshot,
    load_snapshot,
)
from robo_minerador.state.artifacts import ArtifactStore
from robo_minerador.state.store import StateStore

SHA = "a" * 40
ISC = b"""ISC License

Copyright (c) 2026 Fixture Authors

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
"""


def repository() -> Repository:
    return Repository(
        repo_id=123,
        state_id="manifest-state-id",
        full_name="owner/project",
        url="https://github.com/owner/project",
        commit_sha=SHA,
        default_branch="main",
        stars=0,
    )


def archive(files: dict[str, bytes], links: dict[str, str] | None = None) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo("owner-project-prefix/" + name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        for name, target in (links or {}).items():
            info = tarfile.TarInfo("owner-project-prefix/" + name)
            info.type, info.linkname = tarfile.SYMTYPE, target
            tar.addfile(info)
    return output.getvalue()


def client(http: httpx.Client, **kwargs: object) -> GitHubClient:
    return GitHubClient(
        DiscoveryConfig(created_from="2020-01-01", created_until="2020-01-02"),
        http=http,
        **kwargs,
    )


def test_sha_download_redirect_no_token_and_offline_reopen(tmp_path: Path) -> None:
    raw = archive(
        {"src/a.ts": b"\xef\xbb\xbfexport const a = 1;\r\n", "data": b"\xff\x00"},
        {"src/alias.ts": "a.ts"},
    )
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.host == "api.github.com":
            assert request.url.path == f"/repos/owner/project/tarball/{SHA}"
            assert request.headers["authorization"] == "Bearer secret"
            return httpx.Response(
                302,
                headers={
                    "Location": f"https://codeload.github.com/owner/project/legacy.tar.gz/{SHA}"
                },
            )
        assert "authorization" not in request.headers
        return httpx.Response(200, content=raw)

    objects = ArtifactStore(tmp_path / "objects")
    with httpx.Client(
        transport=httpx.MockTransport(handle), headers={"Authorization": "Bearer ambient"}
    ) as http:
        response = client(http, token="secret").get_archive("owner/project", SHA, max_bytes=100_000)
        snapshot = capture_snapshot(repository(), response, objects, max_bytes=100_000)
    assert len(calls) == 2
    assert snapshot.archive_sha256 == hashlib.sha256(raw).hexdigest()
    assert snapshot.repository.commit_sha == SHA
    assert snapshot.tree_sha256 != snapshot.archive_sha256
    restored = load_snapshot(ArtifactStore(objects.root), snapshot.manifest_sha256)
    assert restored == snapshot
    assert restored.read("src/a.ts", objects) == b"\xef\xbb\xbfexport const a = 1;\r\n"
    assert restored.read("data", objects) == b"\xff\x00"
    link = next(f for f in restored.files if f.path == "src/alias.ts")
    assert link.target == "a.ts"
    assert link.target_sha256 == hashlib.sha256(restored.read("src/a.ts", objects)).hexdigest()
    assert objects.get(snapshot.archive_sha256) == raw
    assert capture_snapshot(repository(), response, objects, max_bytes=100_000) == snapshot


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.test/x",
        "http://codeload.github.com/x",
        "https://api.github.com.evil.test/x",
        "https://user@codeload.github.com/x",
        "https://codeload.github.com:invalid/x",
    ],
)
def test_reject_untrusted_redirect(location: str) -> None:
    calls = []
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: calls.append(r) or httpx.Response(302, headers={"Location": location})
        )
    ) as http:
        with pytest.raises(GitHubError):
            client(http, token="secret").get_archive("owner/project", SHA, max_bytes=100_000)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "name", ["../escape", "/absolute", "a/../../escape", "C:/drive", "a\\escape"]
)
def test_tar_paths_are_rejected(tmp_path: Path, name: str) -> None:
    with pytest.raises(SnapshotError):
        capture_bytes(tmp_path, archive({name: b"bad"}))
    assert not (tmp_path / "escape").exists()


def capture_bytes(tmp_path: Path, raw: bytes, **limits: int):
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=raw))
    ) as http:
        response = client(http).get_archive("owner/project", SHA, max_bytes=100_000)
    return capture_snapshot(
        repository(),
        response,
        ArtifactStore(tmp_path / "objects"),
        max_bytes=limits.get("max_bytes", 100_000),
        max_entries=limits.get("max_entries", 100),
    )


@pytest.mark.parametrize("target", ["../../escape", "/outside", "missing", "loop"])
def test_external_dangling_and_cyclic_symlinks_rejected(tmp_path: Path, target: str) -> None:
    with pytest.raises(SnapshotError, match="symlink"):
        capture_bytes(tmp_path, archive({"source.ts": b"x"}, {"loop": target}))


def test_corrupt_archive_and_limits(tmp_path: Path) -> None:
    with pytest.raises(SnapshotError):
        capture_bytes(tmp_path, b"not a tarball")
    with pytest.raises(SnapshotError, match="limit"):
        capture_bytes(tmp_path, archive({"a": b"a", "b": b"b"}), max_entries=1)
    with pytest.raises(SnapshotError, match="limit"):
        capture_bytes(tmp_path, archive({"a": b"a" * 20_000}), max_bytes=1000)


def test_archive_transport_limit_and_retry_observer() -> None:
    calls, evidence, waits = [], [], []
    responses = [httpx.Response(503), httpx.Response(200, content=b"x" * 11)]
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: calls.append(r) or responses.pop(0))
    ) as http:
        with pytest.raises(GitHubError):
            client(http, on_response=evidence.append, sleep=waits.append).get_archive(
                "owner/project", SHA, max_bytes=10
            )
    assert len(calls) == 2
    assert waits == [1]
    assert evidence[-1]["error"] == "response_too_large"
    assert evidence[-1]["status"] == 200


def test_license_endpoint_is_commit_scoped_and_keeps_original_base64() -> None:
    body = b"Original attribution\r\n\xff"
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "path": "LICENSE",
                "encoding": "base64",
                "content": base64.b64encode(body).decode(),
                "license": {"spdx_id": "MIT"},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        result = client(http).get_license("owner/project", SHA)
    assert calls[0].url.params["ref"] == SHA
    assert base64.b64decode(result.data["content"]) == body
    assert (
        json.loads(base64.b64decode(result.envelope["body_base64"]))["content"]
        == result.data["content"]
    )


def test_submodule_is_unsupported_context(tmp_path: Path) -> None:
    snapshot = capture_bytes(
        tmp_path,
        archive(
            {
                ".gitmodules": b'[submodule "lib"]\npath = vendor/lib\nurl = https://example.test/lib\n'
            }
        ),
    )
    assert snapshot.context_status == "unsupported_context"
    assert not any(f.path == "vendor/lib" for f in snapshot.files)


def test_service_persists_static_assessments_and_replays_without_http(tmp_path: Path) -> None:
    license_body = ISC.replace(b"\n", b"\r\n")
    raw = archive(
        {
            "LICENSE": license_body,
            "package.json": b'{"scripts":{"test":"jest"},"devDependencies":{"jest":"*"}}',
            "src/a.ts": b"export function a() {}",
        }
    )
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if "/tarball/" in request.url.path:
            return httpx.Response(200, content=raw)
        assert request.url.params["ref"] == SHA
        return httpx.Response(
            200,
            json={
                "type": "file",
                "path": "LICENSE",
                "encoding": "base64",
                "content": base64.b64encode(license_body).decode(),
                "license": {"spdx_id": "ISC"},
            },
        )

    artifacts = ArtifactStore(tmp_path / "objects")
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
            run_id = state.create_run("config")
            state_id = state.upsert_repository(run_id, 123, "owner/project", SHA)
            repo = repository().model_copy(update={"state_id": state_id})
            report = ScreeningService(client(http), state, artifacts).screen(
                run_id, repo, ExecutionConfig()
            )
            data = load_report(artifacts, report.digest)
            decisions = {
                a.assessment.criterion_id: a.assessment for a in state.assessments(report.task_id)
            }
            assert decisions["H-R01"].status == "approved"
            assert decisions["H-R02"].status == "approved"
            assert decisions["H-R03"].status == "not_evaluated"
            assert decisions["H-R04"].status == "not_evaluated"
            assert decisions["H-R05"].status == "not_evaluated"
            assert state.get_task(report.task_id).entity_id == state_id
            assert data["readme"] == "absent"
            assert data["repository"]["stars"] == 0
            assert data["commands"]["status"] == "planned"
            assert data["eligible"] is False
            assert data["execution"] == "not_evaluated"
        with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
            replay = ScreeningService(client(http), state, artifacts).screen(
                run_id, repo, ExecutionConfig()
            )
            assert replay == report
            assert len(calls) == 2
            snapshot = load_snapshot(artifacts, data["snapshot_manifest"])
            assert snapshot.read("LICENSE", artifacts) == license_body


@pytest.mark.parametrize(
    "license_status,has_license,expected_status,reason",
    [
        (404, False, "rejected", "license_missing"),
        (503, True, "not_evaluated", "license_unverifiable"),
        (404, True, "not_evaluated", "license_unverifiable"),
    ],
)
def test_service_license_failure_states(
    tmp_path: Path, license_status: int, has_license: bool, expected_status: str, reason: str
) -> None:
    raw = archive({"LICENSE": b"license"} if has_license else {"src/a.ts": b"code"})

    def handle(request: httpx.Request) -> httpx.Response:
        return (
            httpx.Response(200, content=raw)
            if "/tarball/" in request.url.path
            else httpx.Response(license_status)
        )

    artifacts = ArtifactStore(tmp_path / "objects")
    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as http,
        StateStore(tmp_path / "state.db", artifacts=artifacts) as state,
    ):
        run_id = state.create_run("config")
        sid = state.upsert_repository(run_id, 123, "owner/project", SHA)
        repo = repository().model_copy(update={"state_id": sid})
        report = ScreeningService(client(http, sleep=lambda _: None), state, artifacts).screen(
            run_id, repo, ExecutionConfig()
        )
        license_decision = next(
            a.assessment
            for a in state.assessments(report.task_id)
            if a.assessment.criterion_id == "H-R02"
        )
        assert (license_decision.status, license_decision.reason_code) == (expected_status, reason)


def test_service_corrupt_archive_retained_and_no_license_claim(tmp_path: Path) -> None:
    artifacts = ArtifactStore(tmp_path / "objects")
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"broken"))
        ) as http,
        StateStore(tmp_path / "state.db", artifacts=artifacts) as state,
    ):
        run_id = state.create_run("config")
        sid = state.upsert_repository(run_id, 123, "owner/project", SHA)
        report = ScreeningService(client(http), state, artifacts).screen(
            run_id, repository().model_copy(update={"state_id": sid}), ExecutionConfig()
        )
        data = load_report(artifacts, report.digest)
        assert data["snapshot_manifest"] is None
        assert artifacts.get(data["archive_sha256"]) == b"broken"
        assert all(
            a.assessment.status == "not_evaluated" for a in state.assessments(report.task_id)
        )


def test_service_validates_manifest_state_identity_before_http(tmp_path: Path) -> None:
    artifacts = ArtifactStore(tmp_path / "objects")
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda _: pytest.fail("must not fetch"))
        ) as http,
        StateStore(tmp_path / "state.db", artifacts=artifacts) as state,
    ):
        run_id = state.create_run("config")
        with pytest.raises(ValueError, match="identity"):
            ScreeningService(client(http), state, artifacts).screen(
                run_id, repository(), ExecutionConfig()
            )


def test_archive_entirely_absolute_and_outside_prefix(tmp_path: Path) -> None:
    for names in [
        ["/absolute/file"],
        ["prefix/file", "other/file"],
        ["prefix/file", "prefix/file"],
    ]:
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as tar:
            for name in names:
                info = tarfile.TarInfo(name)
                info.size = 1
                tar.addfile(info, io.BytesIO(b"x"))
        with pytest.raises(SnapshotError):
            capture_bytes(tmp_path, output.getvalue())


def test_symlink_chain_and_directory_targets(tmp_path: Path) -> None:
    snapshot = capture_bytes(
        tmp_path,
        archive(
            {"src/deep/a.ts": b"original"},
            {"alias": "src", "a.ts": "b.ts", "b.ts": "src/deep/a.ts"},
        ),
    )
    assert (
        next(f for f in snapshot.files if f.path == "a.ts").target_sha256
        == hashlib.sha256(b"original").hexdigest()
    )
    assert next(f for f in snapshot.files if f.path == "alias").target_sha256


def test_entries_beneath_symlink_and_hardlinks_rejected(tmp_path: Path) -> None:
    with pytest.raises(SnapshotError):
        capture_bytes(
            tmp_path, archive({"src/a.ts": b"x", "alias/new.ts": b"bad"}, {"alias": "src"})
        )
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as tar:
        info = tarfile.TarInfo("root/hardlink")
        info.type, info.linkname = tarfile.LNKTYPE, "root/file"
        tar.addfile(info)
    with pytest.raises(SnapshotError):
        capture_bytes(tmp_path, output.getvalue())


def test_snapshot_read_detects_corrupt_file_artifact(tmp_path: Path) -> None:
    snapshot = capture_bytes(tmp_path, archive({"a.ts": b"original"}))
    objects = ArtifactStore(tmp_path / "objects")
    item = next(f for f in snapshot.files if f.path == "a.ts")
    (objects.root / item.sha256).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="digest mismatch"):
        load_snapshot(objects, snapshot.manifest_sha256)


def test_directory_link_hash_tracks_target_contents(tmp_path: Path) -> None:
    first = capture_bytes(tmp_path, archive({"dir/a": b"one"}, {"alias": "dir"}))
    second = capture_bytes(tmp_path, archive({"dir/a": b"two"}, {"alias": "dir"}))
    assert (
        next(f for f in first.files if f.path == "alias").target_sha256
        != next(f for f in second.files if f.path == "alias").target_sha256
    )


def test_snapshot_provenance_reopens_original_response(tmp_path: Path) -> None:
    snapshot = capture_bytes(tmp_path, archive({"a": b"x"}))
    objects = ArtifactStore(tmp_path / "objects")
    provenance = json.loads(objects.get(snapshot.response_artifact))
    assert provenance["endpoint"].endswith(SHA)
    assert provenance["status"] == 200
    assert (
        hashlib.sha256(base64.b64decode(provenance["body_base64"])).hexdigest()
        == snapshot.archive_sha256
    )


def test_internal_symlink_package_configuration_is_not_declared_absent(tmp_path: Path) -> None:
    snapshot = capture_bytes(
        tmp_path,
        archive(
            {"config/package.json": b'{"scripts":{"test":"jest"},"devDependencies":{"jest":"*"}}'},
            {"package.json": "config/package.json"},
        ),
    )
    from robo_minerador.screening.commands import plan_commands

    assert plan_commands(snapshot.contents(ArtifactStore(tmp_path / "objects"))).status == "planned"


def test_archive_http_error_redacts_token_and_preserves_status() -> None:
    evidence = []
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(403, content=b"secret"))
    ) as http:
        with pytest.raises(GitHubError):
            client(http, token="secret", on_response=evidence.append).get_archive(
                "owner/project", SHA, max_bytes=100
            )
    assert evidence[-1]["status"] == 403
    assert b"secret" not in base64.b64decode(evidence[-1]["body_base64"])


def test_archive_request_uses_identity_encoding_and_rejects_encoded_response() -> None:
    import gzip

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(
            200, content=gzip.compress(b"x" * 1000), headers={"Content-Encoding": "gzip"}
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(GitHubError):
            client(http).get_archive("owner/project", SHA, max_bytes=100)


def test_internal_root_directory_symlink_is_preserved(tmp_path: Path) -> None:
    snapshot = capture_bytes(tmp_path, archive({"a.ts": b"x"}, {"root-link": "."}))
    assert next(f for f in snapshot.files if f.path == "root-link").target == "."


def test_lexical_link_normalization_cannot_bypass_directory_symlink(tmp_path: Path) -> None:
    with pytest.raises(SnapshotError, match="symlink"):
        capture_bytes(
            tmp_path,
            archive(
                {"safe/deep/a.ts": b"x", "b.ts": b"different"},
                {"alias": "safe/deep", "link": "alias/../b.ts"},
            ),
        )


def test_rate_limited_archive_is_not_factual_access_rejection(tmp_path: Path) -> None:
    artifacts = ArtifactStore(tmp_path / "objects")
    with (
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    403, headers={"Retry-After": "1"}, json={"message": "rate limited"}
                )
            )
        ) as http,
        StateStore(tmp_path / "state.db", artifacts=artifacts) as state,
    ):
        run_id = state.create_run("config")
        sid = state.upsert_repository(run_id, 123, "owner/project", SHA)
        report = ScreeningService(client(http, sleep=lambda _: None), state, artifacts).screen(
            run_id, repository().model_copy(update={"state_id": sid}), ExecutionConfig()
        )
        access = next(
            a.assessment
            for a in state.assessments(report.task_id)
            if a.assessment.criterion_id == "H-R01"
        )
        assert access.status == "not_evaluated"
        assert access.reason_code == "snapshot_download_unverifiable"


def test_review_sparse_tar_cannot_multiply_source_byte_budget(tmp_path: Path) -> None:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for index in range(4):
            info = tarfile.TarInfo(f"root/sparse{index}.ts")
            info.size = 1
            info.pax_headers = {"GNU.sparse.map": "0,1", "GNU.sparse.size": "60000"}
            tar.addfile(info, io.BytesIO(b"x"))
    raw = output.getvalue()
    assert len(raw) == 10240
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tar:
        assert sum(member.size for member in tar) == 240000
    with pytest.raises(SnapshotError, match="unsupported sparse"):
        capture_bytes(tmp_path, raw, max_bytes=100000)
    objects = ArtifactStore(tmp_path / "objects")
    assert objects.get(hashlib.sha256(raw).hexdigest()) == raw
    expanded_digest = hashlib.sha256(b"x" + b"\x00" * 59999).hexdigest()
    with pytest.raises(FileNotFoundError):
        objects.get(expanded_digest)


def frozen_configuration(tmp_path: Path) -> MiningConfig:
    return MiningConfig(
        workspace=tmp_path / "coleta-ç",
        environment_catalog=tmp_path / "catalog.toml",
        methodology_files=[tmp_path / "method.md"],
        discovery=DiscoveryConfig(created_from="2020-01-01", created_until="2020-01-02"),
        execution=ExecutionConfig(workspace_limit_mb=16),
    )


def screening_http_fixture(calls: list[httpx.Request], files: dict[str, bytes]) -> httpx.Client:
    raw = archive(files)

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if "/tarball/" in request.url.path:
            assert request.url.path == f"/repos/owner/project/tarball/{SHA}"
            return httpx.Response(200, content=raw)
        assert request.url.path == "/repos/owner/project/license"
        assert request.url.params["ref"] == SHA
        return httpx.Response(
            200,
            json={
                "path": "LICENSE",
                "encoding": "base64",
                "type": "file",
                "content": base64.b64encode(ISC).decode(),
                "license": {"spdx_id": "ISC"},
            },
        )

    return httpx.Client(transport=httpx.MockTransport(handle))


@pytest.mark.parametrize("change", [{"workspace_limit_mb": 32}, {"memory_mb": 2048}])
def test_review_screening_rejects_changed_frozen_execution_before_effects(
    tmp_path: Path,
    change: dict[str, int],
) -> None:
    config = frozen_configuration(tmp_path)
    model = config.model_dump(mode="json")
    artifacts = ArtifactStore(tmp_path / "objects")
    calls: list[httpx.Request] = []
    with (
        screening_http_fixture(calls, {"LICENSE": ISC}) as http,
        StateStore(tmp_path / "state.db", artifacts=artifacts) as state,
    ):
        run_id = state.create_run(stable_digest(model), config_json=config.model_dump_json())
        sid = state.upsert_repository(run_id, 123, "owner/project", SHA)
        before = state.events(run_id)
        changed = config.execution.model_copy(update=change)
        with pytest.raises(ValueError, match="execution.*immutable"):
            ScreeningService(client(http), state, artifacts).screen(
                run_id,
                repository().model_copy(update={"state_id": sid}),
                changed,
            )
        assert calls == []
        assert state.events(run_id) == before  # No task/assessment/artifact registration.
        assert not artifacts.root.exists()


@pytest.mark.parametrize("legacy", [False, True])
def test_review_frozen_and_legacy_screening_preserve_replay_validation(
    tmp_path: Path,
    legacy: bool,
) -> None:
    config = frozen_configuration(tmp_path)
    model = config.model_dump(mode="json")
    artifacts = ArtifactStore(tmp_path / "objects")
    calls: list[httpx.Request] = []
    with screening_http_fixture(
        calls,
        {
            "LICENSE": ISC,
            "package.json": b'{"scripts":{"test":"jest"},"devDependencies":{"jest":"*"}}',
        },
    ) as http:
        with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
            run_id = state.create_run(
                "config" if legacy else stable_digest(model),
                config_json=None if legacy else config.model_dump_json(),
            )
            sid = state.upsert_repository(run_id, 123, "owner/project", SHA)
            repo = repository().model_copy(update={"state_id": sid})
            report = ScreeningService(client(http), state, artifacts).screen(
                run_id,
                repo,
                config.execution,
            )
        assert len(calls) == 2
        with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
            before = state.events(run_id)
            service = ScreeningService(client(http), state, artifacts)
            assert service.screen(run_id, repo, config.execution) == report
            with pytest.raises(ValueError, match="immutable"):
                service.screen(
                    run_id,
                    repo,
                    config.execution.model_copy(
                        update={"workspace_limit_mb": 32},
                    ),
                )
            assert len(calls) == 2
            assert state.events(run_id) == before


def test_review_screening_rejects_invalid_frozen_config_hash_before_effects(tmp_path: Path) -> None:
    config = frozen_configuration(tmp_path)
    artifacts = ArtifactStore(tmp_path / "objects")
    calls: list[httpx.Request] = []
    with (
        screening_http_fixture(calls, {"LICENSE": ISC}) as http,
        StateStore(tmp_path / "state.db", artifacts=artifacts) as state,
    ):
        run_id = state.create_run("wrong-hash", config_json=config.model_dump_json())
        sid = state.upsert_repository(run_id, 123, "owner/project", SHA)
        before = state.events(run_id)
        with pytest.raises(ValueError, match="configuration.*hash"):
            ScreeningService(client(http), state, artifacts).screen(
                run_id,
                repository().model_copy(update={"state_id": sid}),
                config.execution,
            )
        assert calls == []
        assert state.events(run_id) == before


@pytest.mark.parametrize("package", [None, b"{}"])
def test_review_missing_submodule_cannot_prove_absence_of_tests(
    tmp_path: Path,
    package: bytes | None,
) -> None:
    artifacts = ArtifactStore(tmp_path / "objects")
    calls: list[httpx.Request] = []
    contents = {
        "LICENSE": ISC,
        ".gitmodules": b'[submodule "tests"]\npath = tests\nurl = https://example.test/tests\n',
    }
    if package is not None:
        contents["package.json"] = package
    with (
        screening_http_fixture(calls, contents) as http,
        StateStore(tmp_path / "state.db", artifacts=artifacts) as state,
    ):
        run_id = state.create_run("config")
        sid = state.upsert_repository(run_id, 123, "owner/project", SHA)
        report = ScreeningService(client(http), state, artifacts).screen(
            run_id,
            repository().model_copy(update={"state_id": sid}),
            ExecutionConfig(),
        )
        decisions = {
            a.assessment.criterion_id: a.assessment for a in state.assessments(report.task_id)
        }
        assert decisions["H-R02"].status == "approved"
        for criterion in ("H-R03", "H-R04"):
            assert decisions[criterion].status == "not_evaluated"
            assert decisions[criterion].reason_code == "unsupported_context"
        assert decisions["H-R05"].status == "not_evaluated"
        assert state.get_task(report.task_id).outcome != "rejected"
        model = load_report(artifacts, report.digest)
        snapshot = load_snapshot(artifacts, model["snapshot_manifest"])
        assert snapshot.context_status == "unsupported_context"
        assert model["eligible"] is False
