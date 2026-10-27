"""Manual checkpoints exercise production services, never dataset acceptance."""

import importlib
import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests/unit"))


def api():
    return importlib.import_module("manual_stages")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("\ufeff  GITHUB_TOKEN = 'local-value' # comment\nOTHER=ignored", "local-value"),
        ('GITHUB_TOKEN="local#value" # comment', "local#value"),
        ("GITHUB_TOKEN=local-value # comment", "local-value"),
    ],
)
def test_dotenv_only_parses_token(tmp_path, text, expected):
    (tmp_path / ".env").write_text(text, encoding="utf-8")
    assert api().load_token(tmp_path, environ={}, interactive=False) == expected


def test_token_precedence_malformed_and_missing_have_no_fallthrough(tmp_path):
    (tmp_path / ".env").write_text('GITHUB_TOKEN="unterminated')
    (tmp_path / ".env.example").write_text("GITHUB_TOKEN=fallback")
    assert api().load_token(tmp_path, environ={"GITHUB_TOKEN": "process"}) == "process"
    with pytest.raises(ValueError):
        api().load_token(tmp_path, environ={}, interactive=False)
    (tmp_path / ".env").unlink()
    assert api().load_token(tmp_path, environ={}, interactive=False) == "fallback"
    (tmp_path / ".env.example").unlink()
    with pytest.raises(ValueError):
        api().load_token(tmp_path, environ={}, interactive=False)
    assert not (tmp_path / "results").exists()


def github(request):
    if request.url.path == "/search/repositories":
        items = [
            {
                "id": n,
                "full_name": f"owner/repo{n}",
                "default_branch": "main",
                "html_url": f"https://github.com/owner/repo{n}",
                "license": {"spdx_id": "MIT"},
                "stargazers_count": 1,
                "created_at": "2024-01-01T00:00:00Z",
                "archived": False,
            }
            for n in range(1, 6)
        ]
        return httpx.Response(
            200, json={"total_count": 5, "incomplete_results": False, "items": items}
        )
    if "/commits/" in request.url.path:
        return httpx.Response(200, json={"sha": "a" * 40})
    if "/tarball/" in request.url.path or "/license" in request.url.path:
        return httpx.Response(404, json={"message": "not available"})
    raise AssertionError(request.url.path)


def test_separate_stages_reuse_config_selection_and_never_rediscover(tmp_path, respx_mock):
    respx_mock.route().mock(side_effect=github)
    m = api()
    discovery = m.run_stage("discovery", ROOT, tmp_path, token="test-token", max_repos=3)
    d = json.loads(discovery.read_text())
    assert len(d["discovery"]["repositories"]) == 5
    assert d["processing_scope"]["selected_ids"] == [1, 2, 3]
    assert d["discovery"]["exhaustive"] is True
    searches = sum(c.request.url.path == "/search/repositories" for c in respx_mock.calls)
    screening = m.run_stage(
        "screening", ROOT, tmp_path, token="test-token", input_json=discovery, max_repos=2
    )
    s = json.loads(screening.read_text())
    assert s["processing_scope"]["selected_ids"] == [1, 2]
    assert len(s["screening"]) == 2
    assert s["run_id"] == d["run_id"]
    assert s["effective_config"] == d["effective_config"]
    assert searches == sum(c.request.url.path == "/search/repositories" for c in respx_mock.calls)
    count = len(respx_mock.calls)
    extraction = m.run_stage("extraction", ROOT, tmp_path, input_json=screening)
    e = json.loads(extraction.read_text())
    assert len(respx_mock.calls) == count
    assert e["run_id"] == d["run_id"]
    assert all(r["status"] == "skipped" for r in e["extraction"])
    assert all(r["coverage"] is None for r in e["extraction"])
    assert all(not r["accepted"] for r in e["extraction"])
    assert (extraction.parent / "result.json").is_file()
    replay = m.run_stage("screening", ROOT, tmp_path, token="test-token", input_json=screening)
    assert replay == screening
    assert len(respx_mock.calls) == count


def test_all_delegates_and_latest_valid_context(tmp_path, respx_mock):
    respx_mock.route().mock(side_effect=github)
    m = api()
    result = m.run_stage("all", ROOT, tmp_path, token="test-token", max_repos=3)
    value = json.loads(result.read_text())
    assert value["stage"] == "extraction"
    assert len(value["screening"]) == len(value["extraction"]) == 3
    assert all(
        (result.parent / f"{stage}.json").is_file()
        for stage in ("discovery", "screening", "extraction")
    )
    assert m.latest_checkpoint(ROOT, tmp_path, "screening") == result.parent / "screening.json"
    (tmp_path / "runs" / "invalid").mkdir()
    (tmp_path / "runs" / "invalid" / "screening.json").write_text("{}")
    assert m.latest_checkpoint(ROOT, tmp_path, "screening") == result.parent / "screening.json"


