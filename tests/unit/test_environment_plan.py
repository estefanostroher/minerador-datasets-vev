"""T07 resolves static snapshot evidence, never executes the project."""

import dataclasses
import inspect
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from robo_minerador.config import ExecutionConfig
from robo_minerador.environments import catalog, plan
from robo_minerador.screening.commands import plan_commands
from robo_minerador.state.artifacts import ArtifactStore

ROOT = Path(__file__).resolve().parents[2]


def snapshot(**package: object) -> dict[str, bytes]:
    return {
        "package.json": json.dumps(
            {
                "scripts": {"test": "vitest --coverage"},
                "devDependencies": {"vitest": "^3"},
                **package,
            }
        ).encode(),
        "package-lock.json": b'{"lockfileVersion": 3}',
    }


def resolve(contents: dict[str, bytes]) -> plan.PlanResult:
    return plan.plan_environment(
        contents, plan_commands(contents), catalog.load_catalog(ROOT), ExecutionConfig()
    )


def test_catalog_is_validated_and_default_plan_is_frozen() -> None:
    assert hasattr(catalog, "load_catalog"), "T07 catalog loader is missing"
    result = resolve(snapshot())
    assert result.status == "planned"
    env = result.plan
    assert env is not None
    assert (env.node_version, env.manager_name, env.manager_version) == ("22.22.3", "npm", "10.8.2")
    assert env.node_defaulted and not env.manager_defaulted  # npm is inferred from its lock.
    assert env.install.argv == ("npm", "ci")
    assert env.suite.argv == ("npm", "test", "--", "--run", "--watch=false")
    assert env.build is None
    assert env.environment == (("CI", "true"), ("LC_ALL", "C.UTF-8"), ("TZ", "UTC"))
    assert env.install.timeout_seconds == 1200
    assert env.limits.memory_mb == 4096
    assert env.derived_image_id.startswith("sha256:")
    assert env.base_ref.startswith("node@sha256:")
    assert env.platform == "linux/amd64"
    assert env.digest == resolve(snapshot()).plan.digest
    with pytest.raises(dataclasses.FrozenInstanceError):
        env.node_version = "24.0.0"


def test_choose_node_uses_numeric_order_and_intersection() -> None:
    assert catalog.choose_node(["9.0.0", "22.22.3", "20.19.0"], [">=20", "<23"]) == "22.22.3"


def test_invalid_or_branch_cannot_hide_behind_matching_branch() -> None:
    assert resolve(snapshot(engines={"node": ">=20 || ${DYNAMIC}"})).plan is None


def test_prerelease_only_interval_cannot_select_a_stable_catalog_runtime() -> None:
    expression = ">=22.22.3-rc.1 <22.22.3"
    result = resolve(snapshot(engines={"node": expression}))
    assert (result.status, result.reason_code, result.plan) == (
        "not_evaluated",
        "environment_unresolved",
        None,
    )
    assert any(c.expression == expression for c in result.constraints)


@pytest.mark.parametrize(
    "declaration,expected",
    [
        ("20.19.0", "20.19.0"),
        ("^20.0.0 || ^22.0.0", "22.22.3"),
        (">=20", "24.0.0"),
        ("20 - 22", "22.22.3"),
        ("~22.22", "22.22.3"),
        ("22.x", "22.22.3"),
    ],
)
def test_node_engines_ranges(declaration: str, expected: str) -> None:
    result = resolve(snapshot(engines={"node": declaration}))
    assert result.status == "planned"
    assert result.plan.node_version == expected
    assert not result.plan.node_defaulted
    assert any(c.source == "package.json#engines.node" for c in result.constraints)


@pytest.mark.parametrize("declaration", ["22.22.2", "25", "${NODE_VERSION}", "lts/*"])
def test_unknown_or_dynamic_node_is_unresolved(declaration: str) -> None:
    contents = snapshot()
    contents[".nvmrc"] = declaration.encode()
    result = resolve(contents)
    assert (result.status, result.reason_code, result.plan) == (
        "not_evaluated",
        "environment_unresolved",
        None,
    )
    assert result.details


def test_preferred_node_must_obey_lower_priority_constraints() -> None:
    contents = snapshot(volta={"node": "22.22.3"}, engines={"node": ">=20 <24"})
    contents[".nvmrc"] = b"20.19.0\n"
    assert resolve(contents).plan is None
    contents[".nvmrc"] = b"22\n"
    contents[".node-version"] = b"22.22.3\n"
    assert resolve(contents).plan.node_version == "22.22.3"


