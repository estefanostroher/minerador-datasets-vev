"""Valida persistência, portabilidade e recusa de snapshots adulterados."""

import base64
from concurrent.futures import ThreadPoolExecutor
import json
import stat
import shutil
from pathlib import Path
import time
import subprocess
import sys
import threading

import pytest

from robo_minerador.snapshot_store import SnapshotStore
from robo_minerador.triage_snapshot import SnapshotUnavailable
from test_git_transport import local_repository, local_store  # noqa: F401


def test_full_tree_bytes_and_portable_reference(local_store):
    """Preserva bytes, modos, caixa, Unicode e nomes que não são UTF-8."""
    state = local_store
    artifact = state.store.acquire(state.candidate, state.client)
    reference = artifact.reference()
    assert reference["format"] == "git-bare-v1"
    assert reference["cache_key"] == f"42/{artifact.commit_sha}"
    assert str(artifact.storage_path) not in json.dumps(reference)
    assert artifact.file_count == len(state.fixture.contents) + 1
    assert artifact.total_blob_bytes == sum(map(len, state.fixture.contents.values()))
    for path, expected in state.fixture.contents.items():
        assert state.store.read_blob(artifact, path) == expected
    entries = state.store.list_entries(artifact)
    assert any(entry.get("path_base64") == base64.b64encode(b"inv\xff.txt").decode()
               for entry in entries)
    assert next(entry for entry in entries if entry.get("path") == "link")["mode"] == "120000"
    assert next(entry for entry in entries if entry.get("path") == "submodule")["type"] == "commit"
    with pytest.raises(SnapshotUnavailable):
        state.store.read_blob(artifact, "submodule")
    assert not state.fixture.marker.exists()
    assert not (artifact.storage_path / "repository.git" / "hidden.txt").exists()


def test_cache_hit_and_offline_reopen_never_fetch(local_store):
    """Cache íntegro dispensa cliente e transporta referência para outro store."""
    state = local_store
    first = state.store.acquire(state.candidate, state.client)
    second = state.store.acquire(state.candidate, None)
    assert first == second
    assert state.calls == [42]
    assert state.transport.fetches == 1
    offline = SnapshotStore(state.store.root)
    assert offline.open_verified(first.reference()) == first
    assert offline.read_blob(first, "hidden.txt") == b"preservado"


@pytest.mark.parametrize("tamper", ["manifest", "marker", "entry", "ref", "objects", "config"])
def test_tampering_is_refused_without_refetch(local_store, tamper):
    """Manifesto, refs e objetos adulterados não viram hit nem refetch silencioso."""
    state = local_store
    artifact = state.store.acquire(state.candidate, state.client)
    path = artifact.storage_path
    if tamper in {"manifest", "entry"}:
        manifest = json.loads((path / "manifest.json").read_bytes())
        if tamper == "entry":
            manifest["entries"].pop()
        else:
            manifest["tree_oid"] = "c" * 40
        (path / "manifest.json").write_text(json.dumps(manifest))
    elif tamper == "marker":
        (path / "complete.json").write_text("{}")
    elif tamper == "ref":
        (path / "repository.git" / "refs" / "minerador" / "snapshot").write_text("d" * 40 + "\n")
    elif tamper == "objects":
        entry = next(entry for entry in state.store.list_entries(artifact) if entry.get("path") == "binary.bin")
        oid = entry["oid"]
        blob = path / "repository.git" / "objects" / oid[:2] / oid[2:]
        blob.chmod(stat.S_IWRITE | stat.S_IREAD)
        blob.write_bytes(b"objeto corrompido")
    else:
        (path / "repository.git" / "config").write_text("[include]\npath=/fora\n")
    with pytest.raises(SnapshotUnavailable):
        state.store.open_verified(artifact.reference())
    with pytest.raises(SnapshotUnavailable):
        state.store.acquire(state.candidate, None)
    assert state.transport.fetches == 1
    assert state.calls == [42]


