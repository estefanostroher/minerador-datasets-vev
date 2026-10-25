"""T14 reference bytes and extraction checkpoint, without executing project code."""

import base64
import hashlib
import io
import json
import os
import tarfile
from dataclasses import replace

import httpx
import pytest

from robo_minerador.discovery.github import GitHubResponse
from robo_minerador.extraction import context
from robo_minerador.screening.snapshots import Repository, capture_snapshot
from robo_minerador.state.artifacts import ArtifactIntegrityError, ArtifactStore
from robo_minerador.state.store import StateStore


def archive(files, links=None, prefix="repo"):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as tar:
        for path, body in files.items():
            member = tarfile.TarInfo(prefix + "/" + path)
            member.size = len(body)
            member.mode = 0o755 if path.endswith(".sh") else 0o644
            tar.addfile(member, io.BytesIO(body))
        for path, target in (links or {}).items():
            member = tarfile.TarInfo(prefix + "/" + path)
            member.type = tarfile.SYMTYPE
            member.linkname = target
            member.mode = 0o777
            tar.addfile(member)
    return output.getvalue()


def snapshot(artifacts, files=None, links=None, repository=None):
    repository = repository or Repository(
        repo_id=1,
        state_id="repo",
        full_name="owner/repo",
        url="https://example/repo",
        commit_sha="a" * 40,
        default_branch="main",
    )
    raw = archive(files or fixture_files(), links)
    envelope = {
        "endpoint": f"/repos/{repository.full_name}/tarball/{repository.commit_sha}",
        "body_base64": base64.b64encode(raw).decode(),
        "error": None,
    }
    return capture_snapshot(
        repository,
        GitHubResponse(envelope),
        artifacts,
        max_bytes=1_000_000,
    )


def fixture_files():
    return {
        "package.json": b'{"workspaces":["packages/*"]}',
        "tsconfig.json": b'{"include":["**/*.ts"]}',
        "packages/local/index.ts": b"export const n = 1;\r\n",
        "main.ts": "\ufeff// 😀\r\nexport function f() { return 1; }\r\n".encode(),
        "main.test.ts": b"import { f } from './main';\r\nf();\r\n",
        "fixtures/data.bin": b"\x00\xff\x80\r\n",
        "run.sh": b"#!/bin/sh\nexit 0\n",
    }


def test_reference_roundtrip_bytes_modes_workspaces_and_utf16(tmp_path):
    artifacts = ArtifactStore(tmp_path / "cas")
    original = snapshot(artifacts)
    report = context.preserve_context(original, artifacts)
    reopened = context.load_context(artifacts, report.digest)
    destination = tmp_path / "restored"
    context.restore_context(reopened, artifacts, destination)
    assert {p: (destination / p).read_bytes() for p in fixture_files()} == fixture_files()
    assert reopened.manifest.snapshot_manifest == original.manifest_sha256
    binary = next(f for f in reopened.manifest.files if f.path.endswith("data.bin"))
    assert binary.encoding == "base64"
    assert base64.b64decode(binary.content) == fixture_files()[binary.path]
    assert next(f for f in reopened.manifest.files if f.path == "run.sh").mode == 0o755
    if os.name != "nt":
        assert (destination / "run.sh").stat().st_mode & 0o777 == 0o755
    body = fixture_files()["main.ts"]
    start = len(body.decode().split("export")[0].encode("utf-16-le")) // 2
    code = "export function f() { return 1; }"
    span = context.isolate_target(body, start, start + len(code), code)
    assert body[span.start_byte : span.end_byte] == code.encode()
    with pytest.raises(ValueError, match="target"):
        context.isolate_target(body, start, start + len(code), "wrong")
    with pytest.raises(ValueError):
        context.isolate_target(body, 5, 6, "")  # split a non-BMP surrogate pair


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_missing_or_corrupt_reference_never_restores_partial_tree(tmp_path, damage):
    artifacts = ArtifactStore(tmp_path / "cas")
    report = context.preserve_context(snapshot(artifacts), artifacts)
    entry = report.manifest.files[-1]
    stored = artifacts.root / entry.artifact
    if damage == "missing":
        stored.unlink()
    else:
        stored.write_bytes(b"corrupt")
    destination = tmp_path / "restored"
    with pytest.raises((FileNotFoundError, ArtifactIntegrityError)):
        context.restore_context(report, artifacts, destination)
    assert not destination.exists()


def test_unsupported_snapshot_never_approves_context(tmp_path):
    artifacts = ArtifactStore(tmp_path / "cas")
    original = snapshot(artifacts, {".gitmodules": b"submodule", "a.ts": b"export {}"})
    result = context.preserve_context(original, artifacts)
    assert result.manifest.status == "not_evaluated"
    assert "unsupported_context" in result.manifest.reasons


def test_restore_rejects_nonempty_and_symlinked_parents(tmp_path):
    artifacts = ArtifactStore(tmp_path / "cas")
    result = context.preserve_context(snapshot(artifacts), artifacts)
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "mine").write_bytes(b"mine")
    with pytest.raises(ValueError, match="empty"):
        context.restore_context(result, artifacts, occupied)
    assert (occupied / "mine").read_bytes() == b"mine"
    outside = tmp_path / "outside"
    outside.mkdir()
    parent = tmp_path / "symlink-parent"
    try:
        parent.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Windows account cannot create directory symlinks")
    with pytest.raises(ValueError, match="symlink"):
        context.restore_context(result, artifacts, parent / "restored")
    assert list(outside.iterdir()) == []


