"""Protege integridade Linux, isolamento e limites sem executar candidatos reais."""

from copy import deepcopy
import hashlib
import io
import json
import os
import subprocess
import tarfile
import threading
from types import SimpleNamespace

import pytest

from robo_minerador.config import validate_validation_limits
from robo_minerador.git_transport import BARE_CONFIG, GitTransport
from robo_minerador.snapshot_store import SnapshotArtifact, SnapshotStore
from robo_minerador.validation_docker import DockerExecutor, DockerInfrastructureError, LABEL
from robo_minerador.validation_recipe import _hash, load_recipes
from robo_minerador.validation_workspace import prepare_workspace, ValidationWorkspace


@pytest.fixture
def source(tmp_path):
    """Cria commit confiável com bytes/modos/links que checkout/tar Git perderiam."""
    transport = GitTransport()
    root = tmp_path / "snapshot"
    bare = root / "repository.git"
    bare.mkdir(parents=True)
    transport.run(["init", "--bare", "--template="], repository=bare)
    (bare / "config").write_bytes(BARE_CONFIG)
    files = {"package-lock.json": ("100644", b"{}\r\n"),
             "binary.bin": ("100644", b"\0\xff\r\n\x80binary"),
             "executable": ("100755", b"#!/bin/sh\nexit 0\n"),
             "link": ("120000", b"binary.bin"),
             "hidden.txt": ("100644", b"export-ignore preservado\r\n"),
             ".gitattributes": ("100644", b"hidden.txt export-ignore\n*.bin filter=evil text\n")}
    records = []
    for path, (mode, content) in sorted(files.items()):
        oid = transport.run(["hash-object", "-w", "--stdin"], repository=bare, input_data=content).decode().strip()
        records.append(f"{mode} blob {oid}\t{path}\0".encode())
    tree = transport.run(["mktree", "-z"], repository=bare, input_data=b"".join(records)).decode().strip()
    sha = transport.run(["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                         "commit-tree", tree], repository=bare, input_data=b"fixture\n").decode().strip()
    transport.run(["update-ref", "refs/minerador/snapshot", sha], repository=bare)
    tree, entries, total = transport.inventory(bare, sha)
    artifact = SnapshotArtifact(42, sha, tree, "a" * 64, len(entries), total, root)
    recipe = deepcopy(next(iter(load_recipes()["recipes"].values())))
    recipe.update(repository_id=42, commit_sha=sha, repository_url="https://github.com/fixture/trusted")
    recipe["lockfiles"][0]["sha256"] = hashlib.sha256(files["package-lock.json"][1]).hexdigest()
    return SimpleNamespace(store=SnapshotStore(tmp_path, transport), artifact=artifact, recipe=recipe,
                           verified={"snapshot_artifact": artifact, "snapshot": artifact.reference()}, files=files)


class ArchiveCapture:
    """Inspeciona materialização sem requerer Docker nas verificações de recusa."""

    def materialize(self, artifact, recipe, recipe_hash, manifest, archive):
        """Retém argumentos para conferir bytes sem materializar no Windows."""
        return {"manifest": manifest, "archive": archive, "recipe_hash": recipe_hash}


def test_archive_preserves_original_bytes_modes_and_lock(source):
    """Garante que filtros, CRLF e export-ignore não alteram a árvore adquirida."""
    captured = prepare_workspace(source.store, source.verified, source.recipe, ArchiveCapture())
    with tarfile.open(fileobj=io.BytesIO(captured["archive"])) as archive:
        for path, (mode, content) in source.files.items():
            member = archive.getmember(path)
            if mode == "120000":
                assert member.issym() and member.linkname == content.decode()
            else:
                assert archive.extractfile(member).read() == content
                assert member.mode == (0o755 if mode == "100755" else 0o644)
        assert archive.extractfile(".git/HEAD").read().strip().decode() == source.artifact.commit_sha
        assert not any("hooks/" in name or "alternates" in name for name in archive.getnames())
    source.recipe["lockfiles"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="Lockfile"):
        prepare_workspace(source.store, source.verified, source.recipe, ArchiveCapture())


@pytest.mark.parametrize("kind", ["link", "submodule", "lfs"])
def test_unacquired_or_escaping_content_is_refused(source, monkeypatch, kind):
    """Links externos e conteúdo não adquirido nunca são apresentados como arquivos reais."""
    transport = source.store.transport
    tree, entries, total = transport.inventory(source.artifact.storage_path / "repository.git", source.artifact.commit_sha)
    if kind == "link":
        original = transport.read_blobs

        def tampered(*args, **kwargs):
            """Substitui somente bytes do link para exercitar contenção pré-Docker."""
            contents = original(*args, **kwargs)
            return [b"../../outside" if entry.get("path") == "link" else content
                    for entry, content in zip(args[1], contents)]
        monkeypatch.setattr(transport, "read_blobs", tampered)
    elif kind == "submodule":
        entries[0]["mode"] = "160000"
    else:
        entries[0]["lfs_pointer"] = {"oid": "sha256:" + "b" * 64, "size": 12}
    monkeypatch.setattr(transport, "inventory", lambda *args: (tree, entries, total))
    with pytest.raises(ValueError):
        prepare_workspace(source.store, source.verified, source.recipe, ArchiveCapture())


def _workspace(executor, recipe):
    """Prepara handle simulado com a mesma receita congelada da execução real."""
    executor._volumes.add("owned")
    return ValidationWorkspace("owned", executor.run_id, recipe["repository_id"], recipe["commit_sha"],
                               json.dumps(recipe), _hash(recipe), [], {})


def test_instrument_volume_upload_and_ownership(tmp_path, monkeypatch):
    recipe = deepcopy(next(iter(load_recipes()["recipes"].values())))
    executor = DockerExecutor()
    workspace = _workspace(executor, recipe)
    (tmp_path / "settings.json").write_bytes(b'{"fixture":true}')
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "data").write_bytes(b'\x00\xff')
    calls = []
    def cli(args, **kwargs):
        calls.append((args, kwargs))
        return b"cid"
    monkeypatch.setattr(executor, "_cli", cli)
    monkeypatch.setattr(executor, "_inspect", lambda name: {"HostConfig": {
        "NanoCpus": 4_000_000_000, "Memory": 8589934592, "MemorySwap": 8589934592, "NetworkMode": "none"}})
    monkeypatch.setattr(executor, "_execute", lambda *args: {"exitcode": 0})
    volume = executor.publish_instrument(workspace, tmp_path)
    upload = next(kwargs["data"] for args, kwargs in calls if args[:2] == ["cp", "-"])
    with tarfile.open(fileobj=io.BytesIO(upload)) as archive:
        assert archive.extractfile("settings.json").read() == b'{"fixture":true}'
        assert archive.extractfile("nested/data").read() == b'\x00\xff'
    manifests = [kwargs["data"] for args, kwargs in calls if args[:2] == ["cp", "-"] and args[-1].endswith(":/tmp")]
    assert len(manifests) == 1
    with tarfile.open(fileobj=io.BytesIO(manifests[0])) as archive:
        hashes = json.load(archive.extractfile("manifest.json"))
    assert hashes["settings.json"] == hashlib.sha256(b'{"fixture":true}').hexdigest()
    create = next(args for args, _ in calls if args[0] == "create")
    assert f"type=volume,src={volume},dst=/instrument,volume-nocopy" in create
    assert str(tmp_path) not in str(create) and "type=bind" not in str(create)
    with pytest.raises(ValueError):
        executor._create(recipe, ["true"], instrument_volume="foreign")
    executor._create(recipe, ["true"], instrument_volume=volume)
    assert f"type=volume,src={volume},dst=/instrument,volume-nocopy,readonly" in calls[-1][0]
    executor.close()


@pytest.mark.parametrize("kind", ["volume", "container"])
def test_cancel_waits_for_resource_creation_before_cleanup(monkeypatch, kind):
    recipe = deepcopy(next(iter(load_recipes()["recipes"].values())))
    executor = DockerExecutor()
    entered, resume, cleanup_entered = threading.Event(), threading.Event(), threading.Event()
    existing = set()
    errors = []
    original_release = executor._release_resources

    def release():
        cleanup_entered.set()
        original_release()

    def cli(args, **kwargs):
        if (kind == "volume" and args[:2] == ["volume", "create"]) or (kind == "container" and args[0] == "create"):
            entered.set()
            assert resume.wait(10)
            existing.add(args[-1] if kind == "volume" else args[args.index("--name") + 1])
            return b"cid"
        if args[:2] == ["volume", "rm"] or args[:2] == ["rm", "--force"]:
            name = args[-1]
            if name not in existing:
                raise DockerInfrastructureError("resource not yet created")
            existing.remove(name)
        return b""

    monkeypatch.setattr(executor, "_cli", cli)
    monkeypatch.setattr(executor, "_release_resources", release)
    monkeypatch.setattr(executor, "_inspect", lambda name: {"HostConfig": {
        "NanoCpus": 4_000_000_000, "Memory": 8589934592, "MemorySwap": 8589934592, "NetworkMode": "none"}})
    def create():
        try:
            executor._volume() if kind == "volume" else executor._create(recipe, ["true"])
        except DockerInfrastructureError as error:
            errors.append(error)
    creator = threading.Thread(target=create)
    canceller = threading.Thread(target=executor.cancel)
    try:
        creator.start()
        assert entered.wait(10)
        canceller.start()
        assert cleanup_entered.wait(10)
    finally:
        resume.set()
        creator.join(10)
        if canceller.ident is not None:
            canceller.join(10)
    assert not creator.is_alive() and not canceller.is_alive()
    assert not existing and not executor._volumes and not executor._containers
    assert not executor.cleanup_errors
    executor.close()


@pytest.mark.parametrize("failure", ["upload", "verify"])
def test_instrument_publication_failure_cleans_owned_resources(tmp_path, monkeypatch, failure):
    recipe = deepcopy(next(iter(load_recipes()["recipes"].values())))
    executor = DockerExecutor()
    workspace = _workspace(executor, recipe)
    (tmp_path / "settings.json").write_bytes(b"settings")
    calls = []
    def cli(args, **kwargs):
        calls.append(args)
        if failure == "upload" and args[0] == "cp":
            raise DockerInfrastructureError("upload failed")
        return b"cid"
    monkeypatch.setattr(executor, "_cli", cli)
    monkeypatch.setattr(executor, "_inspect", lambda name: {"HostConfig": {
        "NanoCpus": 4_000_000_000, "Memory": 8589934592, "MemorySwap": 8589934592, "NetworkMode": "none"}})
    monkeypatch.setattr(executor, "_execute", lambda *args: {"exitcode": 1})
    monkeypatch.setattr(executor, "read_log", lambda *args: b"verification failed")
    with pytest.raises(DockerInfrastructureError):
        executor.publish_instrument(workspace, tmp_path)
    assert workspace.instrument_volume is None and not workspace.bootstrap_argv
    volumes = executor._volumes - {"owned"}
    assert len(volumes) == 1 and any(args[0] == "rm" for args in calls)
    executor.close()
    assert ["volume", "rm", next(iter(volumes))] in calls


def test_instrument_rejects_symlink(tmp_path, monkeypatch):
    recipe = deepcopy(next(iter(load_recipes()["recipes"].values())))
    executor = DockerExecutor()
    workspace = _workspace(executor, recipe)
    try:
        (tmp_path / "link").symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("Criação de symlink indisponível neste host")
    monkeypatch.setattr(executor, "_cli", lambda *args, **kwargs: b"")
    with pytest.raises(ValueError, match="link"):
        executor.publish_instrument(workspace, tmp_path)
    executor.close()


def test_configure_cleans_temporary_directory_when_publication_fails(tmp_path, monkeypatch):
    from robo_minerador import validation_instrument as instrument
    recipe = deepcopy(next(iter(load_recipes()["recipes"].values())))
    executor = DockerExecutor()
    workspace = _workspace(executor, recipe)
    workspace.next_phase = 2
    artifact = SimpleNamespace(repository_id=workspace.repository_id, commit_sha=workspace.commit_sha,
                               reference=lambda: "snapshot")
    verified = {"snapshot": "snapshot", "snapshot_artifact": artifact,
                "context": {"files": []}, "instances": {}}
    helper = tmp_path / "helper"
    compiler = helper / "node_modules" / "typescript"
    compiler.mkdir(parents=True)
    (compiler / "package.json").write_text('{"version":"6.0.3"}')
    monkeypatch.setattr(instrument, "HELPER", helper)
    monkeypatch.setattr(instrument, "_command_packages", lambda *args: [])
    monkeypatch.setattr(instrument.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=b"{}"))
    monkeypatch.setattr(executor, "collect_dependencies", lambda workspace: {"packages": [{"name": "vitest", "path": "node_modules/vitest/package.json"}]})
    monkeypatch.setattr(executor, "inspect_instrument_installations", lambda *args: [{"tools": {"node": "22.23.4", "vitest": recipe["tools"]["vitest"], "vite": "7.3.6"}}])
    monkeypatch.setattr(executor, "write_workspace_files", lambda *args: None)
    paths = []
    def fail(workspace, root):
        paths.append(root)
        raise DockerInfrastructureError("upload failed")
    monkeypatch.setattr(executor, "publish_instrument", fail)
    monkeypatch.setattr(executor, "_cli", lambda *args, **kwargs: b"")
    with pytest.raises(DockerInfrastructureError):
        instrument.configure_instrument(workspace, verified, recipe, executor)
    assert not paths[0].exists() and not workspace.bootstrap_argv and workspace.instrument_volume is None
    assert executor.run_phase(workspace, "unit_suite", recipe)["status"] == "not_started"
    executor.close()