@pytest.mark.parametrize("damage", ["path", "config", "sha", "cas", "run"])
def test_checkpoint_integrity_rejected_before_network(tmp_path, respx_mock, damage):
    respx_mock.route().mock(side_effect=github)
    m = api()
    path = m.run_stage("discovery", ROOT, tmp_path, token="test-token")
    value = json.loads(path.read_text())
    if damage == "path":
        value["storage"]["artifacts"] = "../outside"
    elif damage == "config":
        value["effective_config"]["execution"]["memory_mb"] = 1
    elif damage == "sha":
        value["discovery"]["repositories"][0]["commit_sha"] = "b" * 40
    elif damage == "run":
        value["run_id"] = "unowned"
    else:
        (path.parent / "artifacts" / value["discovery_ref"]).write_bytes(b"corrupted")
    path.write_text(json.dumps(value))
    count = len(respx_mock.calls)
    with pytest.raises(ValueError):
        m.run_stage("screening", ROOT, tmp_path, input_json=path, token="test-token")
    assert len(respx_mock.calls) == count


@pytest.mark.parametrize("action", ["discovery", "screening", "extraction", "all"])
def test_help_does_not_load_credentials(action, capsys):
    with pytest.raises(SystemExit) as exc:
        api().main([action, "--help"])
    assert exc.value.code == 0
    assert "--input-json" in capsys.readouterr().out