def test_incomplete_final_and_abandoned_staging(local_store):
    """Staging abandonado não é cache hit; destino incompleto não é sobrescrito."""
    state = local_store
    final = state.store.root / "42" / state.candidate["commit_sha"]
    abandoned = final.with_name(final.name + ".staging-abandonado")
    abandoned.mkdir(parents=True)
    (abandoned / "manifest.json").write_text("{}")
    artifact = state.store.acquire(state.candidate, state.client)
    assert artifact.storage_path == final
    assert abandoned.exists()
    (final / "complete.json").unlink()
    with pytest.raises(SnapshotUnavailable):
        state.store.acquire(state.candidate, None)
    assert state.transport.fetches == 1


def test_cross_process_busy_does_not_remove_foreign_lock(local_store):
    """Trava externa produz diagnóstico estável e permanece intacta."""
    state = local_store
    guard = state.store.root / "42" / (state.candidate["commit_sha"] + ".lock")
    guard.mkdir(parents=True)
    with pytest.raises(SnapshotUnavailable) as captured:
        state.store.acquire(state.candidate, state.client)
    assert captured.value.code == "SNAPSHOT_BUSY"
    assert guard.exists()
    assert state.transport.fetches == 0


def test_same_key_threads_acquire_only_once(local_store):
    """Workers concorrentes compartilham a publicação completa por chave."""
    state = local_store
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(state.store.acquire, state.candidate, state.client) for _ in range(2)]
        first, second = [future.result() for future in futures]
    assert first == second
    assert state.transport.fetches == 1
    assert state.calls == [42]


@pytest.mark.parametrize("field", ["commit_sha", "tree_oid", "manifest_sha256", "cache_key", "file_count"])
def test_reference_cannot_redirect_snapshot(local_store, field):
    """Referência divergente é recusada sem aquisição automática."""
    state = local_store
    artifact = state.store.acquire(state.candidate, state.client)
    reference = artifact.reference()
    reference[field] = 999 if field == "file_count" else "e" * 40
    with pytest.raises(SnapshotUnavailable):
        state.store.open_verified(reference)
    assert state.transport.fetches == 1


def test_metadata_gate_blocks_private_and_mismatched_identity(local_store):
    """Aquisição inicial exige repositório público e identidade confirmada."""
    state = local_store
    state.repository.private = True
    with pytest.raises(SnapshotUnavailable) as captured:
        state.store.acquire(state.candidate, state.client)
    assert captured.value.code == "repository_not_public"
    state.repository.private = False
    state.repository.id = 99
    with pytest.raises(SnapshotUnavailable) as captured:
        state.store.acquire(state.candidate, state.client)
    assert captured.value.code == "repository_mismatch"
    assert state.transport.fetches == 0


def test_publication_collision_reuses_verified_winner(local_store, monkeypatch):
    """Colisão no rename valida o vencedor sem sobrescrever seu artefato."""
    state = local_store
    original = Path.rename
    collisions = []

    def collide(path, target):
        """Simula outro publicador concluindo imediatamente antes do rename."""
        if ".staging-" in path.name:
            shutil.copytree(path, target)
            collisions.append(target)
            raise FileExistsError("Publicação concorrente da fixture")
        return original(path, target)

    monkeypatch.setattr(Path, "rename", collide)
    artifact = state.store.acquire(state.candidate, state.client)
    assert collisions == [artifact.storage_path]
    assert state.store.open_verified(artifact.reference()) == artifact
    assert not list(state.store.root.rglob("*.staging-*"))