def test_flags_deadline_first_failure_and_immutable_recipe(monkeypatch):
    """Prazo é agregado; OOM não vira timeout e falha impede fases posteriores."""
    recipe = deepcopy(next(iter(load_recipes()["recipes"].values())))
    recipe["phases"]["preparation"] *= 2
    now = {"seconds": 100}
    executor = DockerExecutor(clock=lambda: now["seconds"])
    workspace = _workspace(executor, recipe)
    workspace.next_phase = 1
    calls, deadlines = [], []

    def cli(args, **kwargs):
        """Captura flags administrativas sem chamar daemon."""
        calls.append(args)
        return b"cid"

    def execute(name, cid, deadline, metadata):
        """Simula OOM no segundo comando preservando o prazo da fase inteira."""
        deadlines.append(deadline)
        now["seconds"] += 30
        executor._containers.remove(name)
        return {**metadata, "exitcode": 0 if len(deadlines) == 1 else 137,
                "oom": len(deadlines) == 2, "timeout": False, "cancelled": False,
                "daemon_error": None, "capture_loss": None}

    monkeypatch.setattr(executor, "_cli", cli)
    monkeypatch.setattr(executor, "_inspect", lambda name: {"HostConfig": {
        "NanoCpus": 4_000_000_000, "Memory": 8589934592, "MemorySwap": 8589934592, "NetworkMode": "bridge"}})
    monkeypatch.setattr(executor, "_execute", execute)
    result = executor.run_phase(workspace, "preparation", recipe)
    assert deadlines == [700, 700]
    assert result["status"] == "failed" and result["first_failure"]["oom"]
    assert not result["first_failure"]["timeout"]
    flags = calls[0]
    assert flags[flags.index("--cpus") + 1] == "4"
    assert flags[flags.index("--memory") + 1] == flags[flags.index("--memory-swap") + 1] == "8589934592"
    assert "--publish" not in flags and "--privileged" not in flags
    assert executor.run_phase(workspace, "unit_suite", recipe)["status"] == "not_started"
    recipe["limits"]["cpus"] = 5
    with pytest.raises(ValueError, match="alterada"):
        executor.run_phase(workspace, "unit_suite", recipe)
    recipe["limits"]["cpus"] = 4
    tampered = workspace.recipe()
    tampered["limits"]["cpus"] = 5
    workspace.recipe_json = json.dumps(tampered)
    with pytest.raises(ValueError, match="congelada"):
        executor.run_phase(workspace, "unit_suite", recipe)
    executor.close()


