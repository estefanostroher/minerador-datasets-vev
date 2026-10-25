"""Lossless reference context, separate from prepared and observational overlays.

This is an extraction evidence manifest, not the future T17 dataset schema.
No project code is imported or executed while capturing or restoring it.
"""

import base64
import hashlib
import io
import json
import os
import re
import tarfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict

from robo_minerador.environments.plan import EnvironmentPlan
from robo_minerador.execution.docker import PreparedEnvironment
from robo_minerador.identity import stable_digest
from robo_minerador.screening.snapshots import (
    Snapshot,
    SnapshotFile,
    _path,
    _resolve_links,
    load_snapshot,
)
from robo_minerador.state.artifacts import ArtifactStore


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ContextFile(FrozenModel):
    path: str
    kind: Literal["file", "directory", "symlink"]
    mode: int
    size: int
    sha256: str
    artifact: str | None
    encoding: Literal["utf-8", "base64"] | None
    content: str | None
    target: str | None
    target_sha256: str | None


class ContextManifest(FrozenModel):
    kind: Literal["extraction_context"] = "extraction_context"
    schema_version: Literal["1.0"] = "1.0"
    snapshot_manifest: str
    archive_sha256: str
    tree_sha256: str
    repository_sha: str
    status: Literal["approved", "not_evaluated"]
    reasons: tuple[str, ...]
    files: tuple[ContextFile, ...]
    excluded_transport: tuple[str, ...]


@dataclass(frozen=True)
class PreservedContext:
    digest: str
    manifest: ContextManifest


class TargetSpan(FrozenModel):
    start_byte: int
    end_byte: int
    code: str
    sha256: str


def isolate_target(raw: bytes, start: int, end: int, code: str) -> TargetSpan:
    """Convert TypeScript UTF-16 offsets to exact UTF-8 byte bounds, including BOM."""
    text = raw.decode("utf-8", errors="strict")
    utf16 = text.encode("utf-16-le", errors="strict")
    if not 0 <= start <= end <= len(utf16) // 2:
        raise ValueError("invalid target bounds")
    prefix = utf16[: start * 2].decode("utf-16-le", errors="strict").encode("utf-8")
    selected = utf16[start * 2 : end * 2].decode("utf-16-le", errors="strict")
    body = selected.encode("utf-8")
    if selected != code or raw[len(prefix) : len(prefix) + len(body)] != body:
        raise ValueError("target code differs from original snapshot bytes")
    return TargetSpan(
        start_byte=len(prefix),
        end_byte=len(prefix) + len(body),
        code=selected,
        sha256=hashlib.sha256(body).hexdigest(),
    )


def verify_entries(files: tuple[SnapshotFile, ...]) -> None:
    """Validate portable logical paths and resolve every link against the whole tree."""
    entries: dict[str, SnapshotFile] = {}
    folded: dict[str, str] = {}
    directories = (
        {"."}
        | {f.path for f in files if f.kind == "directory"}
        | {str(parent) for f in files for parent in PurePosixPath(f.path).parents}
    )
    for entry in files:
        _path(entry.path)
        for path in (PurePosixPath(entry.path), *PurePosixPath(entry.path).parents):
            name = str(path)
            if name == ".":
                continue
            key = name.casefold()
            if key in folded and folded[key] != name:
                raise ValueError("case-colliding context paths")
            folded[key] = name
        if entry.path in entries:
            raise ValueError("duplicate context path")
        if entry.mode < 0 or entry.mode & ~0o7777:
            raise ValueError("invalid context mode")
        if entry.kind == "symlink":
            target = (entry.target or "").encode("utf-8", errors="surrogateescape")
            if hashlib.sha256(target).hexdigest() != entry.sha256:
                raise ValueError("symlink bytes mismatch")
            parts = entry.path.split("/")[:-1]
            components = (entry.target or "").split("/")
            for index, component in enumerate(components):
                if component == "..":
                    if not parts:
                        raise ValueError("external symlink")
                    parts.pop()
                elif component not in ("", "."):
                    parts.append(component)
                if index < len(components) - 1 and ("/".join(parts) or ".") not in directories:
                    raise ValueError("inconclusive symlink intermediate directory")
        entries[entry.path] = entry
    _resolve_links(entries)
    for entry in files:
        if entry.kind == "symlink" and entries[entry.path].target_sha256 != entry.target_sha256:
            raise ValueError("unverified symlink target hash")