def test_missing_token_cli_has_no_state(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(api(), "load_token", lambda *a, **k: (_ for _ in ()).throw(ValueError()))
    assert api().main(["discovery", "--output-directory", str(tmp_path)]) == 2
    assert list(tmp_path.iterdir()) == []
    assert "Traceback" not in capsys.readouterr().err


def controlled_inputs(tmp_path, *, missing_tests=False, discovery_value=None):
    """Engineering-only approvals; never represent these as GitHub observations."""
    from dataclasses import asdict

    from test_context_roundtrip import snapshot
    from test_screening import MIT, license_response

    from robo_minerador.config import MiningConfig, load_config
    from robo_minerador.domain import Assessment
    from robo_minerador.screening.commands import plan_commands
    from robo_minerador.screening.licenses import assess_license
    from robo_minerador.screening.snapshots import Repository
    from robo_minerador.state.artifacts import ArtifactStore
    from robo_minerador.state.store import StateStore

    artifacts = ArtifactStore(tmp_path / "artifacts")
    state = StateStore(tmp_path / "state.sqlite3", artifacts=artifacts)
    if discovery_value is None:
        config = load_config(ROOT / "config/pilot.toml")
        run_id = state.create_run("engineering-fixture")
        state_id = state.upsert_repository(run_id, 1, "fixture/vitest-basic", "a" * 40)
        repo = Repository(
            repo_id=1,
            state_id=state_id,
            full_name="fixture/vitest-basic",
            url="https://example.invalid/fixture",
            commit_sha="a" * 40,
            default_branch="main",
        )
    else:
        config = MiningConfig.model_validate(discovery_value["effective_config"])
        run_id = discovery_value["run_id"]
        repo = Repository.model_validate(discovery_value["discovery"]["repositories"][0])
        state_id = repo.state_id
    directory = ROOT / "tests/fixtures/projects/vitest-basic"
    files = {
        p.relative_to(directory).as_posix(): p.read_bytes()
        for p in directory.rglob("*")
        if p.is_file() and "versions" not in p.relative_to(directory).parts
    }
    # Explicit engineering scope: the imported dependency stays context, not a target.
    tsconfig = json.loads(files["tsconfig.json"])
    tsconfig["include"] = ["value.ts", "service.ts", "value.test.ts", "test/*.ts"]
    files["tsconfig.json"] = json.dumps(tsconfig).encode()
    files["LICENSE"] = MIT
    if missing_tests:
        files = {p: b for p, b in files.items() if not p.endswith(".test.ts")}
    original = snapshot(artifacts, files, repository=repo)
    license_result = assess_license(files, repo.commit_sha, license_response(ref=repo.commit_sha))
    assert license_result.status == "approved"
    report = {
        "kind": "screening",
        "schema_version": "1.0",
        "source_kind": "fixture",
        "repository": repo.model_dump(mode="json"),
        "snapshot_manifest": original.manifest_sha256,
        "commands": asdict(plan_commands(files)),
        "artifacts": [],
        "eligible": False,
        "license": asdict(license_result),
        "assessments": [
            {
                "criterion_id": f"H-R{n:02}",
                "status": "approved",
                "reason_code": "controlled_fixture_engineering_approval",
            }
            for n in range(1, 5)
        ]
        + [
            {
                "criterion_id": "H-R05",
                "status": "not_evaluated",
                "reason_code": "suite_execution_pending",
            }
        ],
    }
    report = json.loads(json.dumps(report))
    ref = artifacts.put(json.dumps(report).encode())
    task_id = state.start_task(run_id, state_id, "screening")
    for digest in (ref, original.manifest_sha256):
        state.register_artifact(digest)
    for assessment in report["assessments"]:
        state.append_assessment(
            Assessment(
                state_id,
                assessment["criterion_id"],
                assessment["status"],
                assessment["reason_code"],
                (ref,),
            ),
            task_id=task_id,
            rule_version="manual-engineering-fixture-v1",
            stage="screening",
            explanation="Controlled fixture approvals; not observed GitHub eligibility.",
        )
    state.finish_task(
        task_id,
        "completed",
        evidence_ids=(ref, original.manifest_sha256),
        explanation="controlled_fixture_screening_record",
    )
    value = {
        "source_kind": "fixture",
        "run_id": run_id,
        "screening": [{"report_ref": ref, "report": report, "task_id": task_id}],
    }
    return value, config, state, artifacts, original


@pytest.mark.docker
def test_real_docker_manual_extraction_snapshot_http_context(tmp_path, monkeypatch):
    from _manual.extraction import extract_repositories

    from robo_minerador.extraction.context import load_context

    value, config, state, artifacts, original = controlled_inputs(tmp_path)
    try:
        result = extract_repositories(value, config, ROOT, state, artifacts)
        entry = result[0]
        assert entry["status"] == "candidate"
        assert len(entry["candidates"]) == 4
        assert entry["coverage"] is None and entry["accepted"] is False
        assert entry["analyzer"]["health"]["protocol_version"] == "1.0"
        assert entry["analyzer"]["source_verified"] is True
        assert entry["environment_plan"]["status"] == "planned"
        assert len({c["context_reference"] for c in entry["candidates"]}) == 1
        assert any(c["test_context"] for c in entry["candidates"])
        for candidate in entry["candidates"]:
            assert candidate["target"]["code"]
            assert candidate["target"]["signature"]
            assert candidate["source_status"]["status"] == "approved"
            assert candidate["documents"] and candidate["metrics"]
            preserved = load_context(artifacts, candidate["context_reference"])
            assert preserved.manifest.snapshot_manifest == original.manifest_sha256
            assert all(not call["runtime_confirmed"] for call in candidate["associations"])

        def forbidden(*args, **kwargs):
            pytest.fail("completed extraction replay must stay offline")

        monkeypatch.setattr("_manual.extraction.AnalyzerSession", forbidden)
        monkeypatch.setattr("docker.from_env", forbidden)
        replay = extract_repositories(value, config, ROOT, state, artifacts)
        assert replay[0]["extraction_ref"] == entry["extraction_ref"]
        from _manual.checkpoints import publish

        publish(
            tmp_path / "engineering-extraction.json",
            {
                "schema_version": "1.0",
                "purpose": "manual_engineering_verification",
                "source_kind": "fixture",
                "not_final_dataset": True,
                "run_id": value["run_id"],
                "storage": {"state": "state.sqlite3", "artifacts": "artifacts"},
                "extraction": result,
            },
        )
    finally:
        state.close()


def test_missing_tests_and_pending_gates_never_initialize_docker(tmp_path, monkeypatch):
    from _manual import analyzer
    from _manual.extraction import extract_repositories

    def forbidden(*args, **kwargs):
        pytest.fail("Docker must not be initialized")

    monkeypatch.setattr(analyzer, "AnalyzerSession", forbidden)
    value, config, state, artifacts, _ = controlled_inputs(tmp_path, missing_tests=True)
    try:
        result = extract_repositories(value, config, ROOT, state, artifacts)
        assert result[0]["status"] == "not_evaluated"
        assert result[0]["reason_code"] == "test_sources_unavailable"
        value["screening"][0]["report"]["assessments"][2]["status"] = "not_evaluated"
        ref = artifacts.put(json.dumps(value["screening"][0]["report"]).encode())
        value["screening"][0]["report_ref"] = ref
        value.pop("extraction")  # A separate gate scenario, not replay of the previous inputs.
        assert (
            extract_repositories(value, config, ROOT, state, artifacts)[0]["reason_code"]
            == "screening_unresolved"
        )
    finally:
        state.close()


def test_docker_transport_structured_failure_and_timeout():
    from types import SimpleNamespace

    from _manual.analyzer import DockerTransport

    from robo_minerador.extraction.client import AnalysisClient, AnalysisError, AnalysisRequest

    class Container:
        def exec_run(self, argv, **kwargs):
            assert "GITHUB_TOKEN" not in json.dumps(argv)
            return SimpleNamespace(
                exit_code=0,
                output=json.dumps(
                    {
                        "status": 503,
                        "body": {
                            "protocol_version": "1.0",
                            "request_id": "test",
                            "status": "error",
                            "targets": [],
                            "test_calls": [],
                            "documents": [],
                            "metrics": [],
                            "limitations": [],
                            "errors": [{"code": "analyzer_busy", "message": "Busy"}],
                        },
                    }
                ).encode(),
            )

    request = AnalysisRequest(
        request_id="test",
        snapshot_id="sha256:" + "a" * 64,
        root="/workspace/snapshots/test",
        tsconfig_paths=["tsconfig.json"],
        framework="vitest",
    )
    with AnalysisClient("http://analyzer", transport=DockerTransport(Container())) as client:
        with pytest.raises(AnalysisError) as exc:
            client.analyze(request)
        assert exc.value.errors[0].code == "analyzer_busy"

    class Failed:
        def exec_run(self, *args, **kwargs):
            return SimpleNamespace(exit_code=1, output=b"sensitive diagnostic")

    with httpx.Client(transport=DockerTransport(Failed())) as client:
        with pytest.raises(httpx.TransportError) as exc:
            client.get("http://analyzer/health")
        assert "sensitive" not in str(exc.value)


@pytest.mark.parametrize("name", ["discovery", "screening", "extraction", "first_stages"])
def test_powershell_wrappers_parse_and_help_from_another_cwd(tmp_path, name):
    import subprocess

    path = ROOT / "scripts" / f"try_{name}.ps1"
    assert path.is_file()
    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "$errors=$null; $tokens=$null; "
        f"[System.Management.Automation.Language.Parser]::ParseFile('{path}',"
        "[ref]$tokens,[ref]$errors) > $null; if($errors.Count){exit 1}",
    ]
    assert subprocess.run(command, capture_output=True, cwd=tmp_path).returncode == 0
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(path),
            "-Help",
        ],
        capture_output=True,
        cwd=tmp_path,
    )
    assert result.returncode == 0
    assert b"--input-json" in result.stdout