def test_timeout_capture_inspect_before_remove_and_cancel(monkeypatch):
    """Simula deadline sem confundi-lo com OOM e preserva captura completa anterior ao rm."""
    ticks = iter([0, 0, 2, 2])
    executor = DockerExecutor(clock=lambda: next(ticks))
    executor._containers.add("owned")
    order = []

    class Process:
        """Representa attach em andamento até o kill administrativo confiável."""

        pid = 123
        returncode = None

        def __init__(self, argv, **kwargs):
            """Grava volume de saída maior que o limite comum de captura em memória."""
            assert kwargs["shell"] is False
            kwargs["stdout"].write(b"x" * (2 * 1024 * 1024))

        def poll(self):
            """Mantém attach ativo até kill ser solicitado."""
            return self.returncode

        def wait(self, timeout=None):
            """Confirma encerramento após kill sem custo de relógio real."""
            self.returncode = 137

        def kill(self):
            """Encerra apenas a fixture do processo CLI."""
            self.returncode = 137

    def cli(args, **kwargs):
        """Registra sequência kill/inspect/rm/volume sem acessar o daemon."""
        order.append(args[0])
        if args[0] == "inspect":
            return json.dumps([{"State": {"OOMKilled": False, "ExitCode": 137}}]).encode()
        return b""

    monkeypatch.setattr(subprocess, "Popen", Process)
    monkeypatch.setattr(executor, "_cli", cli)
    event = executor._execute("owned", "cid", 1, {})
    assert event["timeout"] and not event["oom"] and event["exitcode"] == 137
    assert order == ["kill", "inspect", "rm"]
    assert len(executor.read_log(event)) == 2 * 1024 * 1024 and event["capture_loss"] is None
    executor._volumes.add("owned-volume")
    executor.cancel()
    assert not executor._volumes
    executor.close()