def ci(value: str, matrix: str = "") -> bytes:
    return (
        "jobs:\n  test:\n" + matrix + "    steps:\n      - uses: actions/setup-node@v4\n"
        "        with:\n          " + value + "\n"
    ).encode()


def test_ci_matrix_is_alternatives_and_selects_highest_compatible() -> None:
    contents = snapshot(engines={"node": "<24"})
    contents[".github/workflows/test.yml"] = ci(
        "node-version: ${{ matrix.node }}",
        "    strategy:\n      matrix:\n        node: [20, 22, 24]\n",
    )
    result = resolve(contents)
    assert result.plan.node_version == "22.22.3"
    assert any(c.alternatives == ("20", "22", "24") for c in result.constraints)


def test_single_ci_and_static_toml_node() -> None:
    contents = snapshot()
    contents[".github/workflows/test.yaml"] = ci("node-version: '20'")
    assert resolve(contents).plan.node_version == "20.19.0"
    del contents[".github/workflows/test.yaml"]
    contents["mise.toml"] = b'[tools]\nnode = "20.19.0"\n'
    assert resolve(contents).plan.node_version == "20.19.0"


@pytest.mark.parametrize("value", ["node-version: ${{ inputs.node }}", "node-version-file: .nvmrc"])
def test_dynamic_or_file_referenced_ci_is_unresolved(value: str) -> None:
    contents = snapshot()
    contents[".nvmrc"] = b"22"
    contents[".github/workflows/test.yml"] = ci(value)
    assert resolve(contents).reason_code == "environment_unresolved"


def test_workspace_engines_are_never_partially_ignored() -> None:
    contents = snapshot(
        workspaces=["packages/*"],
        jest={"projects": ["packages/*"]},
        scripts={"test": "jest"},
        devDependencies={"jest": "^29"},
        engines={"node": ">=20"},
    )
    contents["packages/a/package.json"] = b'{"engines":{"node":"<22"}}'
    result = resolve(contents)
    assert result.plan.node_version == "20.19.0"
    assert any(c.source == "packages/a/package.json#engines.node" for c in result.constraints)
    contents["packages/b/package.json"] = b'{"engines":{"node":">=22"}}'
    assert resolve(contents).plan is None


@pytest.mark.parametrize(
    "manager,lock,body,install",
    [
        ("npm@10.8.2", "package-lock.json", b"{}", ("npm", "ci")),
        (
            "pnpm@9.15.5",
            "pnpm-lock.yaml",
            b"lockfileVersion: '9.0'",
            ("pnpm", "install", "--frozen-lockfile"),
        ),
        (
            "yarn@1.22.22",
            "yarn.lock",
            b"# yarn lockfile v1\n",
            ("yarn", "install", "--frozen-lockfile"),
        ),
        (
            "yarn@4.6.0",
            "yarn.lock",
            b"__metadata:\n  version: 8\n",
            ("yarn", "install", "--immutable"),
        ),
    ],
)
def test_exact_manager_and_appropriate_argv(manager, lock, body, install) -> None:
    contents = snapshot(packageManager=manager)
    del contents["package-lock.json"]
    contents[lock] = body
    result = resolve(contents)
    assert result.status == "planned", result.details
    assert result.plan.install.argv == install
    assert result.plan.suite.argv[0] == manager.split("@")[0]
    assert result.plan.suite.argv[-2:] == ("--run", "--watch=false")
    if manager.startswith("yarn"):
        assert "--" not in result.plan.suite.argv


@pytest.mark.parametrize("manager", ["pnpm@9.15.4", "npm@^10", "yarn@3.8.7", "bun@1.0.0"])
def test_declared_manager_outside_catalog_is_not_substituted(manager: str) -> None:
    assert resolve(snapshot(packageManager=manager)).plan is None


def test_manager_engines_and_volta_must_agree() -> None:
    assert resolve(snapshot(engines={"npm": "<10"})).plan is None
    assert resolve(snapshot(packageManager="npm@10.8.2", volta={"npm": "9.0.0"})).plan is None
    assert resolve(snapshot(engines={"npm": "^10.0.0"})).plan is not None