def test_screening_real_snapshot_preserves_bytes_but_runtime_gates_stay_pending(
    tmp_path, respx_mock
):
    from test_context_roundtrip import archive

    from robo_minerador.screening.snapshots import load_snapshot
    from robo_minerador.state.artifacts import ArtifactStore

    files = {
        "package.json": b'{"scripts":{"test":"vitest run"},"devDependencies":{"vitest":"3"}}',
        "package-lock.json": b'{"lockfileVersion":3}',
        "tsconfig.json": b'{"include":["*.ts"]}',
        "main.ts": b"export function f() { return 1; }\r\n",
        "main.test.ts": b"import {f} from './main'; test('f', () => f());",
    }

    def route(request):
        if "/tarball/" in request.url.path:
            return httpx.Response(200, content=archive(files))
        if "/license" in request.url.path:
            return httpx.Response(404, json={"message": "missing"})
        return github(request)

    respx_mock.route().mock(side_effect=route)
    path = api().run_stage("all", ROOT, tmp_path, max_repos=1, token="test-token")
    value = json.loads(path.read_bytes())
    assert value["status"] == "completed"
    report = value["screening"][0]["report"]
    original = load_snapshot(ArtifactStore(path.parent / "artifacts"), report["snapshot_manifest"])
    assert original.contents(ArtifactStore(path.parent / "artifacts")) == files
    assert (
        next(a for a in report["assessments"] if a["criterion_id"] == "H-R03")["status"]
        == "not_evaluated"
    )
    assert value["extraction"][0]["status"] != "candidate"


def test_existing_screening_freezes_selection_even_from_discovery(tmp_path, respx_mock):
    respx_mock.route().mock(side_effect=github)
    discovery = api().run_stage("discovery", ROOT, tmp_path, token="test-token")
    api().run_stage(
        "screening", ROOT, tmp_path, input_json=discovery, max_repos=2, token="test-token"
    )
    count = len(respx_mock.calls)
    with pytest.raises(ValueError):
        api().run_stage(
            "screening", ROOT, tmp_path, input_json=discovery, max_repos=3, token="test-token"
        )
    assert len(respx_mock.calls) == count


def test_resigned_manifest_and_selection_still_require_owned_state(tmp_path, respx_mock):
    from robo_minerador.identity import stable_digest

    respx_mock.route().mock(side_effect=github)
    path = api().run_stage("discovery", ROOT, tmp_path, token="test-token", max_repos=2)
    value = json.loads(path.read_bytes())
    value["processing_scope"]["selected_ids"] = [1, 2, 3]
    value["checkpoint_hash"] = stable_digest(
        {k: v for k, v in value.items() if k != "checkpoint_hash"}
    )
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        api().run_stage("screening", ROOT, tmp_path, input_json=path, token="test-token")


def test_second_bad_snapshot_stops_before_docker_and_never_accepts(tmp_path, monkeypatch):
    import copy

    from _manual import extraction

    value, config, state, artifacts, _ = controlled_inputs(tmp_path)
    bad = copy.deepcopy(value["screening"][0])
    bad["report"]["snapshot_manifest"] = "0" * 64
    bad["report_ref"] = artifacts.put(json.dumps(bad["report"]).encode())
    value["screening"].append(bad)

    def forbidden(*args, **kwargs):
        pytest.fail("all inputs must be verified before Docker IO")

    monkeypatch.setattr(extraction, "AnalyzerSession", forbidden)
    try:
        with pytest.raises((ValueError, FileNotFoundError)):
            extraction.extract_repositories(value, config, ROOT, state, artifacts)
    finally:
        state.close()


