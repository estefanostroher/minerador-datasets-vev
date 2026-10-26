"""P2 capability evidence against the real Linux daemon, four exact patches."""

import json
from dataclasses import asdict, replace
from pathlib import Path

import docker
import pytest
import test_direct_calls as direct

from robo_minerador.config import ExecutionConfig
from robo_minerador.environments.catalog import load_catalog
from robo_minerador.environments.plan import Command, plan_environment
from robo_minerador.execution.docker import DockerExecutor
from robo_minerador.execution.results import load_report
from robo_minerador.screening.commands import plan_commands
from robo_minerador.state.artifacts import ArtifactStore
from robo_minerador.state.store import StateStore
from robo_minerador.validation.cases import validate_cases

ROOT = Path(__file__).resolve().parents[2]
REPORTERS = ROOT / "tools/ts-analyzer/src/frameworks/reporters"


@pytest.fixture(scope="module")
def analyzer():
    yield from direct.analyzer.__wrapped__()


def fixture_files(framework, version):
    base = direct.fixture_files(framework, version)
    files = {
        k: v
        for k, v in base.items()
        if k
        in {
            "package.json",
            "package-lock.json",
            "tsconfig.json",
            "jest.config.cjs",
            "vitest.config.ts",
        }
    }
    if framework == "vitest":
        directory = ROOT / "tests/fixtures/projects/coverage-vitest"
        if version == "4.0.0":
            directory = directory / "versions/4.0.0"
        for name in ("package.json", "package-lock.json"):
            files[name] = (directory / name).read_bytes()
    files["value.ts"] = (
        "// original 😀 UTF16\r\n"
        "/** Return a magnitude, retaining an intentionally nested branch. */\r\n"
        "export function magnitude(value: number): number {\r\n"
        "  const nested = (flag: boolean) => { if (flag) return 100; return -100; };\r\n"
        "  if (value < 0) {\r\n"
        "    return -value;\r\n"
        "  } else {\r\n"
        "    nested(true);\r\n"
        "    return value;\r\n"
        "  }\r\n"
        "}\r\n"
        "/** A second target with homonymous direct/indirect test names. */\r\n"
        "export function twin(value: number): number {\r\n"
        "  return value < 0 ? -value : value;\r\n"
        "}\r\n"
    ).encode()
    imports = "import {test,expect,beforeEach,vi} from 'vitest';\n" if framework == "vitest" else ""
    mock = "vi" if framework == "vitest" else "jest"
    files["value.test.ts"] = (
        imports + "import {magnitude,twin} from './value';\n"
        "import * as values from './value';\n"
        "import {appendFileSync} from 'node:fs';\n"
        "beforeEach(()=>appendFileSync('/workspace/hooks.log','hook\\n'));\n"
        "test('direct 😀',()=>{expect(magnitude(1)).toBe(1)});\n"
        "const indirect=(n:number)=>magnitude(n);\n"
        "test('indirect',()=>{expect(indirect(-1)).toBe(1)});\n"
        f"test('mocked',()=>{{const spy={mock}.spyOn(values,'magnitude').mockReturnValue(9);"
        "expect(values.magnitude(-5)).toBe(9);spy.mockRestore()});\n"
        "test.skip('skipped',()=>{magnitude(-4)});\n"
        "test('same',()=>{expect(twin(1)).toBe(1)});\n"
        "const indirectTwin=(n:number)=>twin(n);\n"
        "test('same',()=>{expect(indirectTwin(-1)).toBe(1)});\n"
    ).encode()
    return files