def test_multiple_and_nested_locks_have_no_guessed_scope() -> None:
    contents = snapshot()
    contents["pnpm-lock.yaml"] = b"lockfileVersion: 9"
    assert resolve(contents).plan is None
    del contents["pnpm-lock.yaml"]
    contents["packages/a/yarn.lock"] = b"# yarn lockfile v1"
    assert resolve(contents).plan is None


def test_yarn_path_is_relative_present_and_metadata_only() -> None:
    contents = snapshot(packageManager="yarn@4.6.0")
    del contents["package-lock.json"]
    contents["yarn.lock"] = b"__metadata:\n  version: 8\n"
    contents[".yarnrc.yml"] = b"yarnPath: .yarn/releases/yarn-4.6.0.cjs\n"
    contents[".yarn/releases/yarn-4.6.0.cjs"] = b"throw new Error('NEVER EXECUTE')"
    assert resolve(contents).plan.manager_version == "4.6.0"
    del contents[".yarn/releases/yarn-4.6.0.cjs"]
    assert resolve(contents).plan is None


def test_volta_manager_and_yarn_path_can_resolve_without_package_manager_field() -> None:
    contents = snapshot(volta={"pnpm": "9.15.5"})
    del contents["package-lock.json"]
    contents["pnpm-lock.yaml"] = b"lockfileVersion: '9.0'"
    assert resolve(contents).plan.manager_variant == "pnpm"
    contents = snapshot()
    del contents["package-lock.json"]
    contents["yarn.lock"] = b"__metadata:\n  version: 8\n"
    contents[".yarnrc.yml"] = b"yarnPath: .yarn/releases/yarn-4.6.0.cjs\n"
    contents[".yarn/releases/yarn-4.6.0.cjs"] = b"throw Error('never execute')"
    assert resolve(contents).plan.manager_variant == "yarn4"


def test_yarn_path_unknown_metadata_and_conflicting_lock_are_unresolved() -> None:
    contents = snapshot(packageManager="yarn@4.6.0")
    del contents["package-lock.json"]
    contents["yarn.lock"] = b"# yarn lockfile v1\n"
    assert resolve(contents).plan is None
    contents["yarn.lock"] = b"__metadata:\n  version: 8\n"
    contents[".yarnrc.yml"] = b"yarnPath: .yarn/release.cjs\n"
    contents[".yarn/release.cjs"] = b"console.log('never execute')"
    assert resolve(contents).plan is None
    contents[".yarnrc.yml"] = b"yarnPath: https://example.org/yarn.cjs"
    assert resolve(contents).plan is None


def test_no_lock_requires_explicit_procedure_evidence() -> None:
    contents = snapshot()
    del contents["package-lock.json"]
    assert resolve(contents).plan is None
    contents["README.md"] = b"Install dependencies with `npm install`.\n"
    result = resolve(contents)
    assert result.plan.install.argv == ("npm", "install")
    assert result.plan.install_evidence == ("README.md",)
    assert result.plan.later_metadata == ("derived_lockfile", "resolved_dependency_tree")
    assert result.plan.packages[0].dependencies == (("vitest", "^3"),)


@pytest.mark.parametrize("ci_value", [None, "true"])
def test_explicit_pnpm_no_lock_procedure_disables_frozen_lockfile(
    monkeypatch: pytest.MonkeyPatch, ci_value: str | None
) -> None:
    if ci_value is None:
        monkeypatch.delenv("CI", raising=False)
    else:
        monkeypatch.setenv("CI", ci_value)
    contents = snapshot(packageManager="pnpm@9.15.5")
    del contents["package-lock.json"]
    contents["README.md"] = b"Install dependencies with `pnpm install`.\n"
    result = resolve(contents)
    assert result.status == "planned", result.details
    assert result.plan.install.argv == ("pnpm", "install", "--no-frozen-lockfile")
    assert ("CI", "true") in result.plan.environment
    assert result.plan.install_evidence == ("README.md",)


def test_manager_default_and_explicit_config_policy() -> None:
    contents = snapshot(config={"robo_minerador": {"allow_lockfile_generation": True}})
    del contents["package-lock.json"]
    env = resolve(contents).plan
    assert env.manager_defaulted
    assert env.node_version == "22.22.3"
    assert env.install_evidence == ("package.json#config.robo_minerador.allow_lockfile_generation",)


