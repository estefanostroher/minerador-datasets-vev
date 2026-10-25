"""Pure pre-installation environment resolution from T06 snapshot/command evidence."""

import hashlib
import json
import re
import tomllib
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Literal

import yaml

from robo_minerador.config import ExecutionConfig
from robo_minerador.environments.catalog import Catalog, Runtime, choose_node, npm_satisfies
from robo_minerador.identity import stable_digest
from robo_minerador.screening.commands import CommandPlan, plan_commands
from robo_minerador.state.artifacts import ArtifactStore


@dataclass(frozen=True)
class Constraint:
    source: str
    tool: str
    expression: str
    reason: str
    alternatives: tuple[str, ...] = ()


@dataclass(frozen=True)
class Limits:
    cpus: float
    memory_mb: int
    workspace_limit_mb: int


@dataclass(frozen=True)
class Command:
    argv: tuple[str, ...]
    cwd: str
    timeout_seconds: int


@dataclass(frozen=True)
class PackageMetadata:
    path: str
    scripts: tuple[tuple[str, str], ...]
    dependencies: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class EnvironmentPlan:
    platform: str
    node_version: str
    manager_name: str
    manager_variant: str
    manager_version: str
    node_defaulted: bool
    manager_defaulted: bool
    base_ref: str
    base_image_id: str
    derived_image_id: str
    versions: tuple[tuple[str, str], ...]
    constraints: tuple[Constraint, ...]
    install: Command
    build: Command | None
    suite: Command
    limits: Limits
    environment: tuple[tuple[str, str], ...]
    packages: tuple[PackageMetadata, ...]
    install_evidence: tuple[str, ...]
    later_metadata: tuple[str, ...]
    catalog_hash: str
    catalog_source_hash: str
    recipe_hash: str
    snapshot_hash: str
    command_plan_hash: str
    schema_version: str = "1.0"
    policy_version: str = "T07-v1"

    @property
    def digest(self) -> str:
        return stable_digest(asdict(self))


@dataclass(frozen=True)
class PlanResult:
    status: Literal["planned", "not_evaluated"]
    reason_code: str
    plan: EnvironmentPlan | None
    constraints: tuple[Constraint, ...]
    details: tuple[str, ...] = ()