def test_cancel_before_start_does_not_launch_or_kill_created_container(monkeypatch):
    executor = DockerExecutor()
    executor._containers.add("owned")
    entered, resume = threading.Event(), threading.Event()
    original_set = executor._active.set
    def active_set():
        original_set()
        entered.set()
        assert resume.wait(10)
    monkeypatch.setattr(executor._active, "set", active_set)
    calls, events = [], []
    def cli(args, **kwargs):
        calls.append(args)
        if args[0] == "inspect":
            return json.dumps([{"State": {"OOMKilled": False, "ExitCode": 0, "Running": False}}]).encode()
        return b""
    monkeypatch.setattr(executor, "_cli", cli)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("start after cancellation"))
    worker = threading.Thread(target=lambda: events.append(executor._execute("owned", "cid", executor.clock() + 30, {})))
    worker.start()
    try:
        assert entered.wait(10)
        executor.cancel()
    finally:
        resume.set()
        worker.join(10)
    assert not worker.is_alive() and events[0]["cancelled"]
    assert not any(args[0] == "kill" for args in calls)
    assert not executor._containers
    executor.close()


def test_cancel_after_start_marks_fast_exit_cancelled(monkeypatch):
    executor = DockerExecutor()
    executor._containers.add("owned")
    entered, resume = threading.Event(), threading.Event()
    events, calls = [], []
    class Process:
        pid = 42
        returncode = 0
        def __init__(self, *args, **kwargs):
            entered.set()
            assert resume.wait(10)
        def poll(self):
            assert executor._cancelled.wait(10)
            return 0
        def wait(self, timeout=None):
            return 0
    def cli(args, **kwargs):
        calls.append(args)
        if args[0] == "inspect":
            return json.dumps([{"State": {"OOMKilled": False, "ExitCode": 0, "Running": False}}]).encode()
        return b""
    monkeypatch.setattr(subprocess, "Popen", Process)
    monkeypatch.setattr(executor, "_cli", cli)
    worker = threading.Thread(target=lambda: events.append(executor._execute("owned", "cid", executor.clock() + 30, {})))
    canceller = threading.Thread(target=executor.cancel)
    worker.start()
    try:
        assert entered.wait(10)
        canceller.start()
    finally:
        resume.set()
        worker.join(10)
        if canceller.ident is not None:
            canceller.join(10)
    assert not worker.is_alive() and not canceller.is_alive()
    assert events[0]["cancelled"] and not executor._success(events[0])
    assert not executor._containers and any(args[0] == "rm" for args in calls)
    executor.close()