def test_internal_relative_chained_links_preserved(tmp_path):
    artifacts = ArtifactStore(tmp_path / "cas")
    links = {"alias": "main.ts", "chain": "alias", "packages/local/ref": "../../main.ts"}
    result = context.preserve_context(snapshot(artifacts, links=links), artifacts)
    assert {f.path: f.target for f in result.manifest.files if f.kind == "symlink"} == links
    try:
        probe = tmp_path / "probe"
        probe.symlink_to("target")
        probe.unlink()
    except OSError:
        pytest.skip("Windows account cannot create symlinks; manifest checks still run")
    if os.name == "nt":
        with pytest.raises(ValueError, match="POSIX symlink"):
            context.restore_context(result, artifacts, tmp_path / "restored")
        assert not (tmp_path / "restored").exists()
        # Native Windows-compatible links can still be restored byte-for-byte.
        native = context.preserve_context(
            snapshot(artifacts, links={"alias": "main.ts", "chain": "alias"}), artifacts
        )
        context.restore_context(native, artifacts, tmp_path / "native")
        assert (tmp_path / "native" / "chain").read_bytes() == fixture_files()["main.ts"]
        return
    context.restore_context(result, artifacts, tmp_path / "restored")
    for path, target in links.items():
        assert os.readlink(tmp_path / "restored" / path) == target
        assert (tmp_path / "restored" / path).read_bytes() == fixture_files()["main.ts"]
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "parent-link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        context.restore_context(result, artifacts, tmp_path / "parent-link" / "restore")


@pytest.mark.parametrize("target", ["../outside", "missing", "chain/../main.ts"])
def test_unverified_links_cannot_publish_context(tmp_path, target):
    artifacts = ArtifactStore(tmp_path / "cas")
    original = snapshot(artifacts)
    from robo_minerador.screening.snapshots import SnapshotFile

    link = SnapshotFile(
        "link", "symlink", 0, hashlib.sha256(target.encode()).hexdigest(), None, None, 0o777, target
    )
    original = replace(original, files=(*original.files, link))
    with pytest.raises(ValueError):
        context.preserve_context(original, artifacts)


@pytest.mark.parametrize("path", ["../escape", "MAIN.ts", "main.ts/child", "C:/escape"])
def test_forged_snapshot_paths_rejected_before_publication(tmp_path, path):
    artifacts = ArtifactStore(tmp_path / "cas")
    original = snapshot(artifacts)
    forged = replace(original.files[0], path=path)
    with pytest.raises(ValueError):
        context.preserve_context(replace(original, files=(*original.files, forged)), artifacts)


def test_link_walk_through_missing_directory_is_inconclusive(tmp_path):
    artifacts = ArtifactStore(tmp_path / "cas")
    original = snapshot(artifacts, links={"link": "missing/../main.ts"})
    with pytest.raises(ValueError, match="intermediate"):
        context.preserve_context(original, artifacts)


def test_manifest_tampering_and_unknown_versions_rejected(tmp_path):
    artifacts = ArtifactStore(tmp_path / "cas")
    result = context.preserve_context(snapshot(artifacts), artifacts)
    for change in ({"schema_version": "2.0"}, {"repository_sha": "b" * 40}):
        value = {**result.manifest.model_dump(mode="json"), **change}
        digest = artifacts.put(json.dumps(value).encode())
        with pytest.raises(ValueError):
            context.load_context(artifacts, digest)


def frozen_inputs(tmp_path, artifacts, state, run_id):
    from pathlib import Path

    from robo_minerador.config import ExecutionConfig
    from robo_minerador.environments.catalog import load_catalog
    from robo_minerador.environments.plan import persist_plan, plan_environment
    from robo_minerador.screening.commands import plan_commands

    files = {
        **fixture_files(),
        "package.json": json.dumps(
            {
                "scripts": {"test": "npm test --workspaces"},
                "devDependencies": {"vitest": "^3"},
                "config": {"robo_minerador": {"allow_lockfile_generation": True}},
                "workspaces": ["packages/*"],
            }
        ).encode(),
        "packages/local/package.json": (
            b'{"name":"local","version":"1.0.0","scripts":{"test":"vitest run"},'
            b'"devDependencies":{"vitest":"^3"}}'
        ),
    }
    state_id = state.upsert_repository(run_id, 1, "owner/repo", "a" * 40)
    repo = Repository(
        repo_id=1,
        state_id=state_id,
        full_name="owner/repo",
        url="https://example/repo",
        commit_sha="a" * 40,
        default_branch="main",
    )
    original = snapshot(artifacts, files, repository=repo)
    commands = plan_commands(files)
    result = plan_environment(files, commands, load_catalog(Path.cwd()), ExecutionConfig())
    assert result.plan is not None, result
    plan = result.plan
    plan_digest = persist_plan(plan, artifacts)
    from dataclasses import asdict

    screening = artifacts.put(
        json.dumps(
            {
                "kind": "screening",
                "schema_version": "1.0",
                "repository": repo.model_dump(),
                "snapshot_manifest": original.manifest_sha256,
                "artifacts": [],
                "commands": asdict(commands),
            }
        ).encode()
    )
    return original, plan, plan_digest, screening, files


