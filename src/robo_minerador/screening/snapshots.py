"""Bounded tar snapshots backed by exact, content-addressed bytes.

Archive SHA-256 identifies transport bytes, never a Git commit. The tree digest
identifies the stripped, sorted manifest. Nothing is extracted onto the host.
"""

import base64
import gzip
import hashlib
import io
import json
import tarfile
import zlib
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from robo_minerador.discovery.github import GitHubResponse
from robo_minerador.identity import stable_digest
from robo_minerador.state.artifacts import ArtifactStore


class Repository(BaseModel):
    """Discovery manifest entry, including external and run-scoped identities."""

    model_config = ConfigDict(extra="allow", frozen=True)
    repo_id: int = Field(gt=0, strict=True)
    state_id: str = Field(min_length=1)
    full_name: str = Field(pattern=r"^[\w.-]+/[\w.-]+$")
    url: str
    commit_sha: str = Field(pattern=r"^[0-9a-fA-F]{40}$")
    default_branch: str
    stars: int | None = None


class SnapshotError(ValueError):
    """Unusable archive; its bytes remain available for auditing."""


@dataclass(frozen=True)
class SnapshotFile:
    path: str
    kind: Literal["file", "directory", "symlink"]
    size: int
    sha256: str
    artifact: str | None
    encoding: str | None
    mode: int
    target: str | None = None
    target_sha256: str | None = None


def _read_file(path: str, entries: dict[str, SnapshotFile], artifacts: ArtifactStore) -> bytes:
    seen = set()
    while path not in seen:
        seen.add(path)
        entry = entries.get(path)
        if entry is None or entry.kind == "directory":
            break
        if entry.kind == "file" and entry.artifact is not None:
            return artifacts.get(entry.artifact)
        path = _link_destination(path, entry.target or "", entries)
    raise KeyError(path)


@dataclass(frozen=True)
class Snapshot:
    repository: Repository
    archive_sha256: str
    tree_sha256: str
    files: tuple[SnapshotFile, ...]
    context_status: str
    response_artifact: str
    manifest_sha256: str

    def read(self, path: str, artifacts: ArtifactStore) -> bytes:
        return _read_file(path, {entry.path: entry for entry in self.files}, artifacts)

    def contents(self, artifacts: ArtifactStore) -> dict[str, bytes]:
        contents = {}
        entries = {entry.path: entry for entry in self.files}
        for entry in self.files:
            if entry.kind == "directory":
                continue
            try:
                contents[entry.path] = _read_file(entry.path, entries, artifacts)
            except KeyError:
                pass  # A directory symlink has no file body; its tree reference is retained.
        return contents


def _path(path: str) -> str:
    if (
        not path
        or "\\" in path
        or "\x00" in path
        or PureWindowsPath(path).drive
        or PurePosixPath(path).is_absolute()
        or any(part in ("", ".", "..") for part in path.split("/"))
        or any(":" in part for part in path.split("/"))
    ):
        raise SnapshotError("unsafe archive path")
    return path


def _encoding(content: bytes) -> str:
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return "base64"
    return "utf-8"


def _link_destination(path: str, target: str, entries: dict[str, SnapshotFile]) -> str:
    if not target or "\\" in target or PureWindowsPath(target).drive or target.startswith("/"):
        raise SnapshotError("external or inconclusive symlink")
    parts = path.split("/")[:-1]
    components = target.split("/")
    for index, part in enumerate(components):
        if part == "..":
            if not parts:
                raise SnapshotError("external symlink")
            parts.pop()
        elif part not in ("", "."):
            parts.append(part)
            intermediate = entries.get("/".join(parts))
            if index < len(components) - 1 and intermediate and intermediate.kind != "directory":
                raise SnapshotError("inconclusive symlink through non-directory component")
    return _path("/".join(parts)) if parts else "."


def _resolve_links(entries: dict[str, SnapshotFile]) -> None:
    directories = {"."} | {
        str(parent)
        for path in entries
        for parent in PurePosixPath(path).parents
        if str(parent) != "."
    }
    for path, entry in list(entries.items()):
        parents = PurePosixPath(path).parents
        if any(str(p) in entries and entries[str(p)].kind != "directory" for p in parents):
            raise SnapshotError("entry beneath file or symlink")
        if entry.kind != "symlink":
            continue
        seen = {path}
        dest = _link_destination(path, entry.target or "", entries)
        while dest in entries and entries[dest].kind == "symlink":
            if dest in seen:
                raise SnapshotError("cyclic symlink")
            seen.add(dest)
            dest = _link_destination(dest, entries[dest].target or "", entries)
        if dest not in entries and dest not in directories:
            raise SnapshotError("dangling or inconclusive symlink")
        if dest in directories or (dest in entries and entries[dest].kind == "directory"):
            target_hash = stable_digest(
                [
                    {"path": p, "kind": item.kind, "sha256": item.sha256, "mode": item.mode}
                    for p, item in sorted(entries.items())
                    if dest == "." or p.startswith(dest + "/")
                ]
            )
        else:
            target_hash = entries[dest].sha256
        entries[path] = SnapshotFile(**{**asdict(entry), "target_sha256": target_hash})