def test_cancel_during_daemon_start_removes_not_yet_running_container(monkeypatch):
    executor = DockerExecutor()
    executor._containers.add("owned")
    launched = threading.Event()
    events, calls = [], []
    class Process:
        pid = 42
        returncode = None
        def __init__(self, *args, **kwargs):
            launched.set()
        def poll(self):
            assert executor._cancelled.wait(10)
            return self.returncode
        def wait(self, timeout=None):
            self.returncode = 1
        def kill(self):
            self.returncode = 1
    def cli(args, **kwargs):
        calls.append(args)
        if args[0] == "kill":
            raise DockerInfrastructureError("container is not running")
        if args[0] == "inspect":
            raise DockerInfrastructureError("container removed")
        return b""
    monkeypatch.setattr(subprocess, "Popen", Process)
    monkeypatch.setattr(executor, "_cli", cli)
    worker = threading.Thread(target=lambda: events.append(executor._execute("owned", "cid", executor.clock() + 30, {})))
    worker.start()
    try:
        assert launched.wait(10)
        executor.cancel()
    finally:
        worker.join(10)
    assert not worker.is_alive() and events[0]["cancelled"]
    assert ["rm", "--force", "owned"] in calls and not executor._containers
    executor.close()


def test_aggregate_deadline_does_not_start_another_command(monkeypatch):
    """Esgotar o orçamento do primeiro passo impede criar o segundo container."""
    recipe = deepcopy(next(iter(load_recipes()["recipes"].values())))
    recipe["phases"]["preparation"] *= 2
    now = {"seconds": 100}
    executor = DockerExecutor(clock=lambda: now["seconds"])
    workspace = _workspace(executor, recipe)
    workspace.next_phase = 1
    created = []

    def create(*args, **kwargs):
        """Conta containers sem acessar Docker para detectar reinício incorreto do prazo."""
        created.append(1)
        return "owned", "cid"

    def execute(name, cid, deadline, metadata):
        """Consome todo o prazo agregado no primeiro comando simulado."""
        now["seconds"] = 701
        return {**metadata, "exitcode": 0, "oom": False, "timeout": False,
                "cancelled": False, "daemon_error": None, "capture_loss": None}

    monkeypatch.setattr(executor, "_create", create)
    monkeypatch.setattr(executor, "_execute", execute)
    monkeypatch.setattr(executor, "_cli", lambda *args, **kwargs: b"")
    result = executor.run_phase(workspace, "preparation", recipe)
    assert len(created) == 1 and result["first_failure"]["timeout"]
    assert result["commands"][1]["status"] == "not_started"
    assert result["commands"][1]["container_id"] is None
    executor.close()