def analysis_payload(request_id, raw):
    code = "export function f() { return 1; }"
    start = len(raw.decode().split("export")[0].encode("utf-16-le")) // 2
    target = {
        "id": "sha256:" + "d" * 64,
        "kind": "function",
        "qualified_name": "f",
        "path": "main.ts",
        "start_offset": start,
        "end_offset": start + len(code),
        "start_line": 2,
        "start_column": 1,
        "end_line": 2,
        "end_column": 34,
        "code": code,
        "signature": "export function f()",
    }
    return {
        "protocol_version": "1.0",
        "request_id": request_id,
        "status": "ok",
        "targets": [target],
        "test_calls": [
            {
                "symbol_target_id": target["id"],
                "callsite_path": "main.test.ts",
                "start_offset": 31,
                "end_offset": 34,
                "case_declaration_id": "sha256:" + "e" * 64,
                "case_title": "f works",
                "case_start_offset": 0,
                "parameterized": False,
                "link_kind": "direct",
                "reason": "resolved_symbol",
                "path_resolution": ["main.ts"],
                "runtime_confirmed": False,
                "mock_potential": True,
            }
        ],
        "documents": [
            {
                "symbol_target_id": target["id"],
                "status": "not_evaluated",
                "reason": "documentation_behavior_unresolved",
                "evidence": [],
                "sources": [],
            }
        ],
        "metrics": [
            {
                "symbol_target_id": target["id"],
                "status": "not_evaluated",
                "reason": "unsupported",
                "method": "cyclomatic-ts-v1",
                "cyclomatic_complexity": None,
                "physical_lines": 1,
                "decisions": [],
                "triviality": {"status": "not_evaluated", "reason": "unsupported", "evidence": []},
            }
        ],
        "limitations": [{"code": "unsupported", "message": "needs runtime"}],
        "errors": [],
    }


def test_extraction_http_retains_inconclusive_data_and_replays_offline(tmp_path):
    from robo_minerador.extraction.client import AnalysisClient
    from robo_minerador.extraction.service import ExtractionService, load_extraction

    artifacts = ArtifactStore(tmp_path / "cas")
    with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
        run_id = state.create_run("frozen")
        original, plan, plan_digest, screening, files = frozen_inputs(
            tmp_path,
            artifacts,
            state,
            run_id,
        )
        calls = []

        def handler(req):
            value = json.loads(req.content)
            calls.append(value)
            assert value["root"] == "/named-volume/snapshots/original"
            return httpx.Response(200, json=analysis_payload(value["request_id"], files["main.ts"]))

        with AnalysisClient(
            "http://analyzer:3000", transport=httpx.MockTransport(handler)
        ) as client:
            service = ExtractionService(client, state, artifacts)
            args = dict(
                run_id=run_id,
                snapshot=original,
                screening_digest=screening,
                plan=plan,
                plan_artifact=plan_digest,
                analyzer_root="/named-volume/snapshots/original",
                framework="vitest",
                tsconfig_paths=("tsconfig.json",),
            )
            report = service.extract(**args)
            assert service.extract(**args) == report
        assert len(calls) == 1
        reopened = load_extraction(artifacts, report.digest)
        candidate = reopened.candidates[0]
        assert (
            candidate.target.code.encode()
            == files["main.ts"][candidate.span.start_byte : candidate.span.end_byte]
        )
        assert candidate.documents[0].status == "not_evaluated"
        assert candidate.metrics[0].cyclomatic_complexity is None
        assert candidate.associations[0].case_declaration_id == "sha256:" + "e" * 64
        assert candidate.associations[0].runtime_confirmed is False
        assert candidate.test_files == ("main.test.ts",)
        assert reopened.eligibility.status == "not_evaluated"
        assert reopened.context_evaluation.status == "not_evaluated"
        assert reopened.analysis_artifact in state.task_evidence(report.task_id)
        inputs = json.loads(artifacts.get(reopened.inputs_artifact))
        assert inputs["root"] == "/named-volume/snapshots/original"
        assert state.get_task(report.task_id).outcome == "completed"
        assert state.assessments(report.task_id)[0].assessment.status == "not_evaluated"
        with pytest.raises(ValueError, match="immutable"):
            service.extract(**{**args, "analyzer_root": "/other/container/path"})
        tampered = reopened.model_dump(mode="json")
        tampered["request_hash"] = "0" * 64
        with pytest.raises(ValueError, match="inputs"):
            load_extraction(artifacts, artifacts.put(json.dumps(tampered).encode()))


def test_mismatched_target_is_auditable_not_a_factual_rejection(tmp_path):
    from robo_minerador.extraction.client import AnalysisClient
    from robo_minerador.extraction.service import ExtractionService, load_extraction

    artifacts = ArtifactStore(tmp_path / "cas")
    with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
        run_id = state.create_run("frozen")
        original, plan, plan_digest, screening, files = frozen_inputs(
            tmp_path,
            artifacts,
            state,
            run_id,
        )

        def handler(req):
            value = analysis_payload(json.loads(req.content)["request_id"], files["main.ts"])
            value["targets"][0]["code"] = "wrong"
            return httpx.Response(200, json=value)

        with AnalysisClient("http://analyzer", transport=httpx.MockTransport(handler)) as client:
            report = ExtractionService(client, state, artifacts).extract(
                run_id=run_id,
                snapshot=original,
                screening_digest=screening,
                plan=plan,
                plan_artifact=plan_digest,
                analyzer_root="/volume/repo",
                framework="vitest",
                tsconfig_paths=("tsconfig.json",),
            )
        model = load_extraction(artifacts, report.digest)
        assert model.candidates[0].span is None
        assert model.candidates[0].source_status.reason == "target_source_unverified"
        assert model.analysis_artifact
        assert model.eligibility.status == "not_evaluated"