def test_partial_error_keeps_completed_checkpoint_without_echoing_exception(tmp_path, respx_mock):
    def route(request):
        if "/tarball/" in request.url.path:
            raise RuntimeError("DO-NOT-PUBLISH-SECRET")
        return github(request)

    respx_mock.route().mock(side_effect=route)
    path = api().run_stage("all", ROOT, tmp_path, token="test-token", max_repos=1)
    value = json.loads(path.read_bytes())
    assert value["status"] == "interrupted"
    assert value["error"]["phase"] == "screening"
    assert value["error"]["error_type"] == "RuntimeError"
    assert (path.parent / "discovery.json").is_file()
    assert "DO-NOT-PUBLISH" not in path.read_text()


@pytest.mark.parametrize(
    "field", ["screening_duplicates", "bool_limit", "wrong_purpose", "task_id", "counts"]
)
def test_typed_checkpoint_structures_refuse_resealed_invalid_values(tmp_path, respx_mock, field):
    from robo_minerador.identity import stable_digest

    respx_mock.route().mock(side_effect=github)
    path = api().run_stage("all", ROOT, tmp_path, token="test-token", max_repos=2)
    value = json.loads((path.parent / "screening.json").read_bytes())
    if field == "screening_duplicates":
        value["screening"][1] = value["screening"][0]
    elif field == "bool_limit":
        value["processing_scope"]["limit"] = True
    elif field == "task_id":
        value["screening"][0]["task_id"] = "foreign-task"
    elif field == "counts":
        value["processing_scope"]["discovered_count"] = 100
    else:
        value["purpose"] = "dataset"
    value["checkpoint_hash"] = stable_digest(
        {k: v for k, v in value.items() if k != "checkpoint_hash"}
    )
    path = path.parent / "screening.json"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        api().run_stage("extraction", ROOT, tmp_path, input_json=path)


def test_helper_removed_after_failed_transfer(tmp_path):
    from types import SimpleNamespace

    from _manual.analyzer import AnalyzerSession

    value, _, state, artifacts, original = controlled_inputs(tmp_path)

    class Helper:
        removed = False

        def exec_run(self, *args, **kwargs):
            return SimpleNamespace(exit_code=0, output=b"false")

        def put_archive(self, *args):
            raise RuntimeError("upload interrupted")

        def remove(self, **kwargs):
            self.removed = True

    helper = Helper()

    class Containers:
        def run(self, *args, **kwargs):
            assert kwargs["network_mode"] == "none"
            assert kwargs["environment"] == {}
            assert kwargs["read_only"] is True
            return helper

    session = AnalyzerSession(ROOT)
    session.client = SimpleNamespace(containers=Containers())
    session.container = SimpleNamespace(attrs={"Image": "local-analyzer"})
    session.volume = "test-workspace"
    try:
        with pytest.raises(RuntimeError):
            session.transfer(original, artifacts, value["run_id"])
        assert helper.removed
    finally:
        state.close()


def test_transport_timeout_remains_typed_and_redacted():
    from _manual.analyzer import DockerTransport

    from robo_minerador.extraction.client import (
        AnalysisClient,
        AnalysisRequest,
        AnalysisTransportError,
    )

    class Timeout:
        def exec_run(self, *args):
            raise TimeoutError("credential-must-not-appear")

    request = AnalysisRequest(
        request_id="timeout",
        snapshot_id="sha256:" + "a" * 64,
        root="/workspace/snapshots/test",
        tsconfig_paths=["tsconfig.json"],
        framework="vitest",
    )
    with AnalysisClient("http://analyzer", transport=DockerTransport(Timeout())) as client:
        with pytest.raises(AnalysisTransportError) as exc:
            client.analyze(request)
        assert "credential" not in str(exc.value)


def test_cli_corrupt_checkpoint_is_integrity_failure_without_traceback(
    tmp_path, respx_mock, monkeypatch, capsys
):
    respx_mock.route().mock(side_effect=github)
    path = api().run_stage("discovery", ROOT, tmp_path, token="test-token")
    value = json.loads(path.read_bytes())
    value["storage"]["artifacts"] = "../non-owned"
    path.write_text(json.dumps(value))
    monkeypatch.setattr(api(), "load_token", lambda *a, **k: "test-token")
    assert (
        api().main(
            [
                "screening",
                "--input-json",
                str(path),
                "--output-directory",
                str(tmp_path),
            ]
        )
        == 4
    )
    error = capsys.readouterr().err
    assert "integrity_failure" in error
    assert "Traceback" not in error and "test-token" not in error


def test_cli_unexpected_infrastructure_failure_is_safe_code_three(monkeypatch, capsys):
    monkeypatch.setattr(api(), "load_token", lambda *a, **k: "test-token")

    def fail(*args, **kwargs):
        raise OSError("SECRET-DIAGNOSTIC")

    monkeypatch.setattr(api(), "run_stage", fail)
    assert api().main(["discovery"]) == 3
    error = capsys.readouterr().err
    assert "infrastructure_interrupted" in error
    assert "SECRET-DIAGNOSTIC" not in error


