"""Publica e reabre snapshots completos com inventário verificável offline."""

import base64
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import threading
import time
import uuid

from . import config
from .git_transport import GitTransport
from .triage_snapshot import SnapshotUnavailable, _api_call


_ACQUISITIONS = threading.BoundedSemaphore(config.SNAPSHOT_MAX_CONCURRENT)
_KEY_LOCKS = {}
_KEY_GUARD = threading.Lock()


def _canonical(value):
    """Serializa JSON determinístico preservando os caminhos sem normalização."""
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _remove_readonly(operation, path, error_info):
    """Permite remover objetos Git somente leitura no staging próprio do Windows."""
    if not isinstance(error_info[1], PermissionError):
        raise error_info[1]
    Path(path).chmod(stat.S_IWRITE | stat.S_IREAD)
    operation(path)


@dataclass(frozen=True)
class SnapshotArtifact:
    """Identifica snapshot verificado e separa referência pública do caminho local."""

    repository_id: int
    commit_sha: str
    tree_oid: str
    manifest_sha256: str
    file_count: int
    total_blob_bytes: int
    storage_path: Path

    def reference(self) -> dict:
        """Produz referência portátil sem caminhos locais ou credenciais."""
        return {"format": "git-bare-v1", "cache_key": f"{self.repository_id}/{self.commit_sha}",
                "repository_id": self.repository_id, "commit_sha": self.commit_sha,
                "tree_oid": self.tree_oid, "manifest_sha256": self.manifest_sha256,
                "file_count": self.file_count, "total_blob_bytes": self.total_blob_bytes,
                "status": "complete"}