def registry_entry(name="external", version="1.2.3", **changes):
    return {
        "version": version,
        "resolved": f"https://registry.npmjs.org/{name}/-/{name}-{version}.tgz",
        "integrity": "sha512-"
        + base64.b64encode(hashlib.sha512(b"registry fixture").digest()).decode(),
        **changes,
    }


def npm_lock(packages=None):
    return json.dumps(
        {
            "lockfileVersion": 3,
            "packages": {"node_modules/external": registry_entry()}
            if packages is None
            else packages,
        }
    ).encode()


def prepared_fixture(artifacts, original, plan, plan_artifact, files, links=None, overrides=None):
    from dataclasses import asdict

    from robo_minerador.execution.docker import ExecutionResult, PreparedEnvironment

    prepared_files = {
        **files,
        "package-lock.json": npm_lock(),
        "generated/output.bin": b"\xff\x00generated",
        "main.ts": files["main.ts"] + b"// installed output\r\n",
        "node_modules/external/index.js": b"module.exports = 42;",
        "node_modules/external/package.json": b'{"name":"external","version":"1.2.3"}',
    }
    prepared_files.update(overrides or {})
    workspace = artifacts.put(archive(prepared_files, links=links, prefix="workspace"))
    empty = artifacts.put(b"")
    observation = artifacts.put(b'{"verified":true}')
    command = ExecutionResult(
        container_id="install-container",
        image_id=plan.derived_image_id,
        argv=plan.install.argv,
        cwd=plan.install.cwd,
        environment=plan.environment,
        limits=plan.limits,
        timeout_seconds=plan.install.timeout_seconds,
        network_mode="none",
        phase="install",
        exit_code=0,
        signal=None,
        timed_out=False,
        oom_killed=False,
        elapsed_seconds=1.0,
        reason="completed",
        stdout_artifact=empty,
        stderr_artifact=empty,
        workspace_artifact=workspace,
        workspace_used_bytes=10,
        storage_policy="readonly-root+bounded-tmpfs-v1",
        storage_verified=True,
        storage_observation_artifact=observation,
        memory_oom_events=0,
        container_inspect_artifact=observation,
    )
    command = replace(
        command, evidence_artifact=artifacts.put(json.dumps(asdict(command)).encode())
    )
    prepared = PreparedEnvironment(
        prepared_id="prepared-id",
        image_id="sha256:" + "b" * 64,
        parent_image_id=plan.derived_image_id,
        base_ref=plan.base_ref,
        plan_digest=plan.digest,
        plan_artifact=plan_artifact,
        input_archive_artifact=original.archive_sha256,
        workspace_artifact=workspace,
        commands=(command,),
        install_network="none",
        storage_policy=command.storage_policy,
    )
    return replace(prepared, evidence_artifact=artifacts.put(json.dumps(asdict(prepared)).encode()))


def test_prepared_lock_and_generated_files_have_separate_provenance(tmp_path):
    artifacts = ArtifactStore(tmp_path / "cas")
    with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
        run_id = state.create_run("frozen")
        original, plan, plan_artifact, _, files = frozen_inputs(tmp_path, artifacts, state, run_id)
        prepared = prepared_fixture(artifacts, original, plan, plan_artifact, files)
        result = context.preserve_prepared_context(
            original,
            plan,
            prepared,
            artifacts,
            external_dependency_roots=("node_modules/external",),
        )
        reopened = context.load_prepared_context(artifacts, result.digest)
        assert reopened == result
        derived = {file.path: file for file in result.manifest.files}
        assert "node_modules/external/index.js" not in derived
        assert base64.b64decode(derived["generated/output.bin"].content) == b"\xff\x00generated"
        assert derived["package-lock.json"].sha256 == hashlib.sha256(npm_lock()).hexdigest()
        assert result.manifest.prepared_archive == prepared.workspace_artifact
        assert result.manifest.command_evidence == (prepared.commands[0].evidence_artifact,)
        assert result.manifest.plan_artifact == plan_artifact
        assert result.manifest.status == "not_evaluated"  # preservation is not runtime proof
        reference = context.preserve_context(original, artifacts)
        assert (
            next(f for f in reference.manifest.files if f.path == "main.ts").content.encode()
            == files["main.ts"]
        )
        assert derived["main.ts"].content.encode() != files["main.ts"]
        for bad in (
            replace(prepared, plan_digest="wrong"),
            replace(prepared, workspace_artifact=artifacts.put(b"corrupt tar")),
        ):
            with pytest.raises(ValueError):
                context.preserve_prepared_context(original, plan, bad, artifacts)