def _reference(snapshot: Snapshot, artifacts: ArtifactStore) -> ContextManifest:
    verify_entries(snapshot.files)
    if load_snapshot(artifacts, snapshot.manifest_sha256) != snapshot:
        raise ValueError("snapshot differs from its immutable manifest")
    files = []
    excluded = []
    for entry in snapshot.files:
        # Only Git transport metadata is intrinsically disposable. Never infer
        # dependencies or fixtures from extensions, directory names or imports.
        if ".git" in entry.path.split("/"):
            excluded.append(entry.path)
            continue
        content: str | None = None
        encoding: Literal["utf-8", "base64"] | None = None
        if entry.kind == "file":
            if entry.artifact is None:
                raise ValueError("missing reference file artifact")
            raw = artifacts.get(entry.artifact)
            if len(raw) != entry.size or hashlib.sha256(raw).hexdigest() != entry.sha256:
                raise ValueError("reference file bytes mismatch")
            try:
                content, encoding = raw.decode("utf-8", errors="strict"), "utf-8"
            except UnicodeDecodeError:
                content, encoding = base64.b64encode(raw).decode("ascii"), "base64"
        files.append(
            ContextFile(
                path=entry.path,
                kind=entry.kind,
                mode=entry.mode,
                size=entry.size,
                sha256=entry.sha256,
                artifact=entry.artifact,
                encoding=encoding,
                content=content,
                target=entry.target,
                target_sha256=entry.target_sha256,
            )
        )
    kept = {entry.path for entry in files}
    # A link into discarded transport is no longer a reproducible internal link.
    verify_entries(tuple(entry for entry in snapshot.files if entry.path in kept))
    complete = snapshot.context_status == "complete"
    return ContextManifest(
        snapshot_manifest=snapshot.manifest_sha256,
        archive_sha256=snapshot.archive_sha256,
        tree_sha256=snapshot.tree_sha256,
        repository_sha=snapshot.repository.commit_sha,
        status="approved" if complete else "not_evaluated",
        reasons=("reference_bytes_preserved",) if complete else ("unsupported_context",),
        files=tuple(files),
        excluded_transport=tuple(excluded),
    )


def preserve_context(snapshot: Snapshot, artifacts: ArtifactStore) -> PreservedContext:
    """Preserve the complete reference tree; approval here means byte integrity only."""
    manifest = _reference(snapshot, artifacts)
    digest = artifacts.put(manifest.model_dump_json().encode("utf-8"))
    return PreservedContext(digest, manifest)


def load_context(artifacts: ArtifactStore, digest: str) -> PreservedContext:
    manifest = ContextManifest.model_validate_json(artifacts.get(digest))
    original = load_snapshot(artifacts, manifest.snapshot_manifest)
    if _reference(original, artifacts) != manifest:
        raise ValueError("context manifest differs from original reference")
    return PreservedContext(digest, manifest)


