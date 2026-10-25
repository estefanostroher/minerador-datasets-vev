"""Static inputs for T07's EnvironmentPlan. Never run scripts or resolve versions.

Only direct Jest/Vitest invocations and explicit npm all-workspace aggregation
are understood. Shell expressions, dynamic JS configs and custom orchestrators
are retained as evidence with unresolved scope, rather than partially executed.
"""

import fnmatch
import json
import re
import shlex
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

import yaml

SUPPORTED = {"jest", "vitest"}
OTHER_RUNNERS = {
    "mocha",
    "ava",
    "tap",
    "tape",
    "uvu",
    "jasmine",
    "karma",
    "playwright",
    "cypress",
    "cucumber-js",
}


@dataclass(frozen=True)
class PackagePlan:
    cwd: str
    package_path: str
    manager_hint: str | None
    scripts: dict[str, str]
    dependencies: dict[str, str]
    config_evidence: tuple[str, ...]
    frameworks: tuple[str, ...]
    name: str | None = None


@dataclass(frozen=True)
class CommandPlan:
    status: str
    reason_code: str
    packages: tuple[PackagePlan, ...]
    workspaces: tuple[str, ...]
    frameworks: tuple[str, ...]
    cwd: str = "."
    suite_argv: tuple[str, ...] = ()
    build_argv: tuple[str, ...] = ()
    environment: dict[str, str] = field(default_factory=lambda: {"CI": "true"})
    evidence: tuple[str, ...] = ()
    workspace_patterns: tuple[str, ...] = ()


def _object(body: bytes) -> dict[str, Any]:
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


def _scripts(model: dict[str, Any]) -> dict[str, str]:
    scripts = model.get("scripts", {})
    if not isinstance(scripts, dict) or any(not isinstance(v, str) for v in scripts.values()):
        raise ValueError("invalid scripts")
    return scripts


def _tokens(script: str) -> list[str]:
    try:
        return shlex.split(script, posix=True)
    except ValueError:
        return []


def _executables(script: str) -> set[str]:
    """Recognize command positions only, never words mentioned as arguments."""
    commands = set()
    command_position = True
    for token in _tokens(script):
        if token in {"&&", "||", ";", "|"}:
            command_position = True
        elif command_position:
            commands.add(token)
            command_position = False
    return commands