def test_prepared_missing_command_evidence_does_not_publish_success(tmp_path):
    artifacts = ArtifactStore(tmp_path / "cas")
    with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
        run_id = state.create_run("frozen")
        original, plan, plan_artifact, _, files = frozen_inputs(tmp_path, artifacts, state, run_id)
        prepared = prepared_fixture(artifacts, original, plan, plan_artifact, files)
        (artifacts.root / prepared.commands[0].evidence_artifact).unlink()
        with pytest.raises(FileNotFoundError):
            context.preserve_prepared_context(original, plan, prepared, artifacts)


def test_extraction_prepared_and_instrumentation_are_auditable_supplements(tmp_path):
    from robo_minerador.extraction.client import AnalysisClient
    from robo_minerador.extraction.instrumentation import InstrumentationClient
    from robo_minerador.extraction.service import ExtractionService, load_extraction

    artifacts = ArtifactStore(tmp_path / "cas")
    with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
        run_id = state.create_run("frozen")
        original, plan, plan_artifact, screening, files = frozen_inputs(
            tmp_path, artifacts, state, run_id
        )
        prepared = prepared_fixture(artifacts, original, plan, plan_artifact, files)
        calls = []

        def handler(req):
            value = json.loads(req.content)
            calls.append(req.url.path)
            if req.url.path == "/v1/analyze":
                return httpx.Response(
                    200, json=analysis_payload(value["request_id"], files["main.ts"])
                )
            assert value["root"] == "/analyzer-volume/reference"
            assert value["framework_version"] == "3.2.4"
            return httpx.Response(
                200,
                json={
                    "protocol_version": "1.0",
                    "request_id": value["request_id"],
                    "snapshot_id": value["snapshot_id"],
                    "status": "ok",
                    "manifest": {
                        "schema_version": "1.0",
                        "purpose": "observational_supplement",
                        "status": "not_evaluated",
                        "framework": "vitest",
                        "framework_version": "3.2.4",
                        "typescript_version": "5.8.3",
                        "files": [],
                        "declarations": [],
                        "targets": [],
                        "callsites": [],
                        "limitations": [{"code": "unsupported", "message": "unsupported fixture"}],
                    },
                    "supplements": {},
                    "errors": [],
                },
            )

        transport = httpx.MockTransport(handler)
        with (
            AnalysisClient("http://analyzer", transport=transport) as client,
            InstrumentationClient(
                "http://analyzer",
                transport=transport,
            ) as instrument,
        ):
            service = ExtractionService(client, state, artifacts, instrumentation=instrument)
            args = dict(
                run_id=run_id,
                snapshot=original,
                screening_digest=screening,
                plan=plan,
                plan_artifact=plan_artifact,
                analyzer_root="/analyzer-volume/reference",
                framework="vitest",
                tsconfig_paths=("tsconfig.json",),
                framework_version="3.2.4",
                prepared=prepared,
                external_dependency_roots=("node_modules/external",),
            )
            report = service.extract(**args)
            assert service.extract(**args) == report
        model = load_extraction(artifacts, report.digest)
        assert calls == ["/v1/analyze", "/v1/instrument"]
        assert model.instrumentation_artifact
        assert model.prepared_context_reference
        assert model.context_evaluation.reason == "prepared_bytes_preserved_reproduction_pending"
        assert model.context_evaluation.status == "not_evaluated"
        assert model.candidates[0].source_status.status == "approved"
        assert model.candidates[0].target.code.encode() in files["main.ts"]
        assert model.eligibility.status == "not_evaluated"
        assert "prepared_context_unavailable" not in model.limitations


@pytest.mark.parametrize(
    "lock",
    [
        b"NOTALOCK",
        b"[]",
        b'{"lockfileVersion":3,"packages":{}}',
        b'{"lockfileVersion":3,"packages":[]}',
        npm_lock({"node_modules/another": registry_entry(name="another")}),
        npm_lock({"node_modules/external": registry_entry(version="9.9.9")}),
        npm_lock({"node_modules/external": {**registry_entry(name="another"), "name": "another"}}),
        npm_lock({"node_modules/external": registry_entry(resolved="file:../packages/local")}),
        npm_lock({"node_modules/external": registry_entry(resolved="workspace:*")}),
        npm_lock({"node_modules/external": registry_entry(resolved="link:../packages/local")}),
        npm_lock({"node_modules/external": registry_entry(resolved="../packages/local")}),
        npm_lock({"node_modules/external": registry_entry(link=True)}),
        npm_lock({"node_modules/external": registry_entry(integrity="")}),
        npm_lock({"node_modules/external": registry_entry(integrity="sha512-NOTBASE64")}),
        npm_lock({"node_modules/external": registry_entry(resolved="")}),
        npm_lock({"node_modules/external": registry_entry(resolved="http://localhost/pkg.tgz")}),
        npm_lock({"node_modules/external": registry_entry(resolved="https://127.0.0.1/pkg.tgz")}),
        b'{"lockfileVersion":1,"packages":{}}',
    ],
)
def test_external_exclusion_requires_matching_npm_lock_proof(tmp_path, lock):
    artifacts = ArtifactStore(tmp_path / "cas")
    with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
        run_id = state.create_run("frozen")
        original, plan, plan_artifact, _, files = frozen_inputs(tmp_path, artifacts, state, run_id)
        prepared = prepared_fixture(
            artifacts,
            original,
            plan,
            plan_artifact,
            files,
            overrides={"package-lock.json": lock},
        )
        with pytest.raises(ValueError, match="dependency exclusion"):
            context.preserve_prepared_context(
                original,
                plan,
                prepared,
                artifacts,
                external_dependency_roots=("node_modules/external",),
            )
        # Invalid exclusion input never destroys the full prepared archive.
        with tarfile.open(fileobj=io.BytesIO(artifacts.get(prepared.workspace_artifact))) as tar:
            assert (
                tar.extractfile("workspace/node_modules/external/index.js").read()
                == b"module.exports = 42;"
            )