def restore_context(context: PreservedContext, artifacts: ArtifactStore, destination: Path) -> None:
    """Restore only into an owned empty directory after all evidence is verified.

    The caller must own/exclusively control the destination and its parent tree;
    this is not an extraction API into concurrently adversarial directories.
    """
    verified = load_context(artifacts, context.digest)
    if verified != context:
        raise ValueError("context handle differs from published manifest")
    if os.name == "nt" and any(
        file.kind == "symlink" and "/" in (file.target or "") for file in verified.manifest.files
    ):
        raise ValueError("POSIX symlink target bytes require a POSIX restoration filesystem")
    destination = destination.absolute()
    for parent in (destination, *destination.parents):
        if parent.is_symlink():
            raise ValueError("destination has symlink parent")
        if (
            parent in (destination, destination.parent)
            and parent.exists()
            and hasattr(os, "getuid")
            and parent.stat().st_uid != os.getuid()
        ):
            raise ValueError("destination parent ownership mismatch")
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("destination must be empty")
    if not destination.parent.is_dir():
        raise ValueError("destination parent must exist")
    # Read and hash-check everything before the first filesystem write.
    bodies = {
        file.path: artifacts.get(file.artifact)
        for file in verified.manifest.files
        if file.kind == "file" and file.artifact is not None
    }
    destination.mkdir(exist_ok=True)
    for file in verified.manifest.files:
        path = destination.joinpath(*file.path.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        if file.kind == "directory":
            path.mkdir(exist_ok=True)
        elif file.kind == "file":
            with path.open("xb") as stream:
                stream.write(bodies[file.path])
            path.chmod(file.mode & 0o777)
    # Links are last, so no write can traverse a freshly created link.
    for file in verified.manifest.files:
        if file.kind == "symlink":
            assert file.target is not None
            path = destination.joinpath(*file.path.split("/"))
            path.symlink_to(
                file.target, target_is_directory=(path.parent / file.target).resolve().is_dir()
            )
    for file in reversed(verified.manifest.files):
        if file.kind == "directory":
            destination.joinpath(*file.path.split("/")).chmod(file.mode & 0o777)


class PreparedContextManifest(FrozenModel):
    kind: Literal["prepared_context_derivation"] = "prepared_context_derivation"
    schema_version: Literal["1.0"] = "1.0"
    snapshot_manifest: str
    prepared_evidence: str
    prepared_archive: str
    plan_artifact: str
    plan_digest: str
    command_evidence: tuple[str, ...]
    external_dependency_roots: tuple[str, ...]
    excluded_dependencies: tuple[str, ...]
    files: tuple[ContextFile, ...]
    status: Literal["not_evaluated"] = "not_evaluated"
    reason: Literal["prepared_bytes_preserved_reproduction_pending"] = (
        "prepared_bytes_preserved_reproduction_pending"
    )


@dataclass(frozen=True)
class PreparedContext:
    digest: str
    manifest: PreparedContextManifest


def _prepared_files(raw: bytes, artifacts: ArtifactStore) -> tuple[SnapshotFile, ...]:
    """Read Docker's workspace archive without host extraction; expand hardlink data."""
    entries: dict[str, SnapshotFile] = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as tar:
        members = tar.getmembers()
        names: set[str] = set()
        for member in members:
            name = member.name.rstrip("/")
            if name in names:
                raise ValueError("duplicate prepared archive path")
            names.add(name)
            _path(name)
            if name == "workspace" and member.isdir():
                continue
            if not name.startswith("workspace/") or member.issparse():
                raise ValueError("unsupported prepared archive path or sparse file")
            path = _path(name[len("workspace/") :])
            kind: Literal["file", "directory", "symlink"]
            artifact, encoding, target = None, None, None
            if member.isfile() or member.islnk():
                if member.islnk():
                    _path(member.linkname)
                    if not member.linkname.startswith("workspace/"):
                        raise ValueError("external prepared hardlink")
                stream = tar.extractfile(member)
                if stream is None:
                    raise ValueError("unreadable prepared file")
                body = stream.read()
                artifact = artifacts.put(body)
                kind, digest, size = "file", artifact, len(body)
                try:
                    body.decode("utf-8", errors="strict")
                    encoding = "utf-8"
                except UnicodeDecodeError:
                    encoding = "base64"
            elif member.issym():
                target = member.linkname
                kind, size = "symlink", member.size
                digest = hashlib.sha256(
                    target.encode("utf-8", errors="surrogateescape")
                ).hexdigest()
            elif member.isdir():
                kind, size, digest = "directory", member.size, stable_digest({"directory": path})
            else:
                raise ValueError("unsupported prepared archive member")
            entries[path] = SnapshotFile(
                path, kind, size, digest, artifact, encoding, member.mode, target
            )
    _resolve_links(entries)
    result = tuple(entries[path] for path in sorted(entries))
    verify_entries(result)
    return result


def _unique_lock_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("dependency exclusion: ambiguous duplicate JSON key")
        result[key] = value
    return result


def _dependency_json(entry: SnapshotFile | None, artifacts: ArtifactStore) -> dict[str, Any]:
    if entry is None or entry.kind != "file" or entry.artifact is None:
        raise ValueError("dependency exclusion: missing regular lock or package metadata")
    try:
        value = json.loads(
            artifacts.get(entry.artifact).decode("utf-8"), object_pairs_hook=_unique_lock_object
        )
    except (UnicodeError, ValueError) as error:
        raise ValueError("dependency exclusion: invalid lock or package JSON") from error
    if not isinstance(value, dict):
        raise ValueError("dependency exclusion: expected lock or package JSON object")
    return value


def _npm_package_path(path: str) -> tuple[str, str]:
    """Find the owning npm package, including separately locked nested packages."""
    parts = path.split("/")
    owner: tuple[str, str] | None = None
    for index, part in enumerate(parts[:-1]):
        if part != "node_modules":
            continue
        end = index + (3 if parts[index + 1].startswith("@") else 2)
        if end <= len(parts):
            owner = ("/".join(parts[:end]), "/".join(parts[index + 1 : end]))
    if owner is None:
        raise ValueError("dependency exclusion: path is not owned by an npm package")
    return owner


def _verify_npm_exclusions(
    manager: str,
    entries: tuple[SnapshotFile, ...],
    artifacts: ArtifactStore,
    roots: tuple[str, ...],
) -> None:
    """Only npm v2/v3 packages entries prove this conservative exclusion subset.

    An explicit root is a request, not evidence. Match every excluded package
    path and installed identity to the effective lock. No network or package code
    is used, and symlink/local/unsupported dependency representations fail closed.
    """
    if not roots:
        return
    if manager != "npm":
        raise ValueError("dependency exclusion: unsupported lock proof for selected manager")
    by_path = {entry.path: entry for entry in entries}
    lock_path = "npm-shrinkwrap.json" if "npm-shrinkwrap.json" in by_path else "package-lock.json"
    lock = _dependency_json(by_path.get(lock_path), artifacts)
    if (
        type(lock.get("lockfileVersion")) is not int
        or lock["lockfileVersion"] not in (2, 3)
        or not isinstance(lock.get("packages"), dict)
    ):
        raise ValueError("dependency exclusion: unsupported npm lock packages structure")
    owners = {_npm_package_path(root) for root in roots}
    if {path for path, _ in owners} != set(roots):
        raise ValueError("dependency exclusion: root must be an exact locked package path")
    for entry in entries:
        if any(entry.path == root or entry.path.startswith(root + "/") for root in roots):
            if entry.kind == "symlink":
                raise ValueError(
                    "dependency exclusion: symlink or workspace contents are not external proof"
                )
            owners.add(_npm_package_path(entry.path))
    for path, path_name in sorted(owners):
        locked = lock["packages"].get(path)
        if not isinstance(locked, dict) or locked.get("link", False) is not False:
            raise ValueError(
                "dependency exclusion: package path missing from lock or is local link"
            )
        package = _dependency_json(by_path.get(path + "/package.json"), artifacts)
        name, version = package.get("name"), package.get("version")
        if (
            not isinstance(name, str)
            or name != locked.get("name", path_name)
            or not isinstance(version, str)
            or re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?", version) is None
            or version != locked.get("version")
        ):
            raise ValueError(
                "dependency exclusion: installed package name/version differs from lock"
            )
        resolved, integrity = locked.get("resolved"), locked.get("integrity")
        if not isinstance(resolved, str) or not isinstance(integrity, str):
            raise ValueError("dependency exclusion: missing external resolution or integrity")
        resolution = urlsplit(resolved)
        if (
            resolution.scheme != "https"
            or not resolution.hostname
            or "." not in resolution.hostname
            or not resolution.hostname.rsplit(".", 1)[-1].isalpha()
            or resolution.hostname.endswith((".localhost", ".local", ".internal", ".home.arpa"))
            or resolution.username is not None
            or resolution.password is not None
            or not resolution.path
            or resolution.fragment
            or "\\" in resolved
        ):
            raise ValueError("dependency exclusion: unsupported or local package resolution")
        algorithm, _, encoded = integrity.partition("-")
        lengths = {"sha256": 32, "sha384": 48, "sha512": 64}
        try:
            digest = base64.b64decode(encoded, validate=True)
        except ValueError as error:
            raise ValueError("dependency exclusion: invalid registry integrity") from error
        if (
            len(digest) != lengths.get(algorithm)
            or base64.b64encode(digest).decode("ascii") != encoded
        ):
            raise ValueError("dependency exclusion: unsupported or invalid registry integrity")


def _derive_prepared(
    snapshot: Snapshot,
    plan: dict[str, Any],
    plan_artifact: str,
    prepared: dict[str, Any],
    prepared_evidence: str,
    artifacts: ArtifactStore,
    roots: tuple[str, ...],
) -> PreparedContextManifest:
    _reference(snapshot, artifacts)
    if prepared["plan_digest"] != stable_digest(plan) or prepared["plan_artifact"] != plan_artifact:
        raise ValueError("prepared environment differs from frozen plan")
    if json.loads(artifacts.get(plan_artifact)) != plan:
        raise ValueError("frozen plan bytes mismatch")
    if prepared["parent_image_id"] != plan["derived_image_id"]:
        raise ValueError("prepared parent image mismatch")
    # T12 may accept canonical transport, so compare logical input entries as well
    # as the exact transport reference when it is the original GitHub archive.
    input_archive = prepared["input_archive_artifact"]
    artifacts.get(input_archive)
    if input_archive != snapshot.archive_sha256:
        input_files = _prepared_files(artifacts.get(input_archive), artifacts)
        expected = {(f.path, f.kind, f.mode, f.sha256) for f in snapshot.files}
        if {(f.path, f.kind, f.mode, f.sha256) for f in input_files} != expected:
            raise ValueError("prepared input archive differs from original snapshot")
    command_evidence = []
    for command in prepared["commands"]:
        digest = command["evidence_artifact"]
        evidence = json.loads(artifacts.get(digest))
        if evidence != {**command, "evidence_artifact": ""}:
            raise ValueError("prepared command evidence mismatch")
        expected_command = plan.get(command["phase"])
        if expected_command is None or any(
            command[key] != expected_command[key] for key in ("argv", "cwd", "timeout_seconds")
        ):
            raise ValueError("prepared command differs from frozen plan")
        if (
            command["exit_code"] != 0
            or command["timed_out"]
            or command["oom_killed"]
            or not command["storage_verified"]
        ):
            raise ValueError("prepared command did not complete verifiably")
        for key in (
            "stdout_artifact",
            "stderr_artifact",
            "workspace_artifact",
            "storage_observation_artifact",
            "container_inspect_artifact",
        ):
            artifacts.get(command[key])
        command_evidence.append(digest)
    expected_phases = [phase for phase in ("install", "build") if plan[phase] is not None]
    if [c["phase"] for c in prepared["commands"]] != expected_phases:
        raise ValueError("missing prepared command evidence")
    if (
        not prepared["commands"]
        or prepared["commands"][-1]["workspace_artifact"] != prepared["workspace_artifact"]
    ):
        raise ValueError("prepared archive is not the final command output")
    entries = _prepared_files(artifacts.get(prepared["workspace_artifact"]), artifacts)
    paths = {f.path for f in entries}
    for root in roots:
        _path(root)
        if root.split("/")[-1] == "node_modules":
            raise ValueError("cannot classify a whole node_modules tree as external")
        if any(f.path == root and f.kind == "symlink" for f in entries):
            raise ValueError("workspace symlinks are not external installed dependencies")
        if "node_modules" not in root.split("/") or not any(
            p.startswith(root + "/") for p in paths
        ):
            raise ValueError("external dependency root must identify installed package contents")
        if any(f.path == root or f.path.startswith(root + "/") for f in snapshot.files):
            raise ValueError("cannot exclude original snapshot dependencies")
    _verify_npm_exclusions(plan["manager_name"], entries, artifacts, roots)
    originals = {file.path for file in snapshot.files}
    for entry in entries:
        if (
            "node_modules" in entry.path.split("/")
            and entry.kind == "file"
            and entry.path not in originals
            and not any(entry.path.startswith(root + "/") for root in roots)
        ):
            raise ValueError("installed dependency requires explicit external classification")
    files, excluded = [], []
    for entry in entries:
        if any(entry.path == root or entry.path.startswith(root + "/") for root in roots):
            excluded.append(entry.path)
            continue
        if ".git" in entry.path.split("/"):
            continue
        content = None
        if entry.artifact:
            raw = artifacts.get(entry.artifact)
            content = (
                raw.decode("utf-8")
                if entry.encoding == "utf-8"
                else base64.b64encode(raw).decode("ascii")
            )
        files.append(
            ContextFile(
                path=entry.path,
                kind=entry.kind,
                mode=entry.mode,
                size=entry.size,
                sha256=entry.sha256,
                artifact=entry.artifact,
                encoding="utf-8"
                if entry.encoding == "utf-8"
                else "base64"
                if entry.artifact
                else None,
                content=content,
                target=entry.target,
                target_sha256=entry.target_sha256,
            )
        )
    return PreparedContextManifest(
        snapshot_manifest=snapshot.manifest_sha256,
        prepared_evidence=prepared_evidence,
        prepared_archive=prepared["workspace_artifact"],
        plan_artifact=plan_artifact,
        plan_digest=prepared["plan_digest"],
        command_evidence=tuple(command_evidence),
        external_dependency_roots=roots,
        excluded_dependencies=tuple(excluded),
        files=tuple(files),
    )


def preserve_prepared_context(
    snapshot: Snapshot,
    plan: EnvironmentPlan,
    prepared: PreparedEnvironment,
    artifacts: ArtifactStore,
    *,
    external_dependency_roots: tuple[str, ...] = (),
) -> PreparedContext:
    """Keep T12 output separate. Exclusion requests require matching npm lock proof.

    No dependency pruning is inferred from imports/extensions. The entire prepared
    archive remains in audit CAS for offline execution, including installed deps.
    Unsupported/local/unproven exclusion requests raise ValueError; reproduction
    is not approved by preservation, even when lock metadata permits exclusion.
    """
    data = json.loads(artifacts.get(prepared.evidence_artifact))
    if data != json.loads(json.dumps({**asdict(prepared), "evidence_artifact": ""})):
        raise ValueError("prepared environment differs from immutable evidence")
    manifest = _derive_prepared(
        snapshot,
        json.loads(json.dumps(asdict(plan))),
        prepared.plan_artifact,
        data,
        prepared.evidence_artifact,
        artifacts,
        external_dependency_roots,
    )
    return PreparedContext(artifacts.put(manifest.model_dump_json().encode()), manifest)


def load_prepared_context(artifacts: ArtifactStore, digest: str) -> PreparedContext:
    manifest = PreparedContextManifest.model_validate_json(artifacts.get(digest))
    expected = _derive_prepared(
        load_snapshot(artifacts, manifest.snapshot_manifest),
        json.loads(artifacts.get(manifest.plan_artifact)),
        manifest.plan_artifact,
        json.loads(artifacts.get(manifest.prepared_evidence)),
        manifest.prepared_evidence,
        artifacts,
        manifest.external_dependency_roots,
    )
    if expected != manifest:
        raise ValueError("prepared derivation differs from evidence")
    return PreparedContext(digest, manifest)