def test_cas_non_owned_paths_refused_before_reading(tmp_path):
    from _manual.checkpoints import LocalArtifacts

    store = LocalArtifacts(tmp_path / "cas")
    store.root.mkdir()
    with pytest.raises(ValueError):
        store.get("../outside")
    outside = tmp_path / "outside"
    outside.write_bytes(b"must not be read by CAS")
    link = store.root / ("a" * 64)
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Windows account cannot create symlinks")
    with pytest.raises(ValueError, match="non-owned"):
        store.get(link.name)
    assert outside.read_bytes() == b"must not be read by CAS"


def test_owner_symlink_refused_before_reading(tmp_path, respx_mock, monkeypatch):
    respx_mock.route().mock(side_effect=github)
    path = api().run_stage("discovery", ROOT, tmp_path, token="test-token")
    owner = path.parent / "owner.json"
    outside = tmp_path / "outside-owner.json"
    outside.write_bytes(owner.read_bytes())
    owner.unlink()
    try:
        owner.symlink_to(outside)
    except OSError:
        pytest.skip("Windows account cannot create symlinks")
    original = Path.read_bytes

    def guarded(self):
        assert self != owner, "must reject non-owned file before reading its bytes"
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", guarded)
    with pytest.raises(ValueError):
        api().run_stage("screening", ROOT, tmp_path, input_json=path, token="test-token")


def test_screening_retry_from_discovery_reuses_completed_reports_after_interruption(
    tmp_path,
    respx_mock,
):
    from test_context_roundtrip import archive

    interrupted = False
    files = {"package.json": b"{}", "source.ts": b"export const value = 1;"}

    def route(request):
        nonlocal interrupted
        if "/tarball/" in request.url.path:
            if "/repo2/" in request.url.path and not interrupted:
                interrupted = True
                raise RuntimeError("untrusted diagnostic must stay private")
            return httpx.Response(200, content=archive(files))
        return github(request)

    respx_mock.route().mock(side_effect=route)
    discovery = api().run_stage("discovery", ROOT, tmp_path, token="test-token", max_repos=5)
    path = api().run_stage(
        "screening", ROOT, tmp_path, input_json=discovery, token="test-token", max_repos=3
    )
    partial = json.loads(path.read_bytes())
    assert partial["status"] == "interrupted"
    assert partial["error"]["phase"] == "screening"
    assert len(partial["screening"]) == 1
    assert "untrusted diagnostic" not in path.read_text()
    before = len(respx_mock.calls)
    with pytest.raises(ValueError):
        api().run_stage("screening", ROOT, tmp_path, input_json=path, token="test-token")
    assert len(respx_mock.calls) == before
    searches = sum(c.request.url.path == "/search/repositories" for c in respx_mock.calls)
    retried = api().run_stage("screening", ROOT, tmp_path, input_json=discovery, token="test-token")
    completed = json.loads(retried.read_bytes())
    assert retried == path
    assert completed["status"] == "completed" and completed["error"] is None
    assert completed["run_id"] == partial["run_id"]
    assert completed["processing_scope"]["limit"] == 3
    assert len(completed["screening"]) == 3
    assert completed["screening"][0] == partial["screening"][0]
    assert searches == sum(c.request.url.path == "/search/repositories" for c in respx_mock.calls)
    assert sum("/repo1/tarball/" in c.request.url.path for c in respx_mock.calls) == 1


@pytest.mark.parametrize("damage", ["scope", "not_object", "error_code"])
def test_screening_retry_refuses_tampered_interrupted_sidecar(tmp_path, respx_mock, damage):
    def route(request):
        if "/tarball/" in request.url.path:
            raise RuntimeError("private")
        return github(request)

    respx_mock.route().mock(side_effect=route)
    discovery = api().run_stage("discovery", ROOT, tmp_path, token="test-token", max_repos=3)
    partial_path = api().run_stage(
        "screening", ROOT, tmp_path, input_json=discovery, token="test-token"
    )
    partial = json.loads(partial_path.read_bytes())
    if damage == "scope":
        partial["processing_scope"]["limit"] = 4
    elif damage == "not_object":
        partial = []
    else:
        from robo_minerador.identity import stable_digest

        partial["error"]["code"] = "integrity_failure"
        partial["checkpoint_hash"] = stable_digest(
            {k: v for k, v in partial.items() if k != "checkpoint_hash"}
        )
    partial_path.write_text(json.dumps(partial))
    count = len(respx_mock.calls)
    with pytest.raises(ValueError):
        api().run_stage("screening", ROOT, tmp_path, input_json=discovery, token="test-token")
    assert len(respx_mock.calls) == count


