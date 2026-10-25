"""Validated, execution-free view of the frozen runtime matrix and npm ranges."""

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from semantic_version import NpmSpec, Version  # type: ignore[import-untyped]
from semantic_version.base import Range  # type: ignore[import-untyped]

from robo_minerador.environments.lock import load_lock, load_source
from robo_minerador.identity import stable_digest


def _npm_comparators(branch: str) -> tuple[Range, ...]:
    """Expand the pinned NpmSpec grammar without its prerelease regrouping.

    Keep every bound in its original AND set. Generated exclusive upper bounds
    use '-0', npm's minimum prerelease, rather than admitting that next tuple's
    prereleases when another comparator explicitly mentions them.
    """
    if " - " in branch:
        low, high = branch.split(" - ")
        blocks = [">=" + low, "<=" + high]
    else:
        blocks = branch.split() or [">=0.0.0"]
    comparators = []
    for block in blocks:
        # NpmSpec(expression) validates these blocks before expansion.
        match = NpmSpec.Parser.NPM_SPEC_BLOCK.fullmatch(block)
        implicit_upper = match["op"] in {"^", "~"} or any(
            match[field] in {None, "x", "X", "*"} for field in ("major", "minor", "patch")
        )
        for bound in NpmSpec.Parser.parse_simple(block):
            target = bound.target
            if match["op"] == "~" and match["prerel"] and bound.operator == Range.OP_LT:
                # Version.next_minor() promotes an x.y.0 prerelease to x.y.0;
                # npm tilde's upper bound instead increments the stable minor.
                stable_bounds = NpmSpec.Parser.parse_simple(block.split("-", 1)[0])
                target = next(b.target for b in stable_bounds if b.operator == Range.OP_LT)
            if implicit_upper and bound.operator == Range.OP_LT and not target.prerelease:
                target = Version(str(target) + "-0")
            comparators.append(
                Range(bound.operator, target, prerelease_policy=Range.PRERELEASE_ALWAYS)
            )
    return tuple(comparators)


def npm_satisfies(version: str, expression: str) -> bool:
    """Match supported npm ranges with full AND bounds and per-OR prerelease gating."""
    # npm ignores build metadata in comparisons, including exact comparators.
    Version(version)
    NpmSpec(expression)
    parsed = Version(version.split("+", 1)[0])
    expression = re.sub(r"\+[0-9A-Za-z.-]+", "", expression)
    branches = [_npm_comparators(branch.strip()) for branch in expression.split("||")]
    for comparators in branches:
        if not all(bound.match(parsed) for bound in comparators):
            continue
        if not parsed.prerelease:
            return True
        if any(
            bound.target.prerelease and bound.target.truncate() == parsed.truncate()
            for bound in comparators
        ):
            return True
    return False


def choose_node(versions: Iterable[str], constraints: Iterable[str]) -> str:
    """Return the numerically highest common match; never substitute exact pins."""
    ranges = tuple(constraints)
    matches = [v for v in versions if all(npm_satisfies(v, r) for r in ranges)]
    if not matches:
        raise ValueError("no catalog Node version satisfies all constraints")
    return str(max(matches, key=Version))


@dataclass(frozen=True)
class Runtime:
    key: str
    node_version: str
    manager_variant: str
    manager_name: str
    manager_version: str
    base_ref: str
    base_image_id: str
    image_id: str
    recipe_hash: str
    versions: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Catalog:
    platform: str
    default_node: str
    source_hash: str
    digest: str
    runtimes: tuple[Runtime, ...]

    @property
    def node_versions(self) -> tuple[str, ...]:
        return tuple(sorted({r.node_version for r in self.runtimes}, key=Version))


def load_catalog(root: Path) -> Catalog:
    """Check source, lock evidence and both recipes before planning any project.

    This reads files only. Image availability is a later executor concern; no
    Docker connection, image pull or runtime measurement belongs in selection.
    """
    source_path = root / "config/environments.json"
    source = load_source(source_path)
    lock = load_lock(root / "config/environments.lock.json", source_path)
    for key, runtime in lock.runtimes.items():
        recipe = "miner" if key == "miner" else "runner"
        digest = hashlib.sha256((root / f"docker/{recipe}.Dockerfile").read_bytes()).hexdigest()
        if digest != runtime.recipe_hash:
            raise ValueError(f"catalog recipe hash mismatch: {key}")
    runtimes = []
    for key, runtime in sorted(lock.runtimes.items()):
        if key == "miner":
            continue
        variant = key.split("-", 1)[1]
        manager = source.package_managers[variant]
        base = lock.bases[runtime.base]
        runtimes.append(
            Runtime(
                key,
                runtime.versions["node"],
                variant,
                manager.command,
                manager.version,
                base.ref,
                base.image_id,
                runtime.image_id,
                runtime.recipe_hash,
                tuple(sorted(runtime.versions.items())),
            )
        )
    return Catalog(
        lock.platform,
        source.default_node,
        lock.source_hash,
        stable_digest(lock.model_dump(mode="json")),
        tuple(runtimes),
    )