def test_negative_readme_or_mentioned_install_is_not_permission() -> None:
    contents = snapshot()
    del contents["package-lock.json"]
    contents["README.md"] = b"Do not run `npm install`.\n"
    assert resolve(contents).plan is None
    contents["README.md"] = b"The error message mentioned `npm install`.\n"
    assert resolve(contents).plan is None


def test_workspace_manager_engine_conflict_has_source_evidence() -> None:
    contents = snapshot(
        workspaces=["packages/*"],
        jest={"projects": ["packages/*"]},
        scripts={"test": "jest"},
        devDependencies={"jest": "^29"},
    )
    contents["packages/a/package.json"] = b'{"engines":{"npm":"<10"}}'
    result = resolve(contents)
    assert result.plan is None
    assert "packages/a/package.json#engines.npm" in result.details[0]


def test_yarn_does_not_silently_execute_npm_lifecycle() -> None:
    contents = snapshot(
        packageManager="yarn@4.6.0",
        scripts={"test": "vitest run", "pretest": "npm run build", "build": "tsc"},
    )
    del contents["package-lock.json"]
    contents["yarn.lock"] = b"__metadata:\n  version: 8\n"
    assert resolve(contents).plan is None


def test_ci_matrix_does_not_override_exact_preference() -> None:
    contents = snapshot(volta={"node": "20.19.0"})
    contents[".github/workflows/test.yml"] = ci(
        "node-version: ${{ matrix.node }}",
        "    strategy:\n      matrix:\n        node: [20, 22, 24]\n",
    )
    assert resolve(contents).plan.node_version == "20.19.0"


def test_malformed_yaml_is_not_executed_or_ignored() -> None:
    contents = snapshot()
    contents[".yarnrc.yml"] = b"!!python/object/apply:os.system ['echo forbidden']"
    assert resolve(contents).plan is None


@pytest.mark.parametrize(
    "config,field,expression",
    [
        (b"nodeLinker: ${LINKER}\n", "nodeLinker", "${LINKER}"),
        (
            b"npmScopes:\n  private:\n    npmAuthToken: ${TOKEN:-fallback}\n",
            "npmScopes.private.npmAuthToken",
            "${TOKEN:-fallback}",
        ),
        (
            b"plugins:\n  - path: ${PLUGIN}\n",
            "plugins[0].path",
            "${PLUGIN}",
        ),
        (
            b"supportedArchitectures:\n  cpu: [x64, '${ARCH}']\n",
            "supportedArchitectures.cpu[1]",
            "${ARCH}",
        ),
    ],
)
def test_yarn_interpolation_is_unresolved_with_exact_field_evidence(
    monkeypatch: pytest.MonkeyPatch, config: bytes, field: str, expression: str
) -> None:
    # Even a populated host environment cannot resolve snapshot configuration.
    for name in ("LINKER", "TOKEN", "PLUGIN", "ARCH"):
        monkeypatch.setenv(name, "host-value-must-not-be-used")
    contents = snapshot(packageManager="yarn@4.6.0")
    del contents["package-lock.json"]
    contents["yarn.lock"] = b"__metadata:\n  version: 8\n"
    contents[".yarnrc.yml"] = config
    result = resolve(contents)
    assert (result.status, result.reason_code, result.plan) == (
        "not_evaluated",
        "environment_unresolved",
        None,
    )
    source = ".yarnrc.yml#" + field
    assert source in result.details[0]
    assert expression in result.details[0]
    assert any(c.source == source and c.expression == expression for c in result.constraints)


def test_static_nested_yarn_configuration_remains_planned() -> None:
    contents = snapshot(packageManager="yarn@4.6.0")
    del contents["package-lock.json"]
    contents["yarn.lock"] = b"__metadata:\n  version: 8\n"
    contents[".yarnrc.yml"] = (
        b"nodeLinker: node-modules\nnpmScopes:\n  private:\n    npmRegistryServer: https://example.org\n"
        b"supportedArchitectures:\n  cpu: [x64, arm64]\n"
    )
    assert resolve(contents).status == "planned"


