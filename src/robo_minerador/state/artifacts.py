"""Content-addressed binary storage, with flat paths root/<lowercase SHA-256>."""

import hashlib
import os
import re
import tempfile
from pathlib import Path


class ArtifactIntegrityError(ValueError):
    """Stored bytes do not match the requested content digest."""


class ArtifactStore:
    """Publish complete bytes by same-directory atomic hardlink, without newline conversion.

    Existing objects are verified before reuse; corrupt objects are never repaired
    implicitly, including when another writer creates the destination concurrently.
    Publication uses os.link(temp, destination), which never replaces a destination,
    then removes the temporary name. The filesystem must support hardlinks; errors
    propagate as OSError without an overwriting fallback. Failed writes remove only
    the temporary file created by that put.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, digest: str) -> Path:
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("digest must be 64 lowercase hexadecimal characters")
        return self.root / digest

    def get(self, digest: str) -> bytes:
        """Read bytes and verify SHA-256; missing objects raise FileNotFoundError."""
        content = self._path(digest).read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise ArtifactIntegrityError(f"artifact digest mismatch: {digest}")
        return content

    def put(self, content: bytes) -> str:
        """Store exact bytes, returning their digest only after successful publication."""
        digest = hashlib.sha256(content).hexdigest()
        destination = self._path(digest)
        if destination.exists():
            self.get(digest)
            return digest
        self.root.mkdir(parents=True, exist_ok=True)
        self._publish(content, destination)
        return digest

    def _publish(self, content: bytes, destination: Path) -> None:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.root, prefix=".artifact-", delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError:
                # A competing publication must pass the same integrity check as reuse.
                self.get(destination.name)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
