"""Docker-backed evidence collection, separate from catalog validation and the script."""

import hashlib
from pathlib import Path

from docker import DockerClient
from docker.models.images import Image

from robo_minerador.environments.lock import (
    EnvironmentLock,
    LockedBase,
    LockedRuntime,
    SourceCatalog,
    load_source,
    write_lock,
)
from robo_minerador.identity import stable_digest


def check_platform(image: Image) -> str:
    if (image.attrs["Os"], image.attrs["Architecture"]) != ("linux", "amd64"):
        raise ValueError(f"incorrect image platform: {image.id}")
    if image.id is None:
        raise ValueError("missing image ID")
    return image.id


def pull_bases(client: DockerClient, source: SourceCatalog) -> dict[str, LockedBase]:
    bases = {}
    for name, base in source.bases.items():
        print(f"Pulling {base.source} ({source.platform})", flush=True)
        # Freeze the registry index and the selected platform's local content ID.
        registry = client.images.get_registry_data(base.source)
        ref = f"{base.source.split(':')[0]}@{registry.id}"
        image = client.images.pull(ref, platform=source.platform)
        image_id = check_platform(image)
        bases[name] = LockedBase(source=base.source, ref=ref, image_id=image_id)
    return bases


def build_runtime(
    client: DockerClient,
    root: Path,
    source: SourceCatalog,
    *,
    name: str,
    base: str,
    recipe: str,
    tag: str,
    args: dict[str, str],
    versions: dict[str, str],
) -> LockedRuntime:
    print(f"Building {name}: {tag}", flush=True)
    image, logs = client.images.build(
        path=str(root),
        dockerfile=recipe,
        tag=tag,
        buildargs=args,
        platform=source.platform,
        rm=True,
        forcerm=True,
    )
    for entry in logs:
        if isinstance(entry, dict) and "error" in entry:
            raise RuntimeError(str(entry["error"]))
    image.reload()
    image_id = check_platform(image)
    outputs = {}
    for tool in versions:
        outputs[tool] = (
            client.containers.run(
                image_id,
                [tool, "--version"],
                remove=True,
                network_mode="none",
                platform=source.platform,
            )
            .decode()
            .strip()
        )
    return LockedRuntime(
        image_id=image_id,
        tag=tag,
        base=base,
        versions=versions,
        outputs=outputs,
        recipe_hash=hashlib.sha256((root / recipe).read_bytes()).hexdigest(),
    )


def freeze(client: DockerClient, root: Path) -> EnvironmentLock:
    source = load_source(root / "config/environments.json")
    client.ping()
    bases = pull_bases(client, source)
    runtimes = {
        "miner": build_runtime(
            client,
            root,
            source,
            name="miner",
            base="python",
            recipe="docker/miner.Dockerfile",
            tag=f"robo-minerador/miner:py{source.bases['python'].version}-uv{source.uv}",
            args={"PYTHON_BASE": bases["python"].ref, "UV_VERSION": source.uv},
            versions={"python": source.bases["python"].version, "uv": source.uv},
        )
    }
    for node in ("node20", "node22", "node24"):
        for manager, pm in source.package_managers.items():
            name = f"{node}-{manager}"
            runtimes[name] = build_runtime(
                client,
                root,
                source,
                name=name,
                base=node,
                recipe="docker/runner.Dockerfile",
                tag=f"robo-minerador/runner:node{source.bases[node].version}-{manager}-{pm.version}",
                args={
                    "NODE_BASE": bases[node].ref,
                    "NPM_VERSION": source.package_managers["npm"].version,
                    "PM_PACKAGE": pm.package,
                    "PM_VERSION": pm.version,
                },
                versions={
                    "node": source.bases[node].version,
                    "npm": source.package_managers["npm"].version,
                    pm.command: pm.version,
                },
            )
    lock = EnvironmentLock(
        schema_version="1.0",
        platform=source.platform,
        source_hash=stable_digest(source.model_dump(mode="json")),
        bases=bases,
        runtimes=runtimes,
    )
    write_lock(root / "config/environments.lock.json", lock)
    return lock