def _package(
    path: str, model: dict[str, Any], contents: dict[str, bytes], hint: str | None
) -> PackagePlan:
    cwd = str(PurePosixPath(path).parent)
    prefix = "" if cwd == "." else cwd + "/"
    deps: dict[str, str] = {}
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        values = model.get(key, {})
        if not isinstance(values, dict) or any(not isinstance(v, str) for v in values.values()):
            raise ValueError("invalid dependencies")
        deps.update(values)
    configs = tuple(
        sorted(
            p
            for p in contents
            if str(PurePosixPath(p).parent) == cwd
            and (
                PurePosixPath(p).name.startswith(
                    ("jest.config.", "vitest.config.", "vitest.workspace.")
                )
                or PurePosixPath(p).name
                in {"pnpm-workspace.yaml", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"}
            )
        )
    )
    frameworks = set()
    scripts = _scripts(model)
    commands = {command for s in scripts.values() for command in _executables(s)}
    for framework in SUPPORTED | OTHER_RUNNERS:
        if (
            framework in commands
            or framework in deps
            or (framework == "jest" and ("@jest/core" in deps or "jest" in model))
            or any(PurePosixPath(p).name.startswith(framework + ".config.") for p in configs)
        ):
            frameworks.add(framework)
    if any(_tokens(s)[:2] == ["node", "--test"] for s in scripts.values()):
        frameworks.add("node:test")
    if "jest" in model:
        configs += (prefix + "package.json#jest",)
    manager = model.get("packageManager", hint)
    if manager is not None and not isinstance(manager, str):
        raise ValueError("invalid package manager")
    return PackagePlan(
        cwd, path, manager, scripts, deps, configs, tuple(sorted(frameworks)), model.get("name")
    )


def _workspace_patterns(
    root: dict[str, Any], contents: dict[str, bytes]
) -> tuple[list[str], list[str]]:
    patterns = root.get("workspaces", [])
    if isinstance(patterns, dict):
        patterns = patterns.get("packages", [])
    evidence = ["package.json"]
    if "pnpm-workspace.yaml" in contents:
        value = yaml.safe_load(contents["pnpm-workspace.yaml"])
        if not isinstance(value, dict):
            raise ValueError("invalid workspace YAML")
        yaml_patterns = value.get("packages", [])
        if patterns and patterns != yaml_patterns:
            raise ValueError("workspace declarations differ")
        patterns = yaml_patterns
        evidence.append("pnpm-workspace.yaml")
    if not isinstance(patterns, list) or any(not isinstance(p, str) for p in patterns):
        raise ValueError("invalid workspace patterns")
    for pattern in patterns:
        if pattern.lstrip("!").startswith("/") or ".." in pattern.split("/") or "\\" in pattern:
            raise ValueError("unsafe workspace pattern")
    return patterns, evidence


def _match(path: str, pattern: str) -> bool:
    """Segment globs; '**' includes zero levels, '*' never crosses a slash."""
    parts = path.split("/")
    matched = {0}
    for segment in pattern.split("/"):
        if segment == "**":
            matched = set(range(min(matched), len(parts) + 1)) if matched else set()
        else:
            matched = {
                index + 1
                for index in matched
                if index < len(parts) and fnmatch.fnmatchcase(parts[index], segment)
            }
    return len(parts) in matched


def _lifecycle_understood(package: PackagePlan) -> bool:
    for lifecycle in ("pretest", "posttest"):
        script = package.scripts.get(lifecycle)
        if script is not None and (
            _tokens(script) != ["npm", "run", "build"] or "build" not in package.scripts
        ):
            return False
    return True


@dataclass(frozen=True)
class _TestCommand:
    tokens: tuple[str, ...]
    config_path: str | None
    run_mode: bool


def _runner_flags(runner: str) -> tuple[set[str], set[str]]:
    """Small supported CLI subsets; unknown/version-specific flags stay unresolved."""
    value_flags = {"--config", "-c"}
    boolean_flags = {"--coverage", "--silent", "--watch=false"}
    if runner == "jest":
        value_flags |= {"--maxWorkers", "-w", "--reporters", "--coverageDirectory"}
        boolean_flags |= {
            "--runInBand",
            "--ci",
            "--verbose",
            "--no-cache",
            "--detectOpenHandles",
            "--forceExit",
            "--watchAll=false",
        }
    else:
        boolean_flags.add("--run")
    return value_flags, boolean_flags


def _parse_command(tokens: list[str]) -> _TestCommand | None:
    # Positional filters or project selection cannot prove an encompassing suite.
    runner = tokens[0]
    value_flags, boolean_flags = _runner_flags(runner)
    config_path = None
    # A bare 'run' is a Vitest subcommand only immediately after the executable.
    # Any further positional token (including another 'run') is a path filter.
    run_mode = runner == "vitest" and tokens[1:2] == ["run"]
    arguments = iter(tokens[2:] if run_mode else tokens[1:])
    for token in arguments:
        if runner == "jest" and re.fullmatch(r"-w[1-9][0-9]*%?", token):
            continue
        option, separator, value = token.partition("=")
        if option in value_flags:
            if not separator:
                value = next(arguments, "")
            if not value or value.startswith("-"):
                return None
            if runner == "jest" and option in {"-w", "--maxWorkers"}:
                if re.fullmatch(r"[1-9][0-9]*%?", value) is None:
                    return None
            if option in {"--config", "-c"}:
                if config_path is not None:
                    return None  # Even identical repeated config flags are ambiguous here.
                config_path = value
        elif token in boolean_flags:
            if runner == "vitest" and token == "--run":
                run_mode = True
        else:
            return None
    return _TestCommand(tuple(tokens), config_path, run_mode)


def _direct(package: PackagePlan) -> tuple[str, str | None, _TestCommand | None]:
    script = package.scripts.get("test", "")
    tokens = _tokens(script)
    if not tokens:
        status = (
            "suite_scope_unresolved" if package.frameworks or script else "no_test_configuration"
        )
        return status, None, None
    if set(package.frameworks) - SUPPORTED:
        return "framework_unsupported", None, None
    runner = tokens[0]
    if runner not in SUPPORTED:
        return "suite_scope_unresolved", None, None
    # Do not interpret shell, env assignments, aliases, custom wrappers or paths.
    if any(any(c in token for c in ";|&`$<>\n") for token in tokens):
        return "suite_scope_unresolved", None, None
    if any(
        token in {"watch", "dev", "--watch", "--watchAll", "--watch=true", "--watchAll=true"}
        for token in tokens[1:]
    ):
        return "suite_scope_unresolved", None, None
    corroborated = (
        runner in package.dependencies
        or (runner == "jest" and "@jest/core" in package.dependencies)
        or any(
            runner + ".config." in p or p.endswith("#" + runner) for p in package.config_evidence
        )
    )
    if not corroborated or (set(package.frameworks) & SUPPORTED) - {runner}:
        return "framework_ambiguous", None, None
    command = _parse_command(tokens)
    if command is None or not _lifecycle_understood(package):
        return "suite_scope_unresolved", None, None
    return "planned", runner, command


def _root_projects_cover(
    root: dict[str, Any],
    package: PackagePlan,
    workspaces: list[str],
    contents: dict[str, bytes],
    command: _TestCommand,
) -> bool:
    """Only static JSON configs rooted at the repository establish whole-suite scope.

    Non-root config directories/rootDir overrides need Jest's path resolution;
    this static subset leaves them unresolved instead of rebasing their globs.
    """
    if set(package.frameworks) != {"jest"}:
        return False
    config = root.get("jest")
    config_path = command.config_path
    if config_path is None and "jest.config.json" in contents:
        config_path = "jest.config.json"
    if config_path:
        if str(PurePosixPath(config_path).parent) != ".":
            return False
        try:
            config = _object(contents[config_path])
        except (KeyError, ValueError):
            return False
    if not isinstance(config, dict) or not isinstance(config.get("projects"), list):
        return False
    if config.get("rootDir", ".") not in (".", "./"):
        return False
    projects = config["projects"]
    if not projects or any(not isinstance(p, str) for p in projects):
        return False
    patterns = [p.removeprefix("<rootDir>/").removeprefix("./") for p in projects]
    return all(any(_match(workspace, p) for p in patterns) for workspace in workspaces)


def _suite_argv(package: PackagePlan, runner: str) -> tuple[str, ...]:
    if package.manager_hint and not package.manager_hint.startswith("npm@"):
        return ()  # T07 must select the manager and its exact version first.
    flags = (
        ("--ci", "--watch=false", "--watchAll=false")
        if runner == "jest"
        else ("--run", "--watch=false")
    )
    return ("npm", "test", "--", *flags)


def plan_commands(contents: dict[str, bytes]) -> CommandPlan:
    """Create a static plan for all declared workspaces, without choosing a target package."""
    packages: list[PackagePlan] = []
    workspaces: list[str] = []
    evidence: list[str] = []
    try:
        if "package.json" not in contents:
            return CommandPlan("no_test_configuration", "no_test_configuration", (), (), ())
        root = _object(contents["package.json"])
        patterns, evidence = _workspace_patterns(root, contents)
        packages.append(_package("package.json", root, contents, None))
        paths = sorted(p for p in contents if p.endswith("/package.json"))
        selected = []
        for path in paths:
            directory = str(PurePosixPath(path).parent)
            if any(_match(directory, p) for p in patterns if not p.startswith("!")) and not any(
                _match(directory, p[1:]) for p in patterns if p.startswith("!")
            ):
                workspaces.append(directory)
                selected.append(path)
        unreadable = False
        for path in selected:
            try:
                packages.append(
                    _package(path, _object(contents[path]), contents, packages[0].manager_hint)
                )
            except (ValueError, TypeError, AttributeError):
                unreadable = True
                packages.append(
                    PackagePlan(
                        str(PurePosixPath(path).parent),
                        path,
                        packages[0].manager_hint,
                        {},
                        {},
                        (path,),
                        (),
                    )
                )
        unresolved_patterns = any(
            not any(_match(w, p) for w in workspaces) for p in patterns if not p.startswith("!")
        )
    except (ValueError, TypeError, AttributeError, yaml.YAMLError):
        return CommandPlan(
            "suite_scope_unresolved",
            "configuration_unreadable",
            tuple(packages),
            tuple(workspaces),
            (),
        )
    frameworks = tuple(sorted({f for p in packages for f in p.frameworks}))

    def result(status: str, argv: tuple[str, ...] = ()) -> CommandPlan:
        reason = status
        if status == "planned":
            reason = "static_suite_plan" if argv else "manager_resolution_pending"
        return CommandPlan(
            status,
            reason,
            tuple(packages),
            tuple(workspaces),
            frameworks,
            suite_argv=argv,
            evidence=tuple(
                evidence
                + [p.package_path for p in packages]
                + [c for p in packages for c in p.config_evidence]
            ),
            workspace_patterns=tuple(patterns),
        )

    if set(frameworks) - SUPPORTED:
        return result("framework_unsupported")
    if unresolved_patterns or unreadable:
        return result("suite_scope_unresolved")
    root_package = packages[0]
    tokens = _tokens(root_package.scripts.get("test", ""))
    if workspaces:
        status, runner, command = _direct(root_package)
        if (
            status == "planned"
            and runner
            and command is not None
            and _root_projects_cover(root, root_package, workspaces, contents, command)
        ):
            if any(set(p.frameworks) - {runner} for p in packages[1:]):
                return result("framework_ambiguous")
            return result("planned", _suite_argv(root_package, runner))
        if tokens not in (["npm", "test", "--workspaces"], ["npm", "run", "test", "--workspaces"]):
            return result("suite_scope_unresolved")
        if not _lifecycle_understood(root_package):
            return result("suite_scope_unresolved")
        for package in packages[1:]:
            status, runner, child_command = _direct(package)
            if status != "planned":
                return result(
                    status if status != "no_test_configuration" else "suite_scope_unresolved"
                )
            if runner == "vitest" and (child_command is None or not child_command.run_mode):
                return result("suite_scope_unresolved")
        if root_package.manager_hint and not root_package.manager_hint.startswith("npm@"):
            return result("suite_scope_unresolved")
        return result("planned", ("npm", "test"))
    status, runner, _ = _direct(root_package)
    if status != "planned":
        return result(status)
    # T07 chooses the actual manager/version using these retained hints/locks.
    return result("planned", _suite_argv(root_package, runner or ""))