@pytest.mark.parametrize("value", [True, 0, -1, 4.0, "4"])
def test_limits_reject_coercion(value):
    """Configuração não aceita bool, números não positivos ou coerções implícitas."""
    limits = deepcopy(next(iter(load_recipes()["recipes"].values()))["limits"])
    limits["cpus"] = value
    with pytest.raises(ValueError):
        validate_validation_limits(limits)


@pytest.mark.skipif(os.environ.get("ROBO_TEST_DOCKER") != "1", reason="Fixture Docker real exige opt-in explícito.")
@pytest.mark.parametrize("filename", ["settings.json", "validation-command.mjs"])
def test_real_instrument_verifier_rejects_corrupted_upload(tmp_path, monkeypatch, filename):
    recipe = deepcopy(next(iter(load_recipes()["recipes"].values())))
    executor = DockerExecutor()
    root = tmp_path / "instrument"
    root.mkdir()
    (root / "settings.json").write_bytes(b'{"fixture":true}')
    (root / "validation-command.mjs").write_bytes(b"export const fixture = true;\n")
    real_cli = executor._cli
    corrupted = []
    def cli(args, **kwargs):
        result = real_cli(args, **kwargs)
        if args[:2] == ["cp", "-"] and args[-1].endswith(":/tmp"):
            payload = io.BytesIO()
            with tarfile.open(fileobj=payload, mode="w") as archive:
                data = b"corrupted bytes"
                entry = tarfile.TarInfo(filename)
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
            real_cli(["cp", "-", args[-1].removesuffix(":/tmp") + ":/instrument"], data=payload.getvalue())
            corrupted.append(filename)
        return result

    try:
        executor.preflight()
        workspace_volume = executor._volume()
        workspace = ValidationWorkspace(workspace_volume, executor.run_id, recipe["repository_id"], recipe["commit_sha"],
                                        json.dumps(recipe), _hash(recipe), [], {})
        monkeypatch.setattr(executor, "_cli", cli)
        with pytest.raises(DockerInfrastructureError, match="instrument bytes: " + filename):
            executor.publish_instrument(workspace, root)
        assert corrupted == [filename]
        assert workspace.instrument_volume is None and not workspace.bootstrap_argv
        assert all(event["kind"] != "candidate" for event in executor.events)
    finally:
        executor.close()
    assert not executor.cleanup_errors
    assert real_cli(["ps", "-aq", "--filter", "label=" + LABEL + "=" + executor.run_id]) == b""
    assert real_cli(["volume", "ls", "-q", "--filter", "label=" + LABEL + "=" + executor.run_id]) == b""