def _tar_files(
    raw: bytes, artifacts: ArtifactStore, max_bytes: int, max_entries: int
) -> tuple[SnapshotFile, ...]:
    if len(raw) > max_bytes:
        raise SnapshotError("archive byte limit")
    try:
        if raw.startswith(b"\x1f\x8b"):
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                tar_bytes = stream.read(max_bytes + 1)
        else:
            tar_bytes = raw
        if len(tar_bytes) > max_bytes:
            raise SnapshotError("expanded archive byte limit")
        entries: dict[str, SnapshotFile] = {}
        prefix: str | None = None
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as tar:
            for count, member in enumerate(tar, 1):
                if count > max_entries:
                    raise SnapshotError("archive entry limit")
                # Sparse holes expand logically beyond the physical tar budget.
                # This stage does not materialize or publish sparse file bodies.
                if member.issparse() or any(
                    key.startswith("GNU.sparse.") for key in member.pax_headers
                ):
                    raise SnapshotError("unsupported sparse archive entry")
                name = _path(member.name.rstrip("/") if member.isdir() else member.name)
                root, separator, path = name.partition("/")
                if prefix is None:
                    prefix = root
                if root != prefix or (not separator and not member.isdir()):
                    raise SnapshotError("archive outside single GitHub prefix root")
                if not separator:
                    continue
                path = _path(path)
                if path in entries:
                    raise SnapshotError("duplicate archive path")
                if member.size < 0 or member.size > max_bytes:
                    raise SnapshotError("file byte limit")
                artifact, encoding, target = None, None, None
                kind: Literal["file", "directory", "symlink"]
                if member.isfile():
                    file_stream = tar.extractfile(member)
                    if file_stream is None:
                        raise SnapshotError("unreadable archive file")
                    content = file_stream.read(member.size + 1)
                    if len(content) != member.size:
                        raise SnapshotError("truncated archive file")
                    artifact = artifacts.put(content)
                    digest, encoding, kind = artifact, _encoding(content), "file"
                elif member.issym():
                    target, kind = member.linkname, "symlink"
                    digest = hashlib.sha256(
                        target.encode("utf-8", errors="surrogateescape")
                    ).hexdigest()
                elif member.isdir():
                    digest, kind = stable_digest({"directory": path}), "directory"
                else:
                    raise SnapshotError("unsupported archive entry (hardlink/device/submodule)")
                entries[path] = SnapshotFile(
                    path, kind, member.size, digest, artifact, encoding, member.mode, target
                )
        if not entries:
            raise SnapshotError("empty archive")
        _resolve_links(entries)
        return tuple(entries[p] for p in sorted(entries))
    except (tarfile.TarError, gzip.BadGzipFile, zlib.error, EOFError, UnicodeError) as error:
        raise SnapshotError("corrupt archive") from error


def capture_snapshot(
    repository: Repository,
    response: GitHubResponse,
    artifacts: ArtifactStore,
    *,
    max_bytes: int,
    max_entries: int = 100_000,
) -> Snapshot:
    if max_bytes < 1 or max_entries < 1:
        raise ValueError("snapshot limits must be positive")
    expected = f"/repos/{repository.full_name}/tarball/{repository.commit_sha}"
    if response.envelope.get("endpoint") != expected or response.envelope.get("error"):
        raise SnapshotError("snapshot response is not bound to manifest commit")
    raw = base64.b64decode(response.envelope["body_base64"], validate=True)
    archive_digest = artifacts.put(raw)
    provenance = artifacts.put(
        json.dumps(
            response.envelope,
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    )
    files = _tar_files(raw, artifacts, max_bytes, max_entries)
    tree = [asdict(f) for f in files]
    context = "unsupported_context" if any(f.path == ".gitmodules" for f in files) else "complete"
    tree_digest = stable_digest(tree)
    model = {
        "kind": "snapshot",
        "schema_version": "1.0",
        "repository": repository.model_dump(mode="json"),
        "archive_sha256": archive_digest,
        "tree_sha256": tree_digest,
        "files": tree,
        "context_status": context,
        "response_artifact": provenance,
    }
    digest = artifacts.put(
        json.dumps(model, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    )
    return Snapshot(repository, archive_digest, tree_digest, files, context, provenance, digest)


def load_snapshot(artifacts: ArtifactStore, digest: str) -> Snapshot:
    """Reopen entirely offline, checking every referenced content hash and tree."""
    model: dict[str, Any] = json.loads(artifacts.get(digest))
    if model.get("kind") != "snapshot" or model.get("schema_version") != "1.0":
        raise SnapshotError("unsupported snapshot manifest")
    artifacts.get(model["archive_sha256"])
    artifacts.get(model["response_artifact"])
    if stable_digest(model["files"]) != model["tree_sha256"]:
        raise SnapshotError("snapshot tree mismatch")
    files = tuple(SnapshotFile(**item) for item in model["files"])
    for entry in files:
        _path(entry.path)
        if entry.artifact is not None:
            content = artifacts.get(entry.artifact)
            if entry.sha256 != entry.artifact or len(content) != entry.size:
                raise SnapshotError("snapshot file mismatch")
    return Snapshot(
        Repository.model_validate(model["repository"]),
        model["archive_sha256"],
        model["tree_sha256"],
        files,
        model["context_status"],
        model["response_artifact"],
        digest,
    )