def persist_plan(plan: EnvironmentPlan, artifacts: ArtifactStore) -> str:
    """Publish immutable plan bytes before handing the plan to a future executor."""
    raw = json.dumps(
        asdict(plan), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return artifacts.put(raw)


def _object(raw: bytes) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


def _relative(path: str) -> bool:
    return bool(
        path
        and path != "."
        and "\\" not in path
        and "\x00" not in path
        and not PurePosixPath(path).is_absolute()
        and not PureWindowsPath(path).drive
        and ".." not in PurePosixPath(path).parts
        and str(PurePosixPath(path)) == path
    )


def _text(value: object) -> str:
    if isinstance(value, str | int | float) and not isinstance(value, bool):
        return str(value).strip()
    raise ValueError("version declaration must be static text")


def _constraint(
    constraints: list[Constraint],
    source: str,
    tool: str,
    value: object,
    reason: str,
    alternatives: tuple[str, ...] = (),
) -> None:
    expression = _text(value)
    constraints.append(Constraint(source, tool, expression, reason, alternatives))
    # Parse now, even when another declaration already excludes every candidate.
    npm_satisfies("22.22.3", expression)


def _ci_constraints(contents: dict[str, bytes], constraints: list[Constraint]) -> None:
    for path, body in sorted(contents.items()):
        if not (path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml"))):
            continue
        workflow = yaml.safe_load(body)
        if not isinstance(workflow, dict) or not isinstance(workflow.get("jobs", {}), dict):
            raise ValueError(f"{path}: invalid CI YAML")
        for name, job in workflow.get("jobs", {}).items():
            if not isinstance(job, dict) or not isinstance(job.get("steps", []), list):
                raise ValueError(f"{path}: dynamic CI job")
            for index, step in enumerate(job.get("steps", [])):
                if not isinstance(step, dict) or not str(step.get("uses", "")).startswith(
                    "actions/setup-node@"
                ):
                    continue
                source = f"{path}#jobs.{name}.steps.{index}.with"
                config = step.get("with", {})
                if not isinstance(config, dict) or "node-version-file" in config:
                    raise ValueError(f"{source}: unresolved Node file reference")
                if "node-version" not in config:
                    continue
                version = _text(config["node-version"])
                matrix_ref = re.fullmatch(r"\$\{\{\s*matrix\.([\w-]+)\s*\}\}", version)
                if matrix_ref:
                    strategy = job.get("strategy", {})
                    matrix = strategy.get("matrix", {}) if isinstance(strategy, dict) else None
                    if not isinstance(matrix, dict) or "include" in matrix or "exclude" in matrix:
                        raise ValueError(f"{source}: unsupported matrix")
                    values = matrix.get(matrix_ref[1])
                    if not isinstance(values, list) or not values:
                        raise ValueError(f"{source}: unresolved matrix axis")
                    alternatives = tuple(_text(v) for v in values)
                    _constraint(
                        constraints,
                        source,
                        "node",
                        " || ".join(alternatives),
                        "CI matrix alternatives",
                        alternatives,
                    )
                else:
                    _constraint(constraints, source, "node", version, "single CI preference")


def _declarations(
    contents: dict[str, bytes],
    commands: CommandPlan,
    constraints: list[Constraint],
) -> list[tuple[str, str, str]]:
    managers: list[tuple[str, str, str]] = []
    for package in commands.packages:
        path = package.package_path
        model = _object(contents[path])
        engines = model.get("engines", {})
        volta = model.get("volta", {})
        if not isinstance(engines, dict) or not isinstance(volta, dict):
            raise ValueError(f"{path}: invalid engines/volta")
        for tool in ("node", "npm", "pnpm", "yarn"):
            if tool in engines:
                _constraint(
                    constraints,
                    f"{path}#engines.{tool}",
                    tool,
                    engines[tool],
                    "applicable root/workspace engine",
                )
            if tool in volta:
                source = f"{path}#volta.{tool}"
                if tool == "node":
                    _constraint(constraints, source, tool, volta[tool], "volta preference")
                else:
                    managers.append((source, tool, _text(volta[tool])))
        if "extends" in volta:
            raise ValueError(f"{path}: unresolved volta file reference")
        if "packageManager" in model:
            declaration = _text(model["packageManager"])
            match = re.fullmatch(
                r"(npm|pnpm|yarn)@(\d+\.\d+\.\d+)(?:\+sha(?:224|256|384|512)\.[0-9a-f]+)?",
                declaration,
            )
            if not match:
                raise ValueError(f"{path}: packageManager must be a supported exact version")
            managers.insert(0, (f"{path}#packageManager", match[1], match[2]))
        prefix = "" if package.cwd == "." else package.cwd + "/"
        for filename in (".nvmrc", ".node-version"):
            file = prefix + filename
            if file in contents:
                _constraint(
                    constraints,
                    file,
                    "node",
                    contents[file].decode().strip(),
                    filename + " preference",
                )
    for path in ("mise.toml", ".mise.toml"):
        if path in contents:
            model = tomllib.loads(contents[path].decode())
            tools = model.get("tools", {})
            if not isinstance(tools, dict):
                raise ValueError(f"{path}: invalid tools")
            if "node" in tools:
                _constraint(
                    constraints, path + "#tools.node", "node", tools["node"], "static TOML runtime"
                )
    _ci_constraints(contents, constraints)
    return managers


def _validate_yarn_interpolation(config: dict[str, Any], constraints: list[Constraint]) -> None:
    """Walk all mapping/list values without resolving variables from the host."""
    pending: list[tuple[object, str]] = [(config, ".yarnrc.yml#")]
    seen: set[int] = set()
    while pending:
        value, source = pending.pop()
        if isinstance(value, str) and "${" in value:
            constraints.append(
                Constraint(source, "yarn", value, "unresolved configuration interpolation")
            )
            raise ValueError(f"{source}: unresolved configuration interpolation: {value}")
        if isinstance(value, dict | list):
            # YAML aliases can revisit a container, including recursive anchors.
            if id(value) in seen:
                continue
            seen.add(id(value))
            if isinstance(value, dict):
                separator = "" if source.endswith("#") else "."
                pending.extend(
                    (item, f"{source}{separator}{key}") for key, item in reversed(value.items())
                )
            else:
                pending.extend(
                    (item, f"{source}[{index}]") for index, item in reversed(list(enumerate(value)))
                )


def _yarn_metadata(
    contents: dict[str, bytes],
    managers: list[tuple[str, str, str]],
    constraints: list[Constraint],
) -> bool:
    """Infer a vendored Yarn release only from a canonical filename, never JS."""
    modern = False
    if ".yarnrc.yml" in contents:
        modern = True
        config = yaml.safe_load(contents[".yarnrc.yml"])
        if not isinstance(config, dict):
            raise ValueError(".yarnrc.yml: invalid YAML mapping")
        _validate_yarn_interpolation(config, constraints)
        if "yarnPath" in config:
            path = _text(config["yarnPath"])
            if not _relative(path) or path not in contents:
                raise ValueError(".yarnrc.yml#yarnPath: missing/unsafe local release")
            match = re.fullmatch(r"yarn-(\d+\.\d+\.\d+)\.(?:cjs|js)", PurePosixPath(path).name)
            if not match:
                raise ValueError(".yarnrc.yml#yarnPath: ambiguous version metadata")
            managers.append((".yarnrc.yml#yarnPath", "yarn", match[1]))
    if ".yarnrc" in contents and re.search(r"\byarn-path\b", contents[".yarnrc"].decode()):
        raise ValueError(".yarnrc: unresolved legacy yarn-path")
    return modern


def _select_manager(
    contents: dict[str, bytes],
    catalog: Catalog,
    managers: list[tuple[str, str, str]],
    constraints: list[Constraint],
) -> tuple[str, bool, tuple[str, ...]]:
    modern_yarn = _yarn_metadata(contents, managers, constraints)
    locks = sorted(
        p
        for p in contents
        if PurePosixPath(p).name
        in {
            "package-lock.json",
            "npm-shrinkwrap.json",
            "pnpm-lock.yaml",
            "yarn.lock",
        }
    )
    if len(locks) > 1 or any("/" in p for p in locks):
        raise ValueError("multiple or nested locks: isolation scope is unresolved")
    locked_variant = None
    if locks:
        lock = locks[0]
        if lock.endswith(".json"):
            _object(contents[lock])
            locked_variant = "npm"
        elif lock == "pnpm-lock.yaml":
            if not isinstance(yaml.safe_load(contents[lock]), dict):
                raise ValueError("invalid pnpm lock YAML")
            locked_variant = "pnpm"
        else:
            body = contents[lock].decode()
            if re.search(r"^# yarn lockfile v1\s*$", body, re.M):
                locked_variant = "yarn1"
            else:
                metadata = yaml.safe_load(body)
                if not isinstance(metadata, dict) or not isinstance(
                    metadata.get("__metadata"), dict
                ):
                    raise ValueError("yarn.lock: unknown lock format")
                locked_variant = "yarn4"
    variants = {r.manager_variant: (r.manager_name, r.manager_version) for r in catalog.runtimes}
    selected = None
    for source, name, version in managers:
        _constraint(constraints, source, name, version, "exact manager declaration")
        matching = [key for key, value in variants.items() if value == (name, version)]
        if len(matching) != 1:
            raise ValueError(f"{source}: exact manager absent from catalog")
        if selected is not None and selected != matching[0]:
            raise ValueError(f"{source}: contradictory manager declarations")
        selected = matching[0]
    if selected and locked_variant and selected != locked_variant:
        raise ValueError("manager declaration contradicts lock format")
    selected = selected or locked_variant or ("yarn4" if modern_yarn else "npm")
    if modern_yarn and selected != "yarn4":
        raise ValueError(".yarnrc.yml contradicts manager/lock")
    if locks:
        constraints.append(
            Constraint(
                locks[0], variants[selected][0], variants[selected][1], "unambiguous lock format"
            )
        )
    return selected, not managers and not locks and not modern_yarn, tuple(locks)


def _install_command(
    contents: dict[str, bytes],
    runtime: Runtime,
    locks: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if locks:
        argv = {
            "npm": ("npm", "ci"),
            "pnpm": ("pnpm", "install", "--frozen-lockfile"),
            "yarn1": ("yarn", "install", "--frozen-lockfile"),
            "yarn4": ("yarn", "install", "--immutable"),
        }[runtime.manager_variant]
        return argv, locks
    manager = runtime.manager_name
    # A literal standalone install instruction is the supported evidence subset.
    # Merely mentioning the manager or a dependency never authorizes lock creation.
    instruction = re.compile(
        rf"^(?:Install dependencies with|Run|Instale as dependências com|Execute) "
        rf"`{manager} install`[.!]?\s*$|^\s*{manager} install\s*$",
        re.M,
    )
    evidence = tuple(
        sorted(
            p
            for p, body in contents.items()
            if "/" not in p
            and PurePosixPath(p).name.lower().startswith("readme")
            and instruction.search(body.decode())
        )
    )
    model = _object(contents["package.json"])
    policy = (
        model.get("config", {}).get("robo_minerador", {})
        if isinstance(model.get("config", {}), dict)
        else {}
    )
    if isinstance(policy, dict) and policy.get("allow_lockfile_generation") is True:
        evidence += ("package.json#config.robo_minerador.allow_lockfile_generation",)
    if not evidence:
        raise ValueError("no lockfile and no explicit supported installation procedure")
    argv = (manager, "install")
    if runtime.manager_variant == "pnpm":
        argv += ("--no-frozen-lockfile",)
    elif runtime.manager_variant == "yarn4":
        argv += ("--no-immutable",)
    return argv, evidence


def _suite(commands: CommandPlan, manager: str) -> tuple[str, ...]:
    if manager != "npm" and any(
        name in package.scripts for package in commands.packages for name in ("pretest", "posttest")
    ):
        raise ValueError("npm lifecycle semantics cannot be preserved under the selected manager")
    if commands.suite_argv:
        argv = commands.suite_argv
        if argv[0] != "npm":
            raise ValueError("unsupported T06 command adapter")
        if argv == ("npm", "test") and commands.workspaces and manager != "npm":
            raise ValueError("npm workspace aggregator cannot run under another manager")
        if manager == "npm":
            return argv
        # Yarn and pnpm pass run-script arguments directly (npm requires '--').
        return (manager, "run", "test", *(a for a in argv[2:] if a != "--"))
    if len(commands.frameworks) != 1:
        raise ValueError("framework command scope unresolved")
    flags = (
        ("--ci", "--watch=false", "--watchAll=false")
        if commands.frameworks[0] == "jest"
        else ("--run", "--watch=false")
    )
    return (manager, "run", "test", *flags)


def plan_environment(
    contents: dict[str, bytes],
    commands: CommandPlan,
    catalog: Catalog,
    execution: ExecutionConfig,
) -> PlanResult:
    """Select once from declarations before install; has no execution/result API.

    Callers must persist ``asdict(result.plan)`` and ``result.plan.digest`` before
    installation. Later derived locks and resolved trees are separate metadata,
    never inputs to a corrective environment choice.
    """
    constraints: list[Constraint] = []
    if commands.status != "planned":
        return PlanResult("not_evaluated", commands.reason_code, None, ())
    try:
        if any(not _relative(p) for p in contents):
            raise ValueError("snapshot paths must be canonical repository-relative paths")
        if commands != plan_commands(contents):
            raise ValueError("T06 command plan does not match snapshot")
        if execution.platform != catalog.platform:
            raise ValueError("execution platform differs from frozen catalog")
        managers = _declarations(contents, commands, constraints)
        variant, manager_defaulted, locks = _select_manager(
            contents, catalog, managers, constraints
        )
        node_constraints = [c.expression for c in constraints if c.tool == "node"]
        node = (
            choose_node(catalog.node_versions, node_constraints)
            if node_constraints
            else catalog.default_node
        )
        runtime = next(
            r for r in catalog.runtimes if r.node_version == node and r.manager_variant == variant
        )
        versions = dict(runtime.versions)
        for c in constraints:
            if c.tool != "node" and (
                c.tool not in versions or not npm_satisfies(versions[c.tool], c.expression)
            ):
                raise ValueError(
                    f"{c.source}: {c.tool} constraint incompatible with selected runtime"
                )
        install, evidence = _install_command(contents, runtime, locks)
        suite = _suite(commands, runtime.manager_name)
        # T06 currently leaves implicit lifecycle build to the original script.
        # An explicit future build argv must use the same selected manager.
        build = None
        if commands.build_argv:
            raise ValueError("explicit build command adapter is not supported by T06")
        environment = {"TZ": "UTC", "LC_ALL": "C.UTF-8", **commands.environment, "CI": "true"}
        resolved = EnvironmentPlan(
            platform=catalog.platform,
            node_version=node,
            manager_name=runtime.manager_name,
            manager_variant=variant,
            manager_version=runtime.manager_version,
            node_defaulted=not node_constraints,
            manager_defaulted=manager_defaulted,
            base_ref=runtime.base_ref,
            base_image_id=runtime.base_image_id,
            derived_image_id=runtime.image_id,
            versions=runtime.versions,
            constraints=tuple(constraints),
            install=Command(install, commands.cwd, execution.install_timeout_seconds),
            build=build,
            suite=Command(suite, commands.cwd, execution.suite_timeout_seconds),
            limits=Limits(execution.cpus, execution.memory_mb, execution.workspace_limit_mb),
            environment=tuple(sorted(environment.items())),
            packages=tuple(
                PackageMetadata(
                    p.package_path,
                    tuple(sorted(p.scripts.items())),
                    tuple(sorted(p.dependencies.items())),
                )
                for p in commands.packages
            ),
            install_evidence=evidence,
            later_metadata=("derived_lockfile", "resolved_dependency_tree"),
            catalog_hash=catalog.digest,
            catalog_source_hash=catalog.source_hash,
            recipe_hash=runtime.recipe_hash,
            snapshot_hash=stable_digest(
                {p: hashlib.sha256(b).hexdigest() for p, b in contents.items()}
            ),
            command_plan_hash=stable_digest(asdict(commands)),
        )
        return PlanResult("planned", "environment_planned", resolved, tuple(constraints))
    except (ValueError, TypeError, KeyError, UnicodeError, yaml.YAMLError) as error:
        return PlanResult(
            "not_evaluated", "environment_unresolved", None, tuple(constraints), (str(error),)
        )