@pytest.mark.parametrize(
    "package",
    [
        b'{"name":"different","version":"1.2.3"}',
        b'{"name":"external","version":"2.0.0"}',
        b"NOTAPACKAGE",
    ],
)
def test_external_exclusion_checks_installed_package_identity(tmp_path, package):
    artifacts = ArtifactStore(tmp_path / "cas")
    with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
        run_id = state.create_run("frozen")
        original, plan, plan_artifact, _, files = frozen_inputs(tmp_path, artifacts, state, run_id)
        prepared = prepared_fixture(
            artifacts,
            original,
            plan,
            plan_artifact,
            files,
            overrides={"node_modules/external/package.json": package},
        )
        with pytest.raises(ValueError, match="dependency exclusion"):
            context.preserve_prepared_context(
                original,
                plan,
                prepared,
                artifacts,
                external_dependency_roots=("node_modules/external",),
            )


def test_external_exclusion_does_not_swallow_local_symlinks_or_unlocked_nested_packages(tmp_path):
    artifacts = ArtifactStore(tmp_path / "cas")
    with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
        run_id = state.create_run("frozen")
        original, plan, plan_artifact, _, files = frozen_inputs(tmp_path, artifacts, state, run_id)
        for changes in (
            {"links": {"node_modules/external/local": "../../packages/local"}},
            {
                "overrides": {
                    "node_modules/external/node_modules/other/package.json": (
                        b'{"name":"other","version":"2.0.0"}'
                    ),
                    "node_modules/external/node_modules/other/index.js": b"local payload",
                }
            },
        ):
            prepared = prepared_fixture(artifacts, original, plan, plan_artifact, files, **changes)
            with pytest.raises(ValueError, match="dependency exclusion"):
                context.preserve_prepared_context(
                    original,
                    plan,
                    prepared,
                    artifacts,
                    external_dependency_roots=("node_modules/external",),
                )


@pytest.mark.parametrize("manager", ["yarn", "pnpm"])
def test_external_exclusion_without_supported_lock_proof_is_inconclusive(tmp_path, manager):
    from robo_minerador.environments.plan import persist_plan

    artifacts = ArtifactStore(tmp_path / "cas")
    with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
        run_id = state.create_run("frozen")
        original, plan, _, _, files = frozen_inputs(tmp_path, artifacts, state, run_id)
        plan = replace(plan, manager_name=manager)
        plan_artifact = persist_plan(plan, artifacts)
        prepared = prepared_fixture(
            artifacts,
            original,
            plan,
            plan_artifact,
            files,
            overrides={"yarn.lock": b"lock", "pnpm-lock.yaml": b"lock"},
        )
        with pytest.raises(ValueError, match="dependency exclusion.*unsupported"):
            context.preserve_prepared_context(
                original,
                plan,
                prepared,
                artifacts,
                external_dependency_roots=("node_modules/external",),
            )


def test_npm_shrinkwrap_is_authoritative_over_package_lock(tmp_path):
    artifacts = ArtifactStore(tmp_path / "cas")
    with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
        run_id = state.create_run("frozen")
        original, plan, plan_artifact, _, files = frozen_inputs(tmp_path, artifacts, state, run_id)
        prepared = prepared_fixture(
            artifacts,
            original,
            plan,
            plan_artifact,
            files,
            overrides={"npm-shrinkwrap.json": b"NOTALOCK"},
        )
        with pytest.raises(ValueError, match="dependency exclusion"):
            context.preserve_prepared_context(
                original,
                plan,
                prepared,
                artifacts,
                external_dependency_roots=("node_modules/external",),
            )


@pytest.mark.parametrize(
    "filename,version",
    [
        ("package-lock.json", 2),
        ("package-lock.json", 3),
        ("npm-shrinkwrap.json", 3),
    ],
)
def test_matching_registry_proof_covers_each_excluded_package_path(tmp_path, filename, version):
    artifacts = ArtifactStore(tmp_path / "cas")
    with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
        run_id = state.create_run("frozen")
        original, plan, plan_artifact, _, files = frozen_inputs(tmp_path, artifacts, state, run_id)
        locked = json.loads(
            npm_lock(
                {
                    "node_modules/external": registry_entry(),
                    "node_modules/external/node_modules/other": registry_entry(
                        name="other", version="2.0.0"
                    ),
                }
            )
        )
        locked["lockfileVersion"] = version
        prepared = prepared_fixture(
            artifacts,
            original,
            plan,
            plan_artifact,
            files,
            overrides={
                filename: json.dumps(locked).encode(),
                "node_modules/external/node_modules/other/package.json": (
                    b'{"name":"other","version":"2.0.0"}'
                ),
                "node_modules/external/node_modules/other/index.js": b"external nested body",
            },
        )
        result = context.preserve_prepared_context(
            original,
            plan,
            prepared,
            artifacts,
            external_dependency_roots=("node_modules/external",),
        )
        assert (
            "node_modules/external/node_modules/other/index.js"
            in result.manifest.excluded_dependencies
        )
        assert context.load_prepared_context(artifacts, result.digest) == result
        assert artifacts.get(prepared.workspace_artifact)
        assert result.manifest.status == "not_evaluated"


