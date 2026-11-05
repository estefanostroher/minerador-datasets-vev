"""Confere isolamento e limites do Git usando somente objetos de fixtures locais."""

import subprocess
import os
import sys
import threading
import time
import tempfile
from types import SimpleNamespace

import pytest

from robo_minerador.git_transport import GitTransport
from robo_minerador.snapshot_store import SnapshotStore
from robo_minerador import snapshot_store
from robo_minerador import git_transport
from robo_minerador.triage_snapshot import SnapshotUnavailable


class LocalTransport(GitTransport):
    """Substitui exclusivamente o fetch público pela origem controlada do teste."""

    def __init__(self, origin, **kwargs):
        """Mantém os mesmos limites e contabiliza aquisições locais."""
        super().__init__(**kwargs)
        self.origin = origin
        self.fetches = 0

    def _fetch(self, repository, url, sha, **options):
        """Habilita protocolo de arquivo somente na fixture injetada."""
        self.fetches += 1
        self.run(["-c", "protocol.file.allow=always", "fetch", "--depth=1", "--no-tags",
                  "--no-recurse-submodules", str(self.origin), sha + ":refs/minerador/snapshot"],
                 repository=repository, **options)


@pytest.fixture
def local_repository(tmp_path):
    """Cria objetos binários, especiais e nomes arbitrários sem checkout."""
    origin = tmp_path / "origin.git"
    runner = GitTransport()
    runner.run(["init", "--bare", "--template=", str(origin)])
    contents = {b"binary.bin": b"\0\xff\r\n\x80", b"bom.txt": b"\xef\xbb\xbftexto\r\n",
                b".gitattributes": b"hidden.txt export-ignore\n*.bin filter=evil\n",
                b"hidden.txt": b"preservado", b"Name.txt": b"maiusculo",
                b"name.txt": b"minusculo", b"inv\xff.txt": b"nome em bytes",
                "a\u0301.txt".encode(): b"decomposto", "\u00e1.txt".encode(): b"composto",
                b"link": b"../../fora"}
    contents[b"asset.lfs"] = (b"version https://git-lfs.github.com/spec/v1\n"
                              b"oid sha256:" + b"a" * 64 + b"\nsize 9876543210\n")
    contents[b"not-lfs.txt"] = (b"version https://git-lfs.github.com/spec/v1\n"
                                b"oid sha256:invalido\nsize 10\n")
    records = []
    for path, content in contents.items():
        oid = runner.run(["hash-object", "-w", "--stdin"], repository=origin,
                         input_data=content).strip()
        mode = b"120000" if path == b"link" else b"100644"
        records.append(mode + b" blob " + oid + b"\t" + path + b"\0")
    records.append(b"160000 commit " + b"a" * 40 + b"\tsubmodule\0")
    tree = runner.run(["mktree", "-z", "--missing"], repository=origin,
                      input_data=b"".join(records)).strip()
    sha = runner.run(["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                      "commit-tree", tree.decode()], repository=origin,
                     input_data=b"Fixture local\n").decode().strip()
    runner.run(["update-ref", "refs/heads/main", sha], repository=origin)
    marker = tmp_path / "hook-executado"
    hooks = origin / "hooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "post-update").write_text(f"#!/bin/sh\ntouch '{marker.as_posix()}'\n")
    return SimpleNamespace(origin=origin, sha=sha, contents=contents, marker=marker)


@pytest.fixture
def local_store(tmp_path, local_repository):
    """Entrega store injetado e metadados públicos sem qualquer rede."""
    transport = LocalTransport(local_repository.origin)
    store = SnapshotStore(tmp_path / "cache", transport)
    repository = SimpleNamespace(id=42, private=False, full_name="fixture/public")
    calls = []

    def get_repo(repository_id):
        """Registra consulta de metadados para detectar uso indevido no cache hit."""
        calls.append(repository_id)
        return repository

    client = SimpleNamespace(get_repo=get_repo)
    return SimpleNamespace(store=store, transport=transport, client=client,
                           candidate={"repository_id": 42, "commit_sha": local_repository.sha},
                           calls=calls, repository=repository, fixture=local_repository)


def test_public_transport_rejects_local_and_authenticated_urls(tmp_path):
    """Impede acesso local e credenciais pela interface de produção."""
    transport = GitTransport()
    for url in (str(tmp_path), "file:///tmp/repo", "https://token@github.com/a/b.git",
                "https://example.com/a/b.git"):
        with pytest.raises(SnapshotUnavailable, match="URL pública"):
            transport.acquire(tmp_path / "unused", url, "a" * 40)
    assert not (tmp_path / "unused").exists()


def test_environment_isolated(monkeypatch, tmp_path):
    """Exclui configurações herdadas e ignora arquivos globais hostis."""
    global_config = tmp_path / "global.gitconfig"
    global_config.write_text("[alias]\n hostile = !exit 99\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "alias.hostile")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "!exit 98")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "inexistente"))
    runner = GitTransport()
    assert runner.preflight().startswith("git version")
    env = runner._environment()
    assert "GIT_CONFIG_COUNT" not in env
    assert "GIT_DIR" not in env
    with pytest.raises(SnapshotUnavailable):
        runner.run(["hostile"])


def test_wrong_sha_has_no_branch_fallback(local_store):
    """SHA indisponível falha uma vez e não publica snapshot de outra revisão."""
    state = local_store
    state.candidate["commit_sha"] = "b" * 40
    with pytest.raises(SnapshotUnavailable):
        state.store.acquire(state.candidate, state.client)
    assert state.transport.fetches == 1
    assert not list(state.store.root.rglob("complete.json"))
    assert not list(state.store.root.rglob("*.lock"))


@pytest.mark.parametrize(("limit", "value", "code"), [
    ("max_entries", 1, "SNAPSHOT_ENTRY_LIMIT"),
    ("max_blob_bytes", 1, "SNAPSHOT_BLOB_LIMIT"),
    ("max_disk_bytes", 1, "SNAPSHOT_DISK_LIMIT"),
])
def test_limits_never_publish(local_store, limit, value, code):
    """Orçamentos interrompem o candidato inteiro e removem staging próprio."""
    state = local_store
    setattr(state.transport, limit, value)
    with pytest.raises(SnapshotUnavailable) as captured:
        state.store.acquire(state.candidate, state.client)
    assert captured.value.code == code
    assert not list(state.store.root.rglob("complete.json"))
    assert not list(state.store.root.rglob("*.staging-*"))


@pytest.mark.parametrize("reason", ["cancel", "timeout", "disk"])
def test_running_process_is_killed_and_reaped(monkeypatch, tmp_path, reason):
    """Monitora subprocesso lento, drena pipes e recolhe o filho após aborto."""
    original = subprocess.Popen
    processes = []
    disk = tmp_path / "disk"
    disk.mkdir()
    script = ("import pathlib,sys,time; "
              "sys.stdout.write('x'*200000); sys.stdout.flush(); "
              "sys.stderr.write('y'*200000); sys.stderr.flush(); "
              "pathlib.Path(sys.argv[1]).write_bytes(b'z'*4096); time.sleep(10)")

    def spawn(command, **kwargs):
        """Executa somente programa controlado do teste em lugar do Git lento."""
        if command[0] == "taskkill":
            return original(command, **kwargs)
        process = original([sys.executable, "-c", script, str(disk / "growth")], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", spawn)
    transport = GitTransport(timeout=0.2 if reason == "timeout" else 5,
                             max_disk_bytes=100 if reason == "disk" else 100000)
    event = threading.Event()
    timer = threading.Timer(0.15, event.set)
    if reason == "cancel":
        timer.start()
    try:
        with pytest.raises(SnapshotUnavailable) as captured:
            transport.run(["--version"], cancel=event, disk_root=disk)
        assert captured.value.code == {"cancel": "SNAPSHOT_CANCELLED", "timeout": "SNAPSHOT_TIMEOUT",
                                       "disk": "SNAPSHOT_DISK_LIMIT"}[reason]
        assert processes and processes[0].poll() is not None
        assert processes[0].stdout.closed and processes[0].stdin.closed
    finally:
        timer.cancel()
        if reason == "cancel":
            timer.join()


def test_cancelled_store_cleans_staging(local_store, monkeypatch):
    """Cancelamento durante fetch remove a aquisição incompleta e a trava."""
    state = local_store

    def cancel_fetch(repository, url, sha, **options):
        """Cancela após a criação de staging para exercitar limpeza excepcional."""
        state.store.cancel()
        state.transport.check(options["cancel"], options["deadline"])

    monkeypatch.setattr(state.transport, "_fetch", cancel_fetch)
    with pytest.raises(SnapshotUnavailable) as captured:
        state.store.acquire(state.candidate, state.client)
    assert captured.value.code == "SNAPSHOT_CANCELLED"
    assert not list(state.store.root.rglob("*.staging-*"))
    assert not list(state.store.root.rglob("*.lock"))


def test_semaphore_wait_is_cancellable(local_store, monkeypatch):
    """Cancela uma espera por vaga antes de consultar metadados ou iniciar Git."""
    semaphore = threading.BoundedSemaphore(1)
    semaphore.acquire()
    monkeypatch.setattr(snapshot_store, "_ACQUISITIONS", semaphore)
    event = threading.Event()
    timer = threading.Timer(0.15, event.set)
    timer.start()
    try:
        with pytest.raises(SnapshotUnavailable) as captured:
            local_store.store.acquire(local_store.candidate, local_store.client, cancel=event)
        assert captured.value.code == "SNAPSHOT_CANCELLED"
        assert local_store.calls == []
        assert local_store.transport.fetches == 0
        assert not list(local_store.store.root.rglob("*.lock"))
    finally:
        timer.cancel()
        timer.join()
        semaphore.release()


def test_inherited_hooks_and_templates_never_execute(local_store, tmp_path, monkeypatch):
    """Hooks de transação e templates globais hostis ficam inertes no fetch."""
    hooks = tmp_path / "hostile-hooks"
    hooks.mkdir()
    marker = tmp_path / "executado"
    hook = hooks / "reference-transaction"
    hook.write_text(f"#!/bin/sh\necho executado > '{marker.as_posix()}'\n")
    hook.chmod(0o755)
    global_config = tmp_path / "hostile-config"
    global_config.write_text(f'[core]\n hooksPath = "{hooks.as_posix()}"\n'
                             f'[init]\n templateDir = "{hooks.as_posix()}"\n')
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(hooks))
    artifact = local_store.store.acquire(local_store.candidate, local_store.client)
    assert not marker.exists()
    assert not list((artifact.storage_path / "repository.git" / "hooks").glob("*"))


@pytest.mark.parametrize("budget", ["entries", "blobs"])
def test_inventory_aborts_before_stream_eof(local_store, tmp_path, monkeypatch, budget):
    """Produtor ainda ativo é encerrado ao revelar excesso, sem capturar toda a saída."""
    state = local_store
    artifact = state.store.acquire(state.candidate, state.client)
    original = subprocess.Popen
    children = []
    record = b"100644 blob " + b"a" * 40 + b" 40\ta.txt\0"
    marker = tmp_path / "stream-concluido"
    script = ("import os,pathlib,sys,time; os.write(1," + repr(record * 2) + "); "
              "time.sleep(10); pathlib.Path(sys.argv[1]).write_text('EOF')")

    def spawn(command, **kwargs):
        """Substitui apenas ls-tree por produtor controlado com EOF atrasado."""
        if "ls-tree" in command:
            child = original([sys.executable, "-c", script, str(marker)], **kwargs)
            children.append(child)
            return child
        return original(command, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", spawn)
    state.transport.max_entries = 1 if budget == "entries" else 100
    state.transport.max_blob_bytes = 1 if budget == "blobs" else 10000
    state.transport.timeout = 2
    start = time.monotonic()
    with pytest.raises(SnapshotUnavailable) as captured:
        state.transport.inventory(artifact.storage_path / "repository.git", artifact.commit_sha)
    assert captured.value.code == ("SNAPSHOT_ENTRY_LIMIT" if budget == "entries" else "SNAPSHOT_BLOB_LIMIT")
    assert time.monotonic() - start < 3
    assert children[0].poll() is not None
    assert children[0].stdout.closed and children[0].stdin.closed
    assert not marker.exists()


def test_common_output_has_explicit_capture_ceiling(monkeypatch):
    """Saída de comandos sem consumidor não pode crescer indefinidamente."""
    original = subprocess.Popen

    def spawn(command, **kwargs):
        """Produz somente saída controlada excedendo o teto de captura solicitado."""
        if command[0] == "taskkill":
            return original(command, **kwargs)
        return original([sys.executable, "-c", "import os; os.write(1,b'x'*200000)"], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", spawn)
    with pytest.raises(SnapshotUnavailable) as captured:
        GitTransport().run(["--version"], output_limit=1024)
    assert captured.value.code == "SNAPSHOT_OUTPUT_LIMIT"


@pytest.mark.parametrize("input_size", [0, 2 * 1024 * 1024])
def test_exited_parent_does_not_leave_descendant_pipe_open(monkeypatch, tmp_path, input_size):
    """Pai sai antes do timeout; descendente com stdout herdado deve ser encerrado."""
    original = subprocess.Popen
    roots = []
    pid_path = tmp_path / "descendant.pid"
    script = ("import pathlib,subprocess,sys; "
              "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(3)']); "
              "pathlib.Path(sys.argv[1]).write_text(str(child.pid))")
    baseline = {thread.ident for thread in threading.enumerate()}

    def spawn(command, **kwargs):
        """Injeta somente programa confiável com filho que mantém o pipe aberto."""
        if command[0] == "taskkill":
            return original(command, **kwargs)
        process = original([sys.executable, "-c", script, str(pid_path)], **kwargs)
        roots.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", spawn)
    start = time.monotonic()
    with pytest.raises(SnapshotUnavailable) as captured:
        GitTransport(timeout=0.3).run(["--version"], input_data=b"x" * input_size)
    elapsed = time.monotonic() - start
    assert captured.value.code == "SNAPSHOT_TIMEOUT"
    assert elapsed < 1.5
    assert roots[0].returncode == 0
    assert roots[0].stdin.closed and roots[0].stdout.closed
    assert not [thread for thread in threading.enumerate()
                if thread.ident not in baseline and thread.name.startswith("git-")]
    if os.name == "nt":
        assert roots[0]._handle.closed
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, int(pid_path.read_text()))
        if handle:
            try:
                assert kernel.WaitForSingleObject(handle, 0) == 0
            finally:
                kernel.CloseHandle(handle)


@pytest.mark.skipif(os.name != "nt", reason="Contagem de handles é específica do Windows.")
def test_windows_job_handles_released_after_exited_roots(monkeypatch, tmp_path):
    """Repetir a saída precoce não deixa jobs, threads ou handles de processos vivos."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    current = kernel.GetCurrentProcess()

    def count_handles():
        """Consulta o total real de handles pertencentes ao processo de testes."""
        count = wintypes.DWORD()
        assert kernel.GetProcessHandleCount(current, ctypes.byref(count))
        return count.value

    GitTransport().preflight()
    before = count_handles()
    for _ in range(3):
        test_exited_parent_does_not_leave_descendant_pipe_open(monkeypatch, tmp_path, 0)
        monkeypatch.undo()
    assert count_handles() <= before + 1


@pytest.mark.skipif(os.name != "nt", reason="Caminhos longos são específicos do Git para Windows.")
@pytest.mark.parametrize("length", ["boundary", "deep"])
def test_long_windows_cache_path_roundtrip(local_store, tmp_path, monkeypatch, length):
    """Adquire e reabre cache acima de MAX_PATH sem alterar configurações globais."""
    state = local_store
    if length == "deep":
        state.store.root = tmp_path / ("segment-" + "x" * 70) / ("segment-" + "y" * 70) / "cache"
    else:
        final = tmp_path / "42" / state.candidate["commit_sha"] / "repository.git"
        state.store.root = tmp_path / ("r" * (233 - len(str(final)) - 1))
    original = subprocess.Popen
    with tempfile.TemporaryFile() as diagnostics:
        def spawn(command, **kwargs):
            """Captura somente diagnósticos da fixture local para explicar regressões."""
            kwargs["stderr"] = diagnostics
            return original(command, **kwargs)

        monkeypatch.setattr(subprocess, "Popen", spawn)
        try:
            artifact = state.store.acquire(state.candidate, state.client)
            assert len(str(artifact.storage_path / "repository.git")) >= 233
            assert state.store.open_verified(artifact.reference()) == artifact
            assert state.store.read_blob(artifact, "binary.bin") == state.fixture.contents[b"binary.bin"]
        except SnapshotUnavailable:
            diagnostics.seek(0)
            pytest.fail(diagnostics.read().decode("utf-8", errors="replace"))


@pytest.mark.skipif(os.name != "nt", reason="Associação ao job é específica do Windows.")
def test_job_assignment_failure_never_runs_suspended_process(monkeypatch, tmp_path):
    """Falha antes de associar ao job mata o processo suspenso e libera seus handles."""
    original = subprocess.Popen
    roots = []
    marker = tmp_path / "executado"

    def spawn(command, **kwargs):
        """Injeta código confiável que denunciaria retomada anterior à contenção."""
        process = original([sys.executable, "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).touch()",
                            str(marker)], **kwargs)
        roots.append(process)
        return process

    def fail_assignment(job, process):
        """Simula recusa do sistema antes de a thread inicial ser retomada."""
        raise OSError("Associação ao job recusada pela fixture")

    monkeypatch.setattr(subprocess, "Popen", spawn)
    monkeypatch.setattr(git_transport._WindowsJob, "attach_and_resume", fail_assignment)
    with pytest.raises(SnapshotUnavailable) as captured:
        GitTransport().run(["--version"])
    assert captured.value.code == "SNAPSHOT_GIT_UNAVAILABLE"
    assert roots[0].returncode is not None
    assert roots[0]._handle.closed
    assert roots[0].stdin.closed and roots[0].stdout.closed
    assert not marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="Alias 8.3 é específico do Windows.")
def test_long_path_without_short_alias_has_explicit_error(monkeypatch, tmp_path):
    """Volume sem alias curto falha antes de iniciar subprocessos ou baixar objetos."""
    target = tmp_path / ("x" * 75) / ("y" * 75) / "repository.git"
    target.mkdir(parents=True)

    def unavailable(path, buffer, size):
        """Simula filesystem sem representação 8.3 disponível."""
        return 0

    monkeypatch.setattr(git_transport._KERNEL, "GetShortPathNameW", unavailable)
    with pytest.raises(SnapshotUnavailable) as captured:
        GitTransport().run(["rev-parse", "HEAD"], repository=target)
    assert captured.value.code == "SNAPSHOT_PATH_LIMIT"