def finished_extraction_checkpoint(tmp_path, respx_mock, monkeypatch):
    """Production persistence/analyze client with controlled HTTP and fixture approvals."""
    from _manual import extraction
    from _manual.checkpoints import save

    from robo_minerador.extraction.client import AnalysisClient

    respx_mock.route().mock(side_effect=github)
    discovery = api().run_stage("discovery", ROOT, tmp_path, token="test-token", max_repos=1)
    checkpoint = json.loads(discovery.read_bytes())
    value, _, state, _, _ = controlled_inputs(discovery.parent, discovery_value=checkpoint)
    state.close()
    checkpoint.update(source_kind="fixture", stage="screening", screening=value["screening"])
    checkpoint["processing_scope"]["not_processed_count"] -= 1
    screening = save(discovery.parent, checkpoint)

    class Session:
        container = None
        metadata = {
            "health": {
                "protocol_version": "1.0",
                "library_version": "0.1.0",
                "node_version": "22.22.3",
                "typescript_version": "5.8.3",
                "ts_morph_version": "26.0.0",
            },
            "source_verified": True,
            "image_id": "sha256:" + "a" * 64,
            "container_id": "b" * 64,
            "workspace_volume": "engineering-workspace",
        }

        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def transfer(self, snapshot, artifacts, run_id):
            return f"/workspace/snapshots/manual-stages/{run_id}/{snapshot.tree_sha256}"

    def analyze(request):
        return httpx.Response(
            200,
            json={
                "protocol_version": "1.0",
                "request_id": json.loads(request.content)["request_id"],
                "status": "ok",
                "targets": [],
                "test_calls": [],
                "documents": [],
                "metrics": [],
                "limitations": [],
                "errors": [],
            },
        )

    monkeypatch.setattr(extraction, "AnalyzerSession", Session)
    monkeypatch.setattr(
        extraction,
        "AnalysisClient",
        lambda *a, **k: AnalysisClient(
            "http://engineering-analyzer",
            transport=httpx.MockTransport(analyze),
        ),
    )
    path = api().run_stage("extraction", ROOT, tmp_path, input_json=screening)
    result = json.loads(path.read_bytes())
    assert result["status"] == "completed"
    assert result["extraction"][0]["status"] == "candidate"

    def forbidden(*args, **kwargs):
        pytest.fail("offline replay must not initialize Docker, transfer, or analyze")

    monkeypatch.setattr(extraction, "AnalyzerSession", forbidden)
    monkeypatch.setattr(extraction, "AnalysisClient", forbidden)
    monkeypatch.setattr("docker.from_env", forbidden)
    return screening, path


@pytest.mark.parametrize("input_stage", ["screening", "extraction"])
def test_completed_extraction_replay_from_either_checkpoint_is_offline(
    tmp_path,
    respx_mock,
    monkeypatch,
    input_stage,
):
    screening, path = finished_extraction_checkpoint(tmp_path, respx_mock, monkeypatch)
    original = path.read_bytes()
    calls = len(respx_mock.calls)
    replay = api().run_stage(
        "extraction", ROOT, tmp_path, input_json=screening if input_stage == "screening" else path
    )
    assert replay == path
    assert replay.read_bytes() == original
    assert len(respx_mock.calls) == calls


@pytest.mark.parametrize("damage", ["snapshot", "analysis", "metadata", "plan"])
def test_completed_extraction_offline_replay_still_refuses_corruption(
    tmp_path,
    respx_mock,
    monkeypatch,
    damage,
):
    from robo_minerador.identity import stable_digest

    screening, path = finished_extraction_checkpoint(tmp_path, respx_mock, monkeypatch)
    value = json.loads(path.read_bytes())
    result = value["extraction"][0]
    if damage == "metadata":
        result["analyzer"]["health"]["node_version"] = "0.0.0"
        value["checkpoint_hash"] = stable_digest(
            {k: v for k, v in value.items() if k != "checkpoint_hash"}
        )
        path.write_text(json.dumps(value))
    else:
        ref = {
            "snapshot": result["snapshot_ref"],
            "analysis": result["extraction_manifest"]["analysis_artifact"],
            "plan": result["environment_plan"]["artifact"],
        }[damage]
        (path.parent / "artifacts" / ref).write_bytes(b"corrupted")
    with pytest.raises(ValueError):
        api().run_stage("extraction", ROOT, tmp_path, input_json=screening)


def test_cli_completed_extraction_is_offline_without_loading_token(
    tmp_path,
    respx_mock,
    monkeypatch,
    capsys,
):
    screening, path = finished_extraction_checkpoint(tmp_path, respx_mock, monkeypatch)
    original = path.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("extraction replay must not load a token")

    monkeypatch.setattr(api(), "load_token", forbidden)
    assert (
        api().main(
            ["extraction", "--input-json", str(screening), "--output-directory", str(tmp_path)]
        )
        == 0
    )
    assert path.read_bytes() == original
    assert "status=completed" in capsys.readouterr().out