def test_digest_covers_limits_commands_scripts_and_snapshot() -> None:
    contents = snapshot()
    commands = plan_commands(contents)
    frozen = catalog.load_catalog(ROOT)
    original = plan.plan_environment(contents, commands, frozen, ExecutionConfig()).plan
    limited = plan.plan_environment(
        contents, commands, frozen, ExecutionConfig(memory_mb=1024)
    ).plan
    assert limited.digest != original.digest
    changed = snapshot(scripts={"test": "vitest run --coverage"})
    assert resolve(changed).plan.digest != original.digest
    changed = {**contents, "src/a.ts": b"export const a = 1"}
    assert resolve(changed).plan.digest != original.digest
    assert original.digest == plan.stable_digest(dataclasses.asdict(original))


def test_modified_t06_input_and_wrong_platform_are_unresolved() -> None:
    contents = snapshot()
    commands = dataclasses.replace(plan_commands(contents), suite_argv=("npx", "vitest"))
    result = plan.plan_environment(
        contents, commands, catalog.load_catalog(ROOT), ExecutionConfig()
    )
    assert result.plan is None
    result = plan.plan_environment(
        contents,
        plan_commands(contents),
        catalog.load_catalog(ROOT),
        ExecutionConfig(platform="linux/arm64"),
    )
    assert result.plan is None


def test_plan_is_persisted_as_content_addressed_versioned_evidence(tmp_path: Path) -> None:
    env = resolve(snapshot()).plan
    artifacts = ArtifactStore(tmp_path / "artifacts")
    digest = plan.persist_plan(env, artifacts)
    assert digest == env.digest
    model = json.loads(artifacts.get(digest))
    assert model["schema_version"] == "1.0"
    assert model["policy_version"] == "T07-v1"
    assert model["install"]["argv"] == ["npm", "ci"]
    assert plan.persist_plan(env, artifacts) == digest


def test_unresolved_t06_scope_does_not_become_executable() -> None:
    result = resolve(snapshot(scripts={"test": "vitest && echo done"}))
    assert result.plan is None
    assert result.reason_code == "suite_scope_unresolved"


def test_selection_has_no_test_result_input_or_execution_effects() -> None:
    assert "test_result" not in inspect.signature(plan.plan_environment).parameters
    with (
        patch("subprocess.Popen", side_effect=AssertionError("host execution")),
        patch("docker.from_env", side_effect=AssertionError("docker execution")),
    ):
        assert resolve(snapshot()).status == "planned"


def test_catalog_rejects_changed_source_and_recipe(tmp_path: Path) -> None:
    import shutil

    shutil.copytree(ROOT / "config", tmp_path / "config")
    shutil.copytree(ROOT / "docker", tmp_path / "docker")
    recipe = tmp_path / "docker/runner.Dockerfile"
    recipe.write_bytes(recipe.read_bytes() + b"\n# changed\n")
    with pytest.raises(ValueError, match="recipe"):
        catalog.load_catalog(tmp_path)
    shutil.copyfile(ROOT / "docker/runner.Dockerfile", recipe)
    source = tmp_path / "config/environments.json"
    model = json.loads(source.read_bytes())
    model["default_node"] = "24.0.0"
    source.write_text(json.dumps(model))
    with pytest.raises(ValueError, match="source hash"):
        catalog.load_catalog(tmp_path)


