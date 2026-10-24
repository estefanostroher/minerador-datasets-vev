"""Strict catalog loading and atomic publication, without project version resolution."""

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from robo_minerador.identity import stable_digest

Version = Annotated[str, StringConstraints(pattern=r"^\d+\.\d+\.\d+$")]
Hash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class BaseSource(StrictModel):
    source: str
    version: Version

    @model_validator(mode="after")
    def exact_tag(self) -> Self:
        if not self.source.endswith(
            f":{self.version}-"
            + ("slim-bookworm" if self.source.startswith("python:") else "bookworm-slim")
        ):
            raise ValueError("base source must use an exact runtime version tag")
        return self


class PackageManager(StrictModel):
    package: Literal["npm", "pnpm", "yarn", "@yarnpkg/cli-dist"]
    command: Literal["npm", "pnpm", "yarn"]
    version: Version


class SourceCatalog(StrictModel):
    schema_version: Literal["1.0"]
    platform: Literal["linux/amd64"]
    default_node: Version
    uv: Version
    bases: dict[str, BaseSource]
    package_managers: dict[str, PackageManager]

    @model_validator(mode="after")
    def required_matrix(self) -> Self:
        if set(self.bases) != {"python", "node20", "node22", "node24"}:
            raise ValueError("catalog requires Python and all three Node bases")
        if set(self.package_managers) != {"npm", "pnpm", "yarn1", "yarn4"}:
            raise ValueError("catalog requires all four package manager variants")
        if self.default_node not in {b.version for k, b in self.bases.items() if k != "python"}:
            raise ValueError("default_node is absent from the catalog")
        return self


def valid_digest(value: str) -> bool:
    return bool(re.fullmatch(r"sha256:[0-9a-f]{64}", value)) and value != "sha256:" + "0" * 64


class LockedBase(StrictModel):
    source: str
    ref: str
    image_id: str

    @model_validator(mode="after")
    def require_digests(self) -> Self:
        if "@" not in self.ref or not valid_digest(self.ref.rsplit("@", 1)[1]):
            raise ValueError("missing or invalid registry digest")
        if self.ref.split("@")[0] != self.source.rsplit(":", 1)[0]:
            raise ValueError("digest repository does not match source repository")
        if not valid_digest(self.image_id):
            raise ValueError("missing or invalid image ID")
        return self


class LockedRuntime(StrictModel):
    image_id: str
    tag: str
    base: str
    recipe_hash: Hash
    versions: dict[str, Version] = Field(min_length=2)
    outputs: dict[str, str] = Field(min_length=2)

    @model_validator(mode="after")
    def require_evidence(self) -> Self:
        if not valid_digest(self.image_id):
            raise ValueError("missing or invalid derived image ID")
        if self.versions.keys() != self.outputs.keys():
            raise ValueError("every tool needs measured version evidence")
        for tool, version in self.versions.items():
            expected = {"node": f"v{version}", "python": f"Python {version}"}.get(tool, version)
            if tool == "uv":
                if not self.outputs[tool].startswith(f"uv {version} "):
                    raise ValueError("uv version evidence mismatch")
            elif self.outputs[tool] != expected:
                raise ValueError(f"{tool} version evidence mismatch")
        return self


class EnvironmentLock(StrictModel):
    schema_version: Literal["1.0"]
    platform: Literal["linux/amd64"]
    source_hash: Hash
    bases: dict[str, LockedBase]
    runtimes: dict[str, LockedRuntime]

    @model_validator(mode="after")
    def complete_matrix(self) -> Self:
        if set(self.bases) != {"python", "node20", "node22", "node24"}:
            raise ValueError("missing base digest")
        expected = {"miner"} | {
            f"{node}-{pm}"
            for node in ("node20", "node22", "node24")
            for pm in ("npm", "pnpm", "yarn1", "yarn4")
        }
        if self.runtimes.keys() != expected:
            raise ValueError("missing runtime digest: incomplete matrix")
        for runtime in self.runtimes.values():
            if runtime.base not in self.bases:
                raise ValueError("runtime references unknown base")
        return self


def load_source(path: Path) -> SourceCatalog:
    return SourceCatalog.model_validate_json(path.read_bytes())


def load_lock(path: Path, source_path: Path) -> EnvironmentLock:
    lock = EnvironmentLock.model_validate_json(path.read_bytes())
    source = load_source(source_path)
    if lock.source_hash != stable_digest(source.model_dump(mode="json")):
        raise ValueError("lock source hash mismatch; freeze environments before collection")
    for name, base in source.bases.items():
        if lock.bases[name].source != base.source:
            raise ValueError("lock source image mismatch")
    for name, runtime in lock.runtimes.items():
        if name == "miner":
            versions = {"python": source.bases["python"].version, "uv": source.uv}
            base_name = "python"
        else:
            base_name, manager = name.split("-", 1)
            pm = source.package_managers[manager]
            versions = {
                "node": source.bases[base_name].version,
                "npm": source.package_managers["npm"].version,
                pm.command: pm.version,
            }
        if runtime.base != base_name or runtime.versions != versions:
            raise ValueError("lock source runtime mismatch")
    return lock


def write_lock(path: Path, lock: EnvironmentLock) -> None:
    """Replace only after validation and fsync; a failed freeze retains the prior lock."""
    EnvironmentLock.model_validate(lock.model_dump())
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=".environments-",
            delete=False,
        ) as f:
            temporary = Path(f.name)
            content = json.dumps(lock.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
            f.write(content.encode())
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