@pytest.mark.parametrize(
    "action,stage,command",
    [
        ("screening", "descoberta", "try_discovery.ps1"),
        ("extraction", "triagem", "try_screening.ps1"),
    ],
)
@pytest.mark.parametrize("content", ["absent", "empty", "corrupt"])
def test_cli_missing_checkpoint_has_safe_stage_specific_hint_before_credentials(
    tmp_path,
    monkeypatch,
    capsys,
    action,
    stage,
    command,
    content,
):
    output = tmp_path / "output"
    if content != "absent":
        output.mkdir()
    if content == "corrupt":
        candidate = output / "runs" / "credential-DO-NOT-ECHO"
        candidate.mkdir(parents=True)
        (candidate / ("discovery.json" if action == "screening" else "screening.json")).write_text(
            '{"private":"SECRET-MUST-NOT-APPEAR"}'
        )

    def forbidden(*args, **kwargs):
        pytest.fail("missing checkpoint must be reported before asking for credentials")

    monkeypatch.setattr(api(), "load_token", forbidden)
    assert (
        api().main([action, "--project-root", str(tmp_path), "--output-directory", str(output)])
        == 2
    )
    message = capsys.readouterr().err
    assert f"Nenhum checkpoint de {stage}" in message
    assert command in message and "-InputJson" in message
    assert "GITHUB_TOKEN" not in message
    assert "SECRET" not in message and "credential-DO-NOT-ECHO" not in message
    assert "Traceback" not in message
    assert not list(tmp_path.rglob("state.sqlite3"))
    assert not list(tmp_path.rglob("result.json"))
    if content == "absent":
        assert not output.exists()


@pytest.mark.parametrize("action", ["discovery", "all", "screening"])
def test_cli_missing_github_token_has_safe_authentication_hint(
    tmp_path,
    respx_mock,
    monkeypatch,
    capsys,
    action,
):
    from _manual.credentials import load_token

    arguments = [action, "--output-directory", str(tmp_path)]
    if action == "screening":
        respx_mock.route().mock(side_effect=github)
        discovery = api().run_stage("discovery", ROOT, tmp_path, token="test-token")
        arguments += ["--input-json", str(discovery)]
    monkeypatch.setattr(
        api(),
        "load_token",
        lambda *a, **k: load_token(
            tmp_path / "without-credentials",
            environ={},
            interactive=False,
        ),
    )
    assert api().main(arguments) == 2
    message = capsys.readouterr().err
    assert "GITHUB_TOKEN ausente" in message
    assert "processo" in message and "Traceback" not in message


def test_cli_malformed_token_and_explicit_missing_checkpoint_do_not_echo_untrusted_data(
    tmp_path,
    capsys,
):
    (tmp_path / ".env").write_text('GITHUB_TOKEN="SECRET-MUST-NOT-APPEAR')
    assert api().main(["discovery", "--project-root", str(tmp_path)]) == 2
    message = capsys.readouterr().err
    assert "GITHUB_TOKEN" in message and "declaração" in message
    assert "SECRET" not in message
    assert (
        api().main(
            [
                "extraction",
                "--project-root",
                str(tmp_path),
                "--output-directory",
                str(tmp_path),
                "--input-json",
                str(tmp_path / "credential-DO-NOT-ECHO.json"),
            ]
        )
        == 4
    )
    message = capsys.readouterr().err
    assert "integrity_failure" in message
    assert "credential-DO-NOT-ECHO" not in message and "SECRET" not in message
    assert not list(tmp_path.rglob("state.sqlite3"))


def test_interrupted_extraction_is_not_a_completed_cache_and_can_retry_from_screening(
    tmp_path,
    respx_mock,
    monkeypatch,
):
    from _manual import extraction

    respx_mock.route().mock(side_effect=github)
    discovery = api().run_stage("discovery", ROOT, tmp_path, token="test-token", max_repos=1)
    screening = api().run_stage(
        "screening", ROOT, tmp_path, input_json=discovery, token="test-token"
    )
    original = extraction.extract_repositories

    def interrupted(*args, **kwargs):
        raise RuntimeError("private-infrastructure-diagnostic")

    monkeypatch.setattr(extraction, "extract_repositories", interrupted)
    path = api().run_stage("extraction", ROOT, tmp_path, input_json=screening)
    value = json.loads(path.read_bytes())
    assert value["status"] == "interrupted"
    assert value["error"]["phase"] == "extraction"
    assert "private-infrastructure" not in path.read_text()
    with pytest.raises(ValueError):
        api().run_stage("extraction", ROOT, tmp_path, input_json=path)
    monkeypatch.setattr(extraction, "extract_repositories", original)
    api().run_stage("extraction", ROOT, tmp_path, input_json=screening)
    assert json.loads(path.read_bytes())["status"] == "completed"


def test_cli_dangling_checkpoint_is_not_found_without_reading_external_files(
    tmp_path,
    capsys,
):
    directory = tmp_path / "runs" / "unowned"
    directory.mkdir(parents=True)
    try:
        (directory / "screening.json").symlink_to(tmp_path / "credential-DO-NOT-ECHO.json")
    except OSError:
        pytest.skip("Windows account cannot create symlinks")
    assert (
        api().main(
            ["extraction", "--project-root", str(tmp_path), "--output-directory", str(tmp_path)]
        )
        == 2
    )
    message = capsys.readouterr().err
    assert "Nenhum checkpoint de triagem" in message
    assert "credential-DO-NOT-ECHO" not in message