def test_semver_parity_with_installed_node_reference() -> None:
    cases = [
        ("^20 || ^22", "22.22.3"),
        ("20 - 22", "22.22.3"),
        ("~22.22", "22.22.3"),
        ("22.x", "22.22.3"),
        (">=20 <24", "24.0.0"),
        ("*", "22.0.0-rc.1"),
        (">=22.0.0-rc.1 <23", "22.0.0-rc.2"),
        (">=22.0.0-rc.1 <23", "22.1.0-rc.1"),
        ("^0.2.3", "0.3.0"),
        ("1.2.3 - 2.3", "2.4.0"),
        ("", "22.22.3"),
        ("<=22", "22.22.3"),
        (">=22.0.0", "22.0.0-rc.1"),
        ("22.0.0-rc.1", "22.0.0-rc.1"),
        ("^1.2.3-beta.1", "1.2.3-beta.2"),
        ("^1.2.3-beta.1", "1.3.0-beta.1"),
        ("^1.2.3-beta.1", "1.3.0"),
        ("~1.2.3-beta.1", "1.2.3-beta.2"),
        ("^0.0.3", "0.0.4"),
        (">1.2", "1.2.9"),
        ("1.2.*", "1.3.0"),
        ("^10.0.0 || ^12.0.0", "10.8.2"),
        ("9.15 - 9.16", "9.15.5"),
        ("1.22.x", "1.22.22"),
        ("~4.6", "4.6.0"),
        ("=22.22.3", "22.22.3"),
        ("v22.22.3", "22.22.3"),
        ("1.2.3+build.1", "1.2.3+build.2"),
        # Mixed prerelease/stable bounds must remain in the same comparator set.
        (">=22.22.3-rc.1 <22.22.3", "22.22.3"),
        (">=22.22.3-rc.1 <22.22.3", "22.22.3-rc.2"),
        (">=22.22.3-rc.1 <22.22.3", "22.22.3-rc.0"),
        (">22.22.3-rc.1 <=22.22.3", "22.22.3-rc.1"),
        (">22.22.3-rc.1 <=22.22.3", "22.22.3"),
        (">=22.22.3 <22.22.4-rc.2", "22.22.4-rc.1"),
        (">=22.22.3 <22.22.4-rc.2", "22.22.4-rc.2"),
        (">=22.22.3 <22.22.4-rc.2", "22.22.4"),
        (">=22.22.3 <22.22.4-rc.2", "22.22.3"),
        (">=22.22.3-rc.1 <=22.22.3-rc.2", "22.22.3-rc.2"),
        (">=22.22.3-rc.1 <=22.22.3-rc.2", "22.22.3"),
        ("22.22.3-rc.1", "22.22.3"),
        (">=22.22.3-rc.1 <22.22.3 || 24.x", "22.22.3"),
        (">=22.22.3-rc.1 <22.22.3 || 24.x", "24.0.0"),
        ("22.x || >=24.0.0-rc.1 <24.0.0", "22.22.3-rc.2"),
        ("22.x || >=24.0.0-rc.1 <24.0.0", "24.0.0-rc.2"),
        (">=22.22.3-rc.1 <22.22.3 >=22.22.3-rc.2", "22.22.3-rc.1"),
        (">=22.22.3-rc.1 <22.22.3 >=22.22.3-rc.2", "22.22.3-rc.3"),
        (">=22.22.3-rc.1 <22.22.3 >=22.22.3-rc.2", "22.22.3"),
        # Implicit upper bounds in partial/caret/tilde ranges exclude '-0' too.
        (">=22.0.0-rc.1 <22", "22.0.0-rc.2"),
        (">=22.22.0-rc.1 <22.22", "22.22.0-rc.2"),
        (">=23.0.0-rc.1 ^22", "23.0.0-rc.2"),
        (">=22.23.0-rc.1 ~22.22", "22.23.0-rc.2"),
        (">=22.23.0-rc.1 <=22.22", "22.23.0-rc.2"),
        ("22.22.3-rc.1 - 22.22.3-rc.3", "22.22.3"),
        ("22.22.3-rc.1 - 22.22.3-rc.3", "22.22.3-rc.2"),
        ("22.22.3-rc.1 - 22.22", "22.22.3"),
        ("^22.22.3-rc.1", "22.22.3"),
        ("~22.22.3-rc.1", "22.22.3"),
        ("~24.0.0-rc.1", "24.0.0"),
        ("~24.0.0-rc.1", "24.0.0-rc.2"),
        ("~24.0.0-rc.1", "24.1.0"),
        ("^0.0.0-rc.1", "0.0.0"),
        (">=23.0.0-rc.1 22.x.3", "23.0.0-rc.2"),
        (">=23.0.0-rc.1 ~22.x", "23.0.0-rc.2"),
        (">=22.23.0-rc.1 22.22.x", "22.23.0-rc.2"),
    ]
    # Executes only our fixed helper against the already installed reference library.
    reference = subprocess.run(
        [
            "node",
            "-e",
            "const s=require('./tools/ts-analyzer/node_modules/semver');"
            "let b='';process.stdin.on('data',d=>b+=d);process.stdin.on('end',()=>"
            "console.log(JSON.stringify(JSON.parse(b).map(([r,v])=>s.satisfies(v,r)))));",
        ],
        input=json.dumps(cases),
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    for (spec, version), expected in zip(cases, json.loads(reference.stdout), strict=True):
        assert catalog.npm_satisfies(version, spec) == expected, (spec, version, expected)
