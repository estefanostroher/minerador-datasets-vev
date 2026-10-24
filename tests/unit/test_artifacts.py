import errno
import hashlib
import os
import tempfile
from pathlib import Path

import pytest

from robo_minerador.state.artifacts import ArtifactIntegrityError, ArtifactStore


@pytest.mark.parametrize("content", [b"", b"\x00\xff\x80\r\n", "ação 😀\r\n".encode()])
def test_binary_roundtrip_and_hash_reuse_are_idempotent(tmp_path, content):
    root = tmp_path / "artifacts"
    store = ArtifactStore(root)
    digest = store.put(content)
    assert digest == hashlib.sha256(content).hexdigest()
    assert store.put(content) == digest
    assert ArtifactStore(root).get(digest) == content
    assert list(root.iterdir()) == [root / digest]


def test_artifacts_keep_crlf_and_lf_distinct(tmp_path):
    store = ArtifactStore(tmp_path)
    crlf, lf = b"a\r\nb", b"a\nb"
    assert store.put(crlf) != store.put(lf)
    assert store.get(store.put(crlf)) == crlf


def test_get_and_put_detect_existing_corruption_without_overwriting_it(tmp_path):
    store = ArtifactStore(tmp_path)
    content = b"original"
    digest = store.put(content)
    (tmp_path / digest).write_bytes(b"corrupt")
    with pytest.raises(ArtifactIntegrityError):
        store.get(digest)
    with pytest.raises(ArtifactIntegrityError):
        store.put(content)
    assert (tmp_path / digest).read_bytes() == b"corrupt"


@pytest.mark.parametrize(
    "digest",
    ["../secret", "/tmp/secret", "C:\\secret", "a" * 63, "g" * 64, "A" * 64, "a" * 64 + "\n"],
)
def test_get_rejects_invalid_digests_before_filesystem_access(tmp_path, digest):
    with pytest.raises(ValueError):
        ArtifactStore(tmp_path / "absent").get(digest)
    assert not (tmp_path / "absent").exists()


def test_get_missing_artifact_reports_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        ArtifactStore(tmp_path).get("a" * 64)


@pytest.mark.parametrize("failure_point", ["fsync", "link"])
def test_failed_write_or_publication_leaves_no_partial_artifact(
    tmp_path, monkeypatch, failure_point
):
    sentinel = tmp_path / "user-data"
    sentinel.write_bytes(b"keep")

    def fail(*args, **kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(os, failure_point, fail)
    with pytest.raises(OSError, match="simulated disk failure"):
        ArtifactStore(tmp_path).put(b"not published")
    assert list(tmp_path.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"keep"


def test_publication_links_complete_tempfile_on_same_filesystem(tmp_path, monkeypatch):
    link = os.link
    content = b"complete\x00\xff"
    digest = hashlib.sha256(content).hexdigest()
    publications = []

    def inspect_link(source, destination):
        source, destination = Path(source), Path(destination)
        assert source.parent == destination.parent == tmp_path
        assert source.read_bytes() == content
        assert destination == tmp_path / digest
        assert not destination.exists()
        publications.append(destination)
        link(source, destination)
        assert source.exists()
        assert source.samefile(destination)

    monkeypatch.setattr(os, "link", inspect_link)
    assert ArtifactStore(tmp_path).put(content) == digest
    assert publications == [tmp_path / digest]
    assert list(tmp_path.iterdir()) == [tmp_path / digest]


def test_partial_write_failure_cleans_only_its_temporary_file(tmp_path, monkeypatch):
    create_temporary = tempfile.NamedTemporaryFile
    sentinel = tmp_path / "user-data"
    sentinel.write_bytes(b"keep")

    def failing_temporary(*args, **kwargs):
        stream = create_temporary(*args, **kwargs)

        def partial_write(content):
            stream.file.write(content[:3])
            raise OSError("partial write")

        monkeypatch.setattr(stream, "write", partial_write)
        return stream

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", failing_temporary)
    with pytest.raises(OSError, match="partial write"):
        ArtifactStore(tmp_path).put(b"incomplete")
    assert list(tmp_path.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"keep"


@pytest.mark.parametrize("published", [b"original", b"corrupt"])
def test_competing_publication_is_verified_before_reuse(tmp_path, monkeypatch, published):
    content = b"original"
    digest = hashlib.sha256(content).hexdigest()
    link = os.link

    def competing_link(source, destination):
        Path(destination).write_bytes(published)
        link(source, destination)

    monkeypatch.setattr(os, "link", competing_link)
    store = ArtifactStore(tmp_path)
    if published == content:
        assert store.put(content) == digest
    else:
        with pytest.raises(ArtifactIntegrityError):
            store.put(content)
    assert list(tmp_path.iterdir()) == [tmp_path / digest]
    assert (tmp_path / digest).read_bytes() == published


@pytest.mark.parametrize("posix_rename_semantics", [False, True])
@pytest.mark.parametrize("published", [b"original", b"corrupt"])
def test_destination_created_after_initial_check_is_never_overwritten(
    tmp_path, monkeypatch, published, posix_rename_semantics
):
    content = b"original"
    destination = tmp_path / hashlib.sha256(content).hexdigest()
    fsync = os.fsync

    def publish_competitor_after_sync(fd):
        fsync(fd)
        # The initial exists() check has finished, but publication has not started.
        destination.write_bytes(published)

    monkeypatch.setattr(os, "fsync", publish_competitor_after_sync)
    if posix_rename_semantics:
        # Exercise real overwrite semantics even on Windows, without fabricating EEXIST.
        monkeypatch.setattr(os, "rename", os.replace)
    store = ArtifactStore(tmp_path)
    if published == content:
        assert store.put(content) == destination.name
    else:
        with pytest.raises(ArtifactIntegrityError):
            store.put(content)
    assert destination.read_bytes() == published
    assert list(tmp_path.iterdir()) == [destination]


def test_filesystem_without_hardlinks_reports_error_without_overwrite_fallback(
    tmp_path, monkeypatch
):
    sentinel = tmp_path / "user-data"
    sentinel.write_bytes(b"keep")

    def unsupported_link(source, destination):
        raise OSError(errno.ENOTSUP, "hardlinks unsupported")

    monkeypatch.setattr(os, "link", unsupported_link)
    with pytest.raises(OSError, match="hardlinks unsupported") as error:
        ArtifactStore(tmp_path).put(b"not published")
    assert error.value.errno == errno.ENOTSUP
    assert list(tmp_path.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"keep"