@pytest.mark.skipif(os.environ.get("ROBO_TEST_DOCKER") != "1", reason="Fixture Docker real exige opt-in explícito.")
def test_real_linux_identity_isolation_limits_and_process_cleanup(source, tmp_path):
    """Inspeciona isolamento real sem alocar 8 GiB nem instalar dependências candidatas."""
    executor = DockerExecutor()
    recipe = deepcopy(source.recipe)
    # Apenas código de fixture confiável. Nenhum manifesto/config candidato é carregado.
    fixture = """const fs=require('node:fs'),cp=require('node:child_process');
fs.writeFileSync('/workspace/installed','yes');
fs.mkdirSync('/workspace/node_modules/trusted-fixture',{recursive:true});
fs.writeFileSync('/workspace/node_modules/trusted-fixture/package.json',JSON.stringify({name:'trusted-fixture',version:'1.2.3'}));
fs.writeFileSync('/workspace/node_modules/.package-lock.json',JSON.stringify({packages:{'node_modules/trusted-fixture':{version:'1.2.3',resolved:'https://registry.npmjs.org/trusted-fixture/-/trusted-fixture-1.2.3.tgz',integrity:'fixture-only'}}}));
cp.spawn('node',['-e',\"setInterval(()=>require('node:fs').appendFileSync('/workspace/heartbeat','x'),20)\"],{detached:true,stdio:'ignore'}).unref();
setTimeout(()=>console.log('fixture-install'),150);"""
    suite = """const fs=require('node:fs'),os=require('node:os'),net=require('node:net');
if(fs.readFileSync('/workspace/installed','utf8')!=='yes')throw Error('volume');
if(Object.keys(os.networkInterfaces()).some(k=>k!=='lo'))throw Error('external network');
if(process.env.ROBO_HOST_SECRET)throw Error('host env');
if(fs.existsSync('/var/run/docker.sock'))throw Error('socket');
if(fs.readFileSync('/sys/fs/cgroup/memory.max','utf8').trim()!=='8589934592')throw Error('memory');
if(fs.readFileSync('/sys/fs/cgroup/memory.swap.max','utf8').trim()!=='0')throw Error('swap');
const quota=fs.readFileSync('/sys/fs/cgroup/cpu.max','utf8').trim().split(' ').map(Number);
if(quota[0]/quota[1]!==4)throw Error('cpu');
const heartbeat=fs.readFileSync('/workspace/heartbeat','utf8');
setTimeout(()=>{if(fs.readFileSync('/workspace/heartbeat','utf8')!==heartbeat)throw Error('surviving process')},150);
try{fs.writeFileSync('/instrument/forbidden','x');throw Error('writable instrument')}catch(e){if(e.message==='writable instrument')throw e;}
const server=net.createServer(s=>s.end('loopback'));server.listen(0,'127.0.0.1',()=>{
const client=net.connect(server.address().port,'127.0.0.1');client.on('data',()=>{client.destroy();server.close();console.log('fixture-suite-ok')});});"""
    recipe["phases"] = {"installation": [{"argv": ["node", "-e", fixture], "cwd": "."}],
                        "preparation": [], "unit_suite": [{"argv": ["node", "-e", suite], "cwd": "."}]}
    # Validação de catálogo continua exigindo npm ci; o handle da fixture é congelado após materialização.
    workspace = prepare_workspace(source.store, source.verified, source.recipe, executor)
    workspace.recipe_json, workspace.recipe_sha256 = json.dumps(recipe), _hash(recipe)
    (tmp_path / "bootstrap.cjs").write_text(
        "// Envolve comando original como fixture confiável, sem alterar seus argumentos.\n"
        "const cp=require('node:child_process');"
        "console.log('trusted-bootstrap');"
        "const argv=process.argv.slice(2);"
        "const r=cp.spawnSync(argv[0],argv.slice(1),{stdio:'inherit'});process.exit(r.status??1);", encoding="utf-8")
    workspace.instrument_volume = executor.publish_instrument(workspace, tmp_path)
    workspace.bootstrap_argv = ("node", "/instrument/bootstrap.cjs")
    os.environ["ROBO_HOST_SECRET"] = "must-not-be-inherited"
    try:
        first = executor.run_phase(workspace, "installation", recipe)
        assert first["status"] == "completed", first
        command = first["commands"][0]
        assert command["inspect"]["HostConfig"]["NanoCpus"] == 4_000_000_000
        assert command["inspect"]["HostConfig"]["Memory"] == 8589934592
        assert command["inspect"]["HostConfig"]["MemorySwap"] == 8589934592
        assert command["inspect"]["State"]["Running"] is False
        assert executor._cli(["ps", "-aq", "--filter", "label=" + LABEL + "=" + executor.run_id]) == b""
        executor.run_phase(workspace, "preparation", recipe)
        final = executor.run_phase(workspace, "unit_suite", recipe)
        assert final["status"] == "completed", final
        command = final["commands"][0]
        assert command["inspect"]["HostConfig"]["NetworkMode"] == "none"
        assert any(m["Destination"] == "/instrument" and m["RW"] is False for m in command["inspect"]["Mounts"])
        assert b"fixture-suite-ok" in executor.read_log(command)
        assert b"trusted-bootstrap" in executor.read_log(command)
        assert command["argv"] == recipe["phases"]["unit_suite"][0]["argv"]
        inventory = executor.collect_dependencies(workspace)
        assert inventory["packages"][0]["name"] == "trusted-fixture"
        assert inventory["packages"][0]["version"] == "1.2.3"
        assert inventory["installed_lockfiles"][0]["packages"]["node_modules/trusted-fixture"]["integrity"] == "fixture-only"
        assert executor.read_workspace_files(workspace, ["binary.bin"])["binary.bin"] == source.files["binary.bin"][1]
        print(json.dumps({"daemon": executor.daemon, "environment": workspace.environment,
                          "limits": command["limits"], "network": command["network"], "oom": command["oom"]}))
    finally:
        os.environ.pop("ROBO_HOST_SECRET", None)
        executor.close()
    assert not executor.cleanup_errors
    assert executor._cli(["volume", "ls", "-q", "--filter", "label=" + LABEL + "=" + executor.run_id]) == b""


