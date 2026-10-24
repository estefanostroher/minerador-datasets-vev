"""Deterministic JSON identities without source-text or newline normalization."""

import hashlib
import json
from pathlib import PurePosixPath, PureWindowsPath


def stable_digest(value: object) -> str:
    """SHA-256 of compact, sorted, non-ASCII-escaped UTF-8 JSON; reject NaN/Infinity."""
    raw = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def occurrence_id(repo_id: int, commit_sha: str, path: str, start_offset: int) -> str:
    """Return a hex SHA-256 of the v1 occurrence payload.

    Payload keys: version (integer 1), repo_id (integer), commit_sha (verbatim),
    path (canonical repository-relative POSIX string), start_offset (zero-based
    UTF-16 code units in the original file, including original newlines).
    The caller supplies the parser's offset; this function never reads or rewrites
    source code. Noncanonical paths are rejected rather than normalized.
    """
    if type(repo_id) is not int:
        raise ValueError("repo_id must be an integer")
    if type(start_offset) is not int or start_offset < 0:
        raise ValueError("start_offset must be a nonnegative integer in UTF-16 code units")
    if (
        not path
        or path == "."
        or "\\" in path
        or "\x00" in path
        or PurePosixPath(path).is_absolute()
        or PureWindowsPath(path).drive
        or ".." in PurePosixPath(path).parts
        or str(PurePosixPath(path)) != path
    ):
        raise ValueError("path must be canonical repository-relative POSIX")
    return stable_digest(
        {
            "version": 1,
            "repo_id": repo_id,
            "commit_sha": commit_sha,
            "path": path,
            "start_offset": start_offset,
        }
    )