def test_invalid_lock_exclusion_stays_not_evaluated_with_full_archive_retained(tmp_path):
    from robo_minerador.extraction.client import AnalysisClient
    from robo_minerador.extraction.service import ExtractionService, load_extraction

    artifacts = ArtifactStore(tmp_path / "cas")
    with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
        run_id = state.create_run("frozen")
        original, plan, plan_artifact, screening, files = frozen_inputs(
            tmp_path, artifacts, state, run_id
        )
        prepared = prepared_fixture(
            artifacts,
            original,
            plan,
            plan_artifact,
            files,
            overrides={"package-lock.json": b"NOTALOCK"},
        )

        def handler(req):
            return httpx.Response(
                200, json=analysis_payload(json.loads(req.content)["request_id"], files["main.ts"])
            )

        with AnalysisClient("http://analyzer", transport=httpx.MockTransport(handler)) as client:
            report = ExtractionService(client, state, artifacts).extract(
                run_id=run_id,
                snapshot=original,
                screening_digest=screening,
                plan=plan,
                plan_artifact=plan_artifact,
                analyzer_root="/analyzer/original",
                framework="vitest",
                tsconfig_paths=("tsconfig.json",),
                prepared=prepared,
                external_dependency_roots=("node_modules/external",),
            )
        manifest = load_extraction(artifacts, report.digest)
        assert manifest.context_evaluation.status == "not_evaluated"
        assert manifest.context_evaluation.reason == "prepared_context_unverified"
        assert manifest.prepared_context_reference is None
        assert len(manifest.candidates) == 1
        assert manifest.eligibility.status == "not_evaluated"
        failures = []
        for ref in manifest.artifacts:
            try:
                value = json.loads(artifacts.get(ref))
            except (UnicodeError, ValueError):
                continue
            if isinstance(value, dict) and value.get("kind") == "prepared_context_failure":
                failures.append(value)
        assert len(failures) == 1
        assert failures[0]["prepared_archive"] == prepared.workspace_artifact
        with tarfile.open(
            fileobj=io.BytesIO(artifacts.get(failures[0]["prepared_archive"]))
        ) as tar:
            assert (
                tar.extractfile("workspace/node_modules/external/index.js").read()
                == b"module.exports = 42;"
            )


def test_transport_failure_has_evidence_and_no_factual_reject(tmp_path):
    from robo_minerador.extraction.client import AnalysisClient
    from robo_minerador.extraction.service import ExtractionService, load_extraction

    artifacts = ArtifactStore(tmp_path / "cas")
    with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
        run_id = state.create_run("frozen")
        original, plan, plan_artifact, screening, _ = frozen_inputs(
            tmp_path, artifacts, state, run_id
        )

        def handler(req):
            raise httpx.ReadTimeout("private host path", request=req)

        with AnalysisClient("http://analyzer", transport=httpx.MockTransport(handler)) as client:
            report = ExtractionService(client, state, artifacts).extract(
                run_id=run_id,
                snapshot=original,
                screening_digest=screening,
                plan=plan,
                plan_artifact=plan_artifact,
                analyzer_root="/volume/reference",
                framework="vitest",
                tsconfig_paths=("tsconfig.json",),
            )
        model = load_extraction(artifacts, report.digest)
        assert model.candidates == ()
        assert model.eligibility.status == "not_evaluated"
        assert "analysis_unavailable" in model.limitations
        evidence = [artifacts.get(ref) for ref in state.task_evidence(report.task_id)]
        assert any(b"analysis_failure" in raw for raw in evidence)
        assert not any(b"private host path" in raw for raw in evidence)


def test_prepared_exclusions_need_explicit_classification_and_preserve_workspace_links(tmp_path):
    artifacts = ArtifactStore(tmp_path / "cas")
    with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
        run_id = state.create_run("frozen")
        original, plan, plan_artifact, _, files = frozen_inputs(tmp_path, artifacts, state, run_id)
        prepared = prepared_fixture(artifacts, original, plan, plan_artifact, files)
        with pytest.raises(ValueError, match="classification"):
            context.preserve_prepared_context(original, plan, prepared, artifacts)
        linked = prepared_fixture(
            artifacts,
            original,
            plan,
            plan_artifact,
            files,
            links={"node_modules/local": "../packages/local"},
        )
        result = context.preserve_prepared_context(
            original,
            plan,
            linked,
            artifacts,
            external_dependency_roots=("node_modules/external",),
        )
        local = next(f for f in result.manifest.files if f.path == "node_modules/local")
        assert local.target == "../packages/local"
        assert local.target_sha256
        with pytest.raises(ValueError, match="whole node_modules"):
            context.preserve_prepared_context(
                original,
                plan,
                linked,
                artifacts,
                external_dependency_roots=("node_modules",),
            )
        with pytest.raises(ValueError, match="workspace symlinks"):
            context.preserve_prepared_context(
                original,
                plan,
                linked,
                artifacts,
                external_dependency_roots=("node_modules/local",),
            )