@pytest.mark.skipif(os.environ.get("ROBO_TEST_DOCKER") != "1", reason="Bootstrap confiável requer registry explícito.")
def test_real_pnpm_bootstrap_without_candidate_mounts():
    """Confere integridade/versionamento pnpm em volume de ferramentas sem candidatos."""
    recipe = deepcopy(list(load_recipes()["recipes"].values())[-1])
    executor = DockerExecutor()
    try:
        executor.preflight()
        volume = executor._volume()
        from robo_minerador.validation_docker import BOOTSTRAP
        executor._trusted(recipe, ["node", "-e", BOOTSTRAP, json.dumps(recipe["tool_bootstrap"])],
                          tool_volume=volume, writable_tools=True, network="bridge")
        event = executor._trusted(recipe, ["pnpm", "--version"], tool_volume=volume)
        assert executor.read_log(event).strip() == b"10.34.7"
        node = executor._trusted(recipe, ["node", "--version"], tool_volume=volume)
        assert executor.read_log(node).strip() == b"v26.13.0"
        assert all(m["Destination"] != "/workspace" for m in event["inspect"]["Mounts"])
        assert any(m["Destination"] == "/tools" and m["RW"] is False for m in event["inspect"]["Mounts"])
        print("pnpm 10.34.7: integridade SHA-512 conferida; volume readonly; sem workspace candidato")
    finally:
        executor.close()
    assert not executor.cleanup_errors