def test_received_different_commit_is_rejected(local_store, monkeypatch):
    """Mesmo um transporte que retorna outro commit não contorna a identidade."""
    state = local_store
    original = state.transport._fetch

    def wrong_commit(repository, url, sha, **options):
        """Entrega deliberadamente o commit disponível para o SHA errado."""
        original(repository, url, state.fixture.sha, **options)

    monkeypatch.setattr(state.transport, "_fetch", wrong_commit)
    state.candidate["commit_sha"] = "b" * 40
    with pytest.raises(SnapshotUnavailable) as captured:
        state.store.acquire(state.candidate, state.client)
    assert captured.value.code == "SNAPSHOT_SHA_MISMATCH"
    assert not list(state.store.root.rglob("complete.json"))


@pytest.mark.parametrize("operation", ["manifest", "blob", "reader"])
def test_manifest_race_uses_exact_validated_bytes(local_store, monkeypatch, operation):
    """Troca determinística após verificação não pode redirecionar o caminho lido."""
    state = local_store
    artifact = state.store.acquire(state.candidate, state.client)
    path = artifact.storage_path / "manifest.json"
    original = state.store._verify
    swapped = json.loads(path.read_bytes())
    first = next(entry for entry in swapped["entries"] if entry.get("path") == "hidden.txt")
    second = next(entry for entry in swapped["entries"] if entry.get("path") == "bom.txt")
    first["oid"], first["size"] = second["oid"], second["size"]

    def verify_then_swap(*args, **kwargs):
        """Adultera em disco exatamente entre a validação e o consumo do manifesto."""
        result = original(*args, **kwargs)
        path.write_text(json.dumps(swapped))
        return result

    monkeypatch.setattr(state.store, "_verify", verify_then_swap)
    if operation == "manifest":
        manifest = state.store.read_manifest(artifact)
        entry = next(entry for entry in manifest["entries"] if entry.get("path") == "hidden.txt")
        assert entry["oid"] != second["oid"]
    elif operation == "blob":
        assert state.store.read_blob(artifact, "hidden.txt") == b"preservado"
    else:
        with state.store.open_reader(artifact) as reader:
            assert reader.read_blob("hidden.txt") == b"preservado"


def test_lfs_pointer_is_explicit_without_payload_download(local_store):
    """Inventaria payload LFS separadamente do tamanho e dos bytes do ponteiro."""
    state = local_store
    artifact = state.store.acquire(state.candidate, state.client)
    entries = state.store.list_entries(artifact)
    pointer = next(entry for entry in entries if entry.get("path") == "asset.lfs")
    assert pointer["lfs_pointer"] == {"oid": "sha256:" + "a" * 64, "size": 9876543210}
    assert pointer["size"] == len(state.fixture.contents[b"asset.lfs"])
    invalid = next(entry for entry in entries if entry.get("path") == "not-lfs.txt")
    assert "lfs_pointer" not in invalid
    assert state.store.read_blob(artifact, "asset.lfs") == state.fixture.contents[b"asset.lfs"]
    assert artifact.total_blob_bytes < pointer["lfs_pointer"]["size"]
    assert not (artifact.storage_path / "repository.git" / "lfs").exists()


def test_reader_pins_paths_and_detects_changed_blob(local_store):
    """Manifesto em disco não muda a sessão; adulteração do blob é recusada."""
    state = local_store
    artifact = state.store.acquire(state.candidate, state.client)
    manifest = state.store.read_manifest(artifact)
    with state.store.open_reader(artifact) as reader:
        (artifact.storage_path / "manifest.json").write_text("{}")
        assert reader.read_blobs(["hidden.txt", b"bom.txt"]) == {
            "hidden.txt": b"preservado", b"bom.txt": state.fixture.contents[b"bom.txt"]}
        entry = next(entry for entry in manifest["entries"] if entry.get("path") == "hidden.txt")
        oid = entry["oid"]
        blob = artifact.storage_path / "repository.git" / "objects" / oid[:2] / oid[2:]
        blob.chmod(stat.S_IWRITE | stat.S_IREAD)
        blob.write_bytes(b"blob adulterado")
        with pytest.raises(SnapshotUnavailable):
            reader.read_blob("hidden.txt")
    with pytest.raises(SnapshotUnavailable) as captured:
        reader.read_blob("hidden.txt")
    assert captured.value.code == "SNAPSHOT_READER_CLOSED"


