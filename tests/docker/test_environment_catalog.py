"""Catalog validation is offline; marked checks inspect existing Docker images only."""

import hashlib
import json
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock

import docker
import pytest
from pydantic import ValidationError

from robo_minerador.environments.freeze import freeze
from robo_minerador.environments.lock import EnvironmentLock, load_lock, write_lock

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("digest", [None, "", "sha256:fake", "sha256:" + "0" * 64])
def test_missing_or_fake_digest_blocks_catalog_loading(digest: str | None) -> None:
    data = json.loads((ROOT / "config/environments.lock.json").read_text())
    data["bases"]["python"]["ref"] = digest
    with pytest.raises(ValidationError):
        EnvironmentLock.model_validate(data)


@pytest.mark.parametrize("digest", [None, "", "sha256:fake", "sha256:" + "0" * 64])
def test_invalid_derived_digest_is_rejected(digest: str | None) -> None:
    data = json.loads((ROOT / "config/environments.lock.json").read_text())
    data["runtimes"]["node22-yarn4"]["image_id"] = digest
    with pytest.raises(ValidationError):
        EnvironmentLock.model_validate(data)


def test_digest_from_another_repository_is_rejected() -> None:
    data = json.loads((ROOT / "config/environments.lock.json").read_text())
    data["bases"]["python"]["ref"] = "node@" + data["bases"]["python"]["ref"].split("@")[1]
    with pytest.raises(ValidationError, match="repository"):
        EnvironmentLock.model_validate(data)


def test_atomic_publication_round_trip(tmp_path: Path) -> None:
    lock = load_lock(ROOT / "config/environments.lock.json", ROOT / "config/environments.json")
    target = tmp_path / "lock.json"
    write_lock(target, lock)
    assert load_lock(target, ROOT / "config/environments.json") == lock
    assert list(tmp_path.iterdir()) == [target]


def test_failed_pull_preserves_previous_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "environments.json").write_bytes((ROOT / "config/environments.json").read_bytes())
    target = config / "environments.lock.json"
    previous = (ROOT / "config/environments.lock.json").read_bytes()
    target.write_bytes(previous)

    def fail(*args: object) -> None:
        raise docker.errors.ImageNotFound("manifest unknown: planned tag does not exist")

    monkeypatch.setattr("robo_minerador.environments.freeze.pull_bases", fail)
    with pytest.raises(docker.errors.ImageNotFound, match="manifest unknown"):
        freeze(Mock(spec=docker.DockerClient), tmp_path)
    assert target.read_bytes() == previous
    assert sorted(p.name for p in config.iterdir()) == [
        "environments.json",
        "environments.lock.json",
    ]


def test_invalid_source_is_rejected_before_docker_access(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.mkdir()
    source = json.loads((ROOT / "config/environments.json").read_text())
    source["platform"] = "linux/arm64"
    (config / "environments.json").write_text(json.dumps(source))
    client = Mock(spec=docker.DockerClient)
    with pytest.raises(ValidationError):
        freeze(client, tmp_path)
    client.ping.assert_not_called()
    assert not (config / "environments.lock.json").exists()


def test_missing_lock_blocks_loading(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_lock(tmp_path / "missing.json", ROOT / "config/environments.json")


def test_lock_matches_source_and_cannot_be_empty() -> None:
    lock = load_lock(ROOT / "config/environments.lock.json", ROOT / "config/environments.json")
    assert len(lock.runtimes) == 13
    data = lock.model_dump(mode="json")
    data["runtimes"] = {}
    with pytest.raises(ValidationError):
        EnvironmentLock.model_validate(data)


def test_modified_source_is_rejected(tmp_path: Path) -> None:
    source = json.loads((ROOT / "config/environments.json").read_text())
    source["default_node"] = "20.19.0"
    path = tmp_path / "source.json"
    path.write_text(json.dumps(source))
    with pytest.raises(ValueError, match="source"):
        load_lock(ROOT / "config/environments.lock.json", path)


def test_lock_records_current_recipes() -> None:
    lock = load_lock(ROOT / "config/environments.lock.json", ROOT / "config/environments.json")
    for name, runtime in lock.runtimes.items():
        recipe = "miner" if name == "miner" else "runner"
        content = (ROOT / f"docker/{recipe}.Dockerfile").read_bytes()
        assert runtime.recipe_hash == hashlib.sha256(content).hexdigest()


def test_atomic_publication_preserves_old_lock_on_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock = load_lock(ROOT / "config/environments.lock.json", ROOT / "config/environments.json")
    target = tmp_path / "lock.json"
    target.write_bytes(b"previous")

    def fail(*args: object) -> None:
        raise OSError("publication interrupted")

    monkeypatch.setattr("robo_minerador.environments.lock.os.replace", fail)
    with pytest.raises(OSError, match="publication interrupted"):
        write_lock(target, lock)
    assert target.read_bytes() == b"previous"
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.docker
def test_real_images_and_tool_versions_without_downloads() -> None:
    lock = load_lock(ROOT / "config/environments.lock.json", ROOT / "config/environments.json")
    with closing(docker.from_env()) as client:
        client.ping()  # Infrastructure failure must fail, never silently skip.
        for base in lock.bases.values():
            image = client.images.get(base.ref)
            assert image.id == base.image_id
            assert image.attrs["Os"] == "linux"
            assert image.attrs["Architecture"] == "amd64"
        for runtime in lock.runtimes.values():
            image = client.images.get(runtime.image_id)
            assert image.attrs["Os"] == "linux"
            assert image.attrs["Architecture"] == "amd64"
            assert not image.attrs["Config"].get("Volumes")
            environment = image.attrs["Config"].get("Env", [])
            assert not any(item.startswith("GITHUB_TOKEN=") for item in environment)
            if runtime.base != "python":
                client.containers.run(
                    runtime.image_id,
                    ["sh", "-c", 'test ! -e /var/run/docker.sock && test -z "${GITHUB_TOKEN+x}"'],
                    remove=True,
                    network_mode="none",
                    platform=lock.platform,
                )
            for tool, version in runtime.versions.items():
                output = (
                    client.containers.run(
                        runtime.image_id,
                        [tool, "--version"],
                        remove=True,
                        network_mode="none",
                        platform=lock.platform,
                    )
                    .decode()
                    .strip()
                )
                assert output == runtime.outputs[tool]
                assert version in output