class SnapshotReader:
    """Fixa caminhos/OIDs de uma abertura verificada e usa um subprocesso por lote.

    O manifesto não é relido. Cada lote confere novamente configuração de
    armazenamento e hashes individuais, sem repetir fsck ou inventário.
    """

    def __init__(self, artifact, manifest, transport, cancel=None):
        """Retém somente entradas provenientes dos bytes validados na abertura."""
        self.artifact = artifact
        self._transport = transport
        self._cancel = cancel
        self._closed = False
        self._entries = {}
        for entry in manifest["entries"]:
            path = (entry["path"].encode("utf-8") if "path" in entry
                    else base64.b64decode(entry["path_base64"], validate=True))
            self._entries[path] = entry

    def __enter__(self):
        """Permite uso com with sem manter subprocesso ocioso aberto."""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Invalida o reader ao sair do bloco, inclusive em exceções."""
        self.close()

    def close(self):
        """Libera o inventário; subprocessos de cada lote são recolhidos pelo runner."""
        self._closed = True
        self._entries.clear()

    def read_blobs(self, paths):
        """Retorna dicionário caminho/bytes com um cat-file batch e OIDs fixados."""
        if self._closed:
            raise SnapshotUnavailable("SNAPSHOT_READER_CLOSED", "Reader já encerrado.")
        requested = []
        entries = []
        for path in paths:
            if len(requested) >= self._transport.max_entries:
                raise SnapshotUnavailable("SNAPSHOT_ENTRY_LIMIT", "Lote excede o limite de entradas.")
            key = path.encode("utf-8") if isinstance(path, str) else path
            entry = self._entries.get(key)
            if entry is None or entry["type"] != "blob":
                raise SnapshotUnavailable("unknown_path", "Caminho não corresponde a um blob versionado.")
            requested.append(path)
            entries.append(entry)
        contents = self._transport.read_blobs(self.artifact.storage_path / "repository.git", entries,
                                             cancel=self._cancel)
        return dict(zip(requested, contents))

    def read_blob(self, path):
        """Lê um caminho fixado; prefira read_blobs para agrupar arquivos."""
        return self.read_blobs([path])[path]


class SnapshotStore:
    """Gerencia publicação atômica, exclusão por chave e verificação local."""

    def __init__(self, root=config.SNAPSHOT_CACHE_PATH, transport=None):
        """Recebe raiz local e transporte substituível por fixture sem rede."""
        self.root = Path(root)
        self.transport = transport if transport is not None else GitTransport()

    def cancel(self):
        """Cancela subprocessos e esperas pertencentes ao transporte deste store."""
        self.transport.cancel()

    def _identity(self, candidate):
        """Recusa identidades ambíguas e componentes que escapariam da raiz."""
        repository_id = candidate.get("repository_id")
        sha = candidate.get("commit_sha")
        if type(repository_id) is not int or repository_id <= 0:
            raise SnapshotUnavailable("SNAPSHOT_INVALID_ID", "Identidade de repositório inválida.")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
            raise SnapshotUnavailable("missing_commit_sha", "SHA imutável ausente ou inválido.")
        return repository_id, sha.lower()

    def _path(self, repository_id, sha):
        """Obtém endereço lógico e recusa redirecionamentos por links locais."""
        path = self.root / str(repository_id) / sha
        for component in (self.root, *self.root.parents, path.parent, path):
            if component.is_symlink():
                raise SnapshotUnavailable("SNAPSHOT_CORRUPT", "Cache redirecionado por link local.")
        return path

    def _verify(self, path, repository_id, sha, *, include_manifest=False, **options):
        """Recalcula manifesto e integridade Git antes de aceitar qualquer cache hit."""
        try:
            if path.is_symlink() or any(item.is_symlink() for item in path.rglob("*")):
                raise SnapshotUnavailable("SNAPSHOT_CORRUPT", "Cache contém link de armazenamento.")
            raw = (path / "manifest.json").read_bytes()
            manifest = json.loads(raw)
            marker = json.loads((path / "complete.json").read_bytes())
            digest = hashlib.sha256(raw).hexdigest()
            tree, entries, total = self.transport.inventory(path / "repository.git", sha, **options)
            expected = {"format": "git-bare-v1", "repository_id": repository_id,
                        "commit_sha": sha, "tree_oid": tree, "entries": entries}
            artifact = SnapshotArtifact(repository_id, sha, tree, digest, len(entries), total, path)
            if raw != _canonical(expected) or manifest != expected or marker != artifact.reference():
                raise SnapshotUnavailable("SNAPSHOT_CORRUPT", "Manifesto ou marcador divergente dos objetos.")
            return (artifact, manifest) if include_manifest else artifact
        except (OSError, ValueError, KeyError, TypeError) as error:
            if isinstance(error, SnapshotUnavailable):
                raise
            raise SnapshotUnavailable("SNAPSHOT_CORRUPT", "Snapshot ausente, incompleto ou inválido.") from error

    def acquire(self, candidate, client, *, cancel=None):
        """Reutiliza cache íntegro ou realiza uma única aquisição pública e atômica."""
        repository_id, sha = self._identity(candidate)
        final = self._path(repository_id, sha)
        deadline = time.monotonic() + self.transport.timeout
        options = {"cancel": cancel, "deadline": deadline}
        with _KEY_GUARD:
            lock = _KEY_LOCKS.setdefault(str(final.absolute()), threading.Lock())
        while not lock.acquire(timeout=0.05):
            self.transport.check(cancel, deadline)
        try:
            self.transport.check(cancel, deadline)
            if final.exists():
                return self._verify(final, repository_id, sha, **options)
            final.parent.mkdir(parents=True, exist_ok=True)
            guard = final.with_name(sha + ".lock")
            try:
                guard.mkdir()
            except FileExistsError as error:
                if final.exists():
                    return self._verify(final, repository_id, sha, **options)
                raise SnapshotUnavailable("SNAPSHOT_BUSY", "Aquisição concorrente para a mesma chave.") from error
            staging = final.with_name(sha + ".staging-" + uuid.uuid4().hex)
            held = False
            try:
                while not _ACQUISITIONS.acquire(timeout=0.05):
                    self.transport.check(cancel, deadline)
                held = True
                self.transport.check(cancel, deadline)
                repository = _api_call(client.get_repo, repository_id)
                self.transport.check(cancel, deadline)
                if repository.id != repository_id:
                    raise SnapshotUnavailable("repository_mismatch", "Identidade do repositório divergente.")
                if repository.private:
                    raise SnapshotUnavailable("repository_not_public", "O repositório não está público.")
                full_name = repository.full_name
                if (not isinstance(full_name, str)
                        or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", full_name)
                        or any(part in {".", ".."} for part in full_name.split("/"))):
                    raise SnapshotUnavailable("SNAPSHOT_INVALID_URL", "Nome público de repositório inválido.")
                staging.mkdir()
                options["disk_root"] = staging
                bare = staging / "repository.git"
                self.transport.acquire(bare, f"https://github.com/{full_name}.git", sha, **options)
                tree, entries, total = self.transport.inventory(bare, sha, **options)
                raw = _canonical({"format": "git-bare-v1", "repository_id": repository_id,
                                  "commit_sha": sha, "tree_oid": tree, "entries": entries})
                artifact = SnapshotArtifact(repository_id, sha, tree, hashlib.sha256(raw).hexdigest(),
                                            len(entries), total, staging)
                (staging / "manifest.json").write_bytes(raw)
                self.transport.check(cancel, deadline, staging)
                (staging / "complete.json").write_bytes(_canonical(artifact.reference()))
                self._verify(staging, repository_id, sha, **options)
                if final.exists():
                    return self._verify(final, repository_id, sha, cancel=cancel, deadline=deadline)
                try:
                    staging.rename(final)
                except OSError:
                    if final.exists():
                        return self._verify(final, repository_id, sha, cancel=cancel, deadline=deadline)
                    raise
                return SnapshotArtifact(repository_id, sha, tree, artifact.manifest_sha256,
                                        len(entries), total, final)
            finally:
                if held:
                    _ACQUISITIONS.release()
                try:
                    if staging.exists():
                        shutil.rmtree(staging, onerror=_remove_readonly)
                finally:
                    guard.rmdir()
        finally:
            lock.release()

    def open_verified(self, reference):
        """Abre referência somente offline; nunca consulta API nem readquire objetos."""
        return self._open_manifest(reference)[0]

    def _open_manifest(self, reference, *, cancel=None):
        """Retorna o artefato e o manifesto dos mesmos bytes efetivamente validados."""
        repository_id, sha = self._identity(reference)
        artifact, manifest = self._verify(self._path(repository_id, sha), repository_id, sha,
                                          include_manifest=True, cancel=cancel)
        if reference != artifact.reference():
            raise SnapshotUnavailable("SNAPSHOT_REFERENCE_MISMATCH", "Referência diverge do snapshot verificado.")
        return artifact, manifest

    def open_reader(self, reference, *, cancel=None):
        """Verifica uma vez e fixa o manifesto para múltiplos lotes offline."""
        if isinstance(reference, SnapshotArtifact):
            reference = reference.reference()
        artifact, manifest = self._open_manifest(reference, cancel=cancel)
        return SnapshotReader(artifact, manifest, self.transport, cancel)

    def read_manifest(self, artifact):
        """Retorna inventário verificado com path UTF-8 ou path_base64 para bytes Git."""
        return self._open_manifest(artifact.reference())[1]

    def list_entries(self, artifact):
        """Lista blobs e gitlinks sem seguir links ou omitir arquivos binários."""
        return self.read_manifest(artifact)["entries"]

    def read_blob(self, artifact, path):
        """Lê bytes de um caminho str ou bytes, inclusive o texto de links simbólicos."""
        with self.open_reader(artifact) as reader:
            return reader.read_blob(path)