@pytest.mark.docker
@pytest.mark.parametrize("framework,version", direct.VERSIONS)
def test_associated_coverage_excludes_indirect_nested_mock_and_skipped(
    analyzer,
    tmp_path,
    framework,
    version,
):
    from robo_minerador.validation.coverage import CoverageService, summarize_istanbul

    files = fixture_files(framework, version)
    instrumented = direct.instrument(analyzer, files, framework, version)
    manifest = instrumented.manifest
    assert manifest.status == "ready", manifest.limitations
    target = next(t for t in manifest.targets if t.qualified_name == "magnitude")
    twin = next(t for t in manifest.targets if t.qualified_name == "twin")
    planned = plan_environment(files, plan_commands(files), load_catalog(ROOT), ExecutionConfig())
    assert planned.plan is not None
    plan = replace(
        planned.plan,
        build=None,
        install=Command(("npm", "ci", "--no-audit", "--no-fund"), ".", 180),
    )
    artifacts = ArtifactStore(tmp_path / "cas")
    state = StateStore(tmp_path / "state.sqlite", artifacts=artifacts)
    run = state.create_run("T15")
    task = state.start_task(run, target.id, "coverage")
    client = docker.from_env()
    runner = DockerExecutor(client, artifacts, install_network="bridge")
    extension = "cjs" if framework == "jest" else "mjs"
    reporter = framework + "." + extension
    supplements = {reporter: (REPORTERS / reporter).read_bytes()}
    argv = (
        ("node", "node_modules/jest/bin/jest.js", "--runInBand", "--testLocationInResults")
        if framework == "jest"
        else ("node", "node_modules/vitest/vitest.mjs", "run")
    )
    config_path = "jest.config.cjs" if framework == "jest" else "vitest.config.ts"
    try:
        prepared = runner.prepare(plan, direct.archive(files))
        baseline_config = direct.configuration(framework, False)
        traced_config = direct.configuration(framework, True)
        config = "config." + extension
        # Reuse the T11 serial-mode supplement; the production coverage wrapper
        # later loads the ORIGINAL config, preserving its transforms and hooks.
        launch = (
            b"require('fs').copyFileSync('/opt/robo-reporters/config.mjs','/workspace/.pair.mjs');"
            b"const r=require('child_process').spawnSync(process.execPath,"
            b"process.argv.slice(2),{stdio:'inherit'});process.exit(r.status??1);"
        )
        pair_argv = argv + (
            "--config",
            "/opt/robo-reporters/" + config,
            "--reporters" if framework == "jest" else "--reporter",
            "/opt/robo-reporters/" + reporter,
        )
        if framework == "vitest":
            supplements["launch.cjs"] = launch
            pair_argv = ("node", "/opt/robo-reporters/launch.cjs", *pair_argv[1:])
            pair_argv = tuple(
                "/workspace/.pair.mjs" if s == "/opt/robo-reporters/config.mjs" else s
                for s in pair_argv
            )
        baseline = runner.run_fresh(
            prepared.prepared_id,
            pair_argv,
            plan.limits,
            supplements={**supplements, config: baseline_config},
        )
        raw = direct.workspace_file(artifacts, baseline, ".robo-reports/raw.json")
        original = load_report(direct.normalize(analyzer, raw, files), raw, artifacts)
        overlay = direct.overlay_archive(
            runner.prepared_archive(prepared.prepared_id),
            {f.path: f.source.encode() for f in manifest.files},
        )
        traced_prepared = runner.prepare(
            replace(plan, install=Command(("node", "--version"), ".", 30)), overlay
        )
        traced = runner.run_fresh(
            traced_prepared.prepared_id,
            pair_argv,
            plan.limits,
            supplements={
                **supplements,
                config: traced_config,
                **{k: v.encode() for k, v in instrumented.supplements.items()},
            },
        )
        traced_raw = direct.workspace_file(artifacts, traced, ".robo-reports/raw.json")
        traced_report = load_report(
            direct.normalize(analyzer, traced_raw, files, manifest), traced_raw, artifacts
        )
        trace = direct.workspace_file(artifacts, traced, ".robo-reports/trace.jsonl")

        def associate(t):
            return validate_cases(
                original, traced_report, manifest, t.id, baseline, traced, trace, artifacts
            )

        associated = associate(target)
        assert associated.status == "evaluated"
        assert [c.title_path[-1] for c in original.cases if c.identity in associated.case_ids] == [
            "direct 😀"
        ]

        def read_report(execution):
            data = direct.workspace_file(artifacts, execution, ".robo-reports/raw.json")
            return load_report(direct.normalize(analyzer, data, files), data, artifacts)

        service = CoverageService(runner, state, artifacts, read_report=read_report)
        result = service.measure(
            task,
            associated,
            target,
            prepared.prepared_id,
            argv,
            plan.limits,
            config_path=config_path,
            source=files["value.ts"],
        )
        if result.availability != "available":
            print(
                json.dumps(
                    json.loads(artifacts.get(result.evidence_id)), indent=2, ensure_ascii=True
                )
            )
            print(
                direct.workspace_file(
                    artifacts, runner.results[-1], ".robo-reports/coverage-envelope.json"
                ).decode()
            )
        assert result.availability == "available", (
            result.reason,
            json.loads(
                direct.workspace_file(
                    artifacts, runner.results[-1], ".robo-reports/coverage-envelope.json"
                )
            )["files"],
        )
        assert len(result.selected_ids) == 1
        assert result.lines.total > result.lines.covered > 0
        assert (result.branches.covered, result.branches.total) == (1, 2)
        evidence = json.loads(artifacts.get(result.evidence_id))
        measured_execution = runner.results[-1]
        assert direct.workspace_file(artifacts, measured_execution, "hooks.log") == b"hook\n"
        assert evidence["executed_ids"] == list(result.selected_ids)
        assert direct.workspace_file(artifacts, measured_execution, "value.ts") == files["value.ts"]
        assert (
            direct.workspace_file(artifacts, measured_execution, config_path) == files[config_path]
        )
        assert evidence["configuration"] and evidence["supplements"] and evidence["coverage"]
        request_source = artifacts.get(
            dict(measured_execution.supplements)["/opt/robo-reporters/coverage-request.cjs"]
        ).decode()
        request = json.loads(request_source.removeprefix("module.exports = ").removesuffix(";\n"))
        envelope = json.loads(artifacts.get(evidence["coverage"]))
        assert request["instrumentation"]["tool"] == envelope["tool"]
        # Same provider/config, deliberately unfiltered suite: indirect case adds
        # one original branch arm. This global metric must never enter result.
        global_execution = service.run_observation(
            prepared.prepared_id,
            argv,
            plan.limits,
            config_path=config_path,
            framework=framework,
            version=version,
            pattern=".*",
        )
        global_data = json.loads(
            direct.workspace_file(
                artifacts, global_execution, ".robo-reports/coverage-envelope.json"
            )
        )
        global_lines, global_branches = summarize_istanbul(
            files["value.ts"], target, global_data["files"]["value.ts"]
        )
        assert (global_branches.covered, global_branches.total) == (2, 2)
        assert global_lines.covered > result.lines.covered
        before = len(runner.results)
        denied = service.measure(
            task,
            associate(twin),
            twin,
            prepared.prepared_id,
            argv,
            plan.limits,
            config_path=config_path,
            source=files["value.ts"],
        )
        assert denied.availability == "unavailable"
        assert denied.reason == "coverage_identity_isolation_unavailable"
        assert denied.lines is None and len(runner.results) == before
        assert not state.assessments(task)
        threshold_argv = argv + (
            ("--coverageThreshold", '{"global":{"lines":100}}')
            if framework == "jest"
            else ("--coverage.thresholds.lines=100",)
        )
        failed = service.measure(
            task,
            associated,
            target,
            prepared.prepared_id,
            threshold_argv,
            plan.limits,
            config_path=config_path,
            source=files["value.ts"],
        )
        assert failed.availability == "unavailable" and failed.reason == "coverage_execution_failed"
        failure_evidence = json.loads(artifacts.get(failed.evidence_id))
        assert failure_evidence["stdout"] and failure_evidence["stderr"]
        assert failure_evidence["configuration"] and failure_evidence["executed_ids"] == list(
            result.selected_ids
        )
        assert failed.lines == result.lines and failed.branches == result.branches
        expanded = service.measure(
            task,
            associated,
            target,
            prepared.prepared_id,
            argv + ("-t", ".*"),
            plan.limits,
            config_path=config_path,
            source=files["value.ts"],
        )
        assert expanded.availability == "unavailable"
        assert expanded.reason == "coverage_identity_isolation_unavailable"
        expanded_evidence = json.loads(artifacts.get(expanded.evidence_id))
        assert len(expanded_evidence["executed_ids"]) == 5
        assert expanded_evidence["configuration"]
        assert expanded.lines is None and expanded.branches is None
        package = "@vitest/coverage-istanbul" if framework == "vitest" else "babel-plugin-istanbul"
        missing = runner.prepare(
            replace(
                plan,
                install=Command(
                    (
                        "node",
                        "-e",
                        f"require('fs').rmSync('node_modules/{package}',{{recursive:true,force:true}})",
                    ),
                    ".",
                    30,
                ),
            ),
            direct.overlay_archive(runner.prepared_archive(prepared.prepared_id), {}),
        )
        absent = service.measure(
            task,
            associated,
            target,
            missing.prepared_id,
            argv,
            plan.limits,
            config_path=config_path,
            source=files["value.ts"],
        )
        assert absent.availability == "unavailable" and absent.reason == "coverage_tool_unavailable"
        assert absent.lines is None and absent.branches is None
        assert not state.assessments(task)
        print(
            json.dumps(
                {
                    "framework": framework,
                    "patch": version,
                    "associated_lines": asdict(result.lines),
                    "global_lines": asdict(global_lines),
                    "associated_branches": asdict(result.branches),
                    "global_branches": asdict(global_branches),
                    "selected": len(result.selected_ids),
                    "method": result.method,
                    "nested": "excluded",
                    "homonyms": denied.reason,
                }
            )
        )
    finally:
        runner.close()
        state.close()
        client.close()