def test_binary_reference_replay_and_frozen_context_models(tmp_path):
    artifacts = ArtifactStore(tmp_path / "cas")
    result = context.preserve_context(snapshot(artifacts), artifacts)
    with pytest.raises(ValueError):
        result.manifest.files[0].mode = 0
    with pytest.raises(ValueError):
        context.ContextFile.model_validate({**result.manifest.files[0].model_dump(), "mode": "644"})


@pytest.mark.parametrize("valid_original", [True, False])
def test_overlay_evidence_verifies_original_hash_without_replacing_reference(
    tmp_path, valid_original
):
    from robo_minerador.extraction.client import AnalysisClient
    from robo_minerador.extraction.instrumentation import InstrumentationClient
    from robo_minerador.extraction.service import ExtractionService, load_extraction

    artifacts = ArtifactStore(tmp_path / "cas")
    with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
        run_id = state.create_run("frozen")
        original, plan, plan_artifact, screening, files = frozen_inputs(
            tmp_path, artifacts, state, run_id
        )

        def handler(req):
            request = json.loads(req.content)
            if req.url.path == "/v1/analyze":
                return httpx.Response(
                    200, json=analysis_payload(request["request_id"], files["main.ts"])
                )
            source = files["main.ts"].decode()
            digest = hashlib.sha256(files["main.ts"]).hexdigest()
            manifest = {
                "schema_version": "1.0",
                "purpose": "observational_supplement",
                "status": "ready",
                "framework": "vitest",
                "framework_version": "3.2.4",
                "typescript_version": "5.8.3",
                "files": [
                    {
                        "path": "main.ts",
                        "original_sha256": digest if valid_original else "0" * 64,
                        "overlay_sha256": digest,
                        "source": source,
                        "edits": [],
                        "transpilation": {
                            "typescript_version": "5.8.3",
                            "input_sha256": digest,
                            "javascript": "",
                            "source_map": json.dumps(
                                {
                                    "version": 3,
                                    "sourcesContent": [source],
                                }
                            ),
                        },
                    }
                ],
                "declarations": [],
                "targets": [],
                "callsites": [],
                "limitations": [],
            }
            return httpx.Response(
                200,
                json={
                    "protocol_version": "1.0",
                    "request_id": request["request_id"],
                    "snapshot_id": request["snapshot_id"],
                    "status": "ok",
                    "manifest": manifest,
                    "supplements": {
                        "manifest.cjs": "module.exports = " + json.dumps(manifest) + ";\n"
                    },
                    "errors": [],
                },
            )

        transport = httpx.MockTransport(handler)
        with (
            AnalysisClient("http://analyzer", transport=transport) as client,
            InstrumentationClient("http://analyzer", transport=transport) as instrument,
        ):
            report = ExtractionService(
                client, state, artifacts, instrumentation=instrument
            ).extract(
                run_id=run_id,
                snapshot=original,
                screening_digest=screening,
                plan=plan,
                plan_artifact=plan_artifact,
                analyzer_root="/real/analyzer/volume/path",
                framework="vitest",
                tsconfig_paths=("tsconfig.json",),
                framework_version="3.2.4",
            )
        model = load_extraction(artifacts, report.digest)
        assert model.instrumentation_artifact
        assert ("instrumentation_unverified" in model.limitations) is not valid_original
        reference = context.load_context(artifacts, model.context_reference)
        assert (
            next(f for f in reference.manifest.files if f.path == "main.ts").content.encode()
            == files["main.ts"]
        )
        assert model.candidates[0].target.code.encode() in files["main.ts"]
        with pytest.raises(ValueError):
            model.candidates[0].associations[0].case_title = "mutated"


def test_missing_prepared_provenance_retains_extraction_candidates(tmp_path):
    from robo_minerador.extraction.client import AnalysisClient
    from robo_minerador.extraction.service import ExtractionService, load_extraction

    artifacts = ArtifactStore(tmp_path / "cas")
    with StateStore(tmp_path / "state.db", artifacts=artifacts) as state:
        run_id = state.create_run("frozen")
        original, plan, plan_artifact, screening, files = frozen_inputs(
            tmp_path, artifacts, state, run_id
        )
        prepared = prepared_fixture(artifacts, original, plan, plan_artifact, files)
        (artifacts.root / prepared.commands[0].evidence_artifact).unlink()

        def handler(req):
            return httpx.Response(
                200,
                json=analysis_payload(
                    json.loads(req.content)["request_id"],
                    files["main.ts"],
                ),
            )

        with AnalysisClient("http://analyzer", transport=httpx.MockTransport(handler)) as client:
            report = ExtractionService(client, state, artifacts).extract(
                run_id=run_id,
                snapshot=original,
                screening_digest=screening,
                plan=plan,
                plan_artifact=plan_artifact,
                analyzer_root="/analyzer/original",
                framework="vitest",
                tsconfig_paths=("tsconfig.json",),
                prepared=prepared,
                external_dependency_roots=("node_modules/external",),
            )
        model = load_extraction(artifacts, report.digest)
        assert len(model.candidates) == 1
        assert model.prepared_context_reference is None
        assert model.context_evaluation.status == "not_evaluated"
        assert model.context_evaluation.reason == "prepared_context_unverified"
        assert any(b"prepared_context_failure" in artifacts.get(ref) for ref in model.artifacts)