@pytest.mark.parametrize("count", [50, 100])
def test_batch_cost_is_one_verification_and_one_cat_file(local_store, monkeypatch, count):
    """Mede lotes reais e comprova ausência de fsck/inventário por arquivo."""
    state = local_store
    runner = state.transport
    origin = state.fixture.origin
    payload = bytes(range(256)) * 8
    oid = runner.run(["hash-object", "-w", "--stdin"], repository=origin, input_data=payload).strip()
    records = b"".join(b"100644 blob " + oid + f"\tfile-{index:03}.bin\0".encode()
                       for index in range(count))
    tree = runner.run(["mktree", "-z"], repository=origin, input_data=records).strip()
    sha = runner.run(["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                      "commit-tree", tree.decode()], repository=origin, input_data=b"Lote\n").decode().strip()
    state.candidate["commit_sha"] = sha
    artifact = state.store.acquire(state.candidate, state.client)
    commands = []
    original = runner.run

    def counted(args, **kwargs):
        """Conta as operações efetivamente executadas durante abertura e lote."""
        commands.append(args[0])
        return original(args, **kwargs)

    monkeypatch.setattr(runner, "run", counted)
    start = time.monotonic()
    with state.store.open_reader(artifact) as reader:
        opened = time.monotonic()
        result = reader.read_blobs([f"file-{index:03}.bin" for index in range(count)])
        finished = time.monotonic()
    assert len(result) == count
    assert all(content == payload for content in result.values())
    assert commands.count("fsck") == 1
    assert commands.count("ls-tree") == 1
    assert commands.count("cat-file") == 1
    print(f"lote={count} abertura={opened - start:.3f}s leitura={finished - opened:.3f}s "
          "fsck=1 inventario=1 cat-file=1")


def test_reader_batch_rechecks_content_hash(local_store, monkeypatch):
    """Frame com OID correto e bytes trocados não contorna a verificação individual."""
    state = local_store
    artifact = state.store.acquire(state.candidate, state.client)
    with state.store.open_reader(artifact) as reader:
        entry = reader._entries[b"hidden.txt"]

        def forged(args, **kwargs):
            """Entrega resposta de protocolo coerente em tamanho mas com hash incorreto."""
            content = b"x" * entry["size"]
            frame = f"{entry['oid']} blob {len(content)}\n".encode() + content + b"\n"
            kwargs["consume"](frame)
            return b""

        monkeypatch.setattr(state.transport, "run", forged)
        with pytest.raises(SnapshotUnavailable) as captured:
            reader.read_blob("hidden.txt")
        assert captured.value.code == "SNAPSHOT_CORRUPT"


def test_reader_batch_cancellation_reaps_process(local_store, monkeypatch):
    """Cancelamento durante batch recolhe o filho e fecha os pipes do runner."""
    state = local_store
    artifact = state.store.acquire(state.candidate, state.client)
    event = threading.Event()
    original = subprocess.Popen
    children = []

    def spawn(command, **kwargs):
        """Retarda somente cat-file; nenhum conteúdo candidato é executado."""
        if "cat-file" in command:
            process = original([sys.executable, "-c", "import time; time.sleep(10)"], **kwargs)
            children.append(process)
            return process
        return original(command, **kwargs)

    with state.store.open_reader(artifact, cancel=event) as reader:
        monkeypatch.setattr(subprocess, "Popen", spawn)
        timer = threading.Timer(0.15, event.set)
        timer.start()
        try:
            with pytest.raises(SnapshotUnavailable) as captured:
                reader.read_blobs(["hidden.txt", "bom.txt"])
            assert captured.value.code == "SNAPSHOT_CANCELLED"
            assert children[0].poll() is not None
            assert children[0].stdin.closed and children[0].stdout.closed
        finally:
            timer.cancel()
            timer.join()
