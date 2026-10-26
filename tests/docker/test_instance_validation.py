"""P3: controlled engineering instances, real screening/extraction and Linux suites."""

import base64
import importlib
import json
from dataclasses import replace
from pathlib import Path

import docker
import httpx
import pytest
import test_direct_calls as direct

from robo_minerador.config import DiscoveryConfig, ExecutionConfig
from robo_minerador.discovery.github import GitHubClient
from robo_minerador.environments.catalog import load_catalog
from robo_minerador.environments.plan import Command, persist_plan, plan_environment
from robo_minerador.execution.docker import DockerExecutor, PreparedEnvironment
from robo_minerador.execution.repetitions import RepositoryValidation
from robo_minerador.execution.results import load_report
from robo_minerador.extraction.client import AnalysisClient
from robo_minerador.extraction.context import load_context, restore_context
from robo_minerador.extraction.instrumentation import InstrumentationClient, InstrumentationResponse
from robo_minerador.extraction.service import ExtractionService, load_extraction
from robo_minerador.screening.commands import plan_commands
from robo_minerador.screening.service import ScreeningService
from robo_minerador.screening.snapshots import Repository, load_snapshot
from robo_minerador.state.artifacts import ArtifactStore
from robo_minerador.state.store import StateStore
from robo_minerador.validation.cases import validate_cases
from robo_minerador.validation.coverage import CoverageService

ROOT = Path(__file__).resolve().parents[2]
LICENSE = b"""MIT License

Copyright (c) 2026 Fixture Authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


@pytest.fixture(scope="module")
def analyzer():
    yield from direct.analyzer.__wrapped__()


def fixture_files(framework, version, variable_direct=False):
    files = direct.fixture_files(framework, version)
    files = {p: b for p, b in files.items() if not p.endswith(".ts") or p == "vitest.config.ts"}
    files["value.ts"] = (
        "// original 😀 UTF16\r\n"
        "import { zero } from './local';\r\n"
        "/** Return the absolute magnitude of the input number.\r\n"
        " * @param value signed input\r\n * @returns nonnegative magnitude\r\n */\r\n"
        "export function magnitude(value: number): number {\r\n"
        "  if (value < zero) { return -value; }\r\n  else { return value; }\r\n}\r\n"
        "export function undocumented(value: number): number {\r\n"
        "  if(value < zero) { return -value; } else { return value; }\r\n}\r\n"
    ).encode()
    files["local.ts"] = b"export const zero = 0;\r\n"
    imports = "import {test,expect} from 'vitest';\n" if framework == "vitest" else ""
    files["value.test.ts"] = (
        imports
        + "import {magnitude} from './value';\n"
        + "\n".join(
            f"test('direct {i}',()=>{{expect(magnitude({i})).toBe({abs(i)})}});"
            for i in (-2, -1, 0, 1)
        )
        + "\ntest.skip('excluded skip',()=>{magnitude(99)});\n"
        + "test.todo('excluded todo');\n"
        + ("test.failing" if framework == "jest" else "test.fails")
        + "('excluded expected failure',()=>{expect(magnitude(-1)).toBe(999)});\n"
    ).encode()
    if variable_direct:
        files["value.test.ts"] = (
            b"import {existsSync} from 'node:fs';\n" + files["value.test.ts"]
        ).replace(
            b"expect(magnitude(1)).toBe(1)",
            b"if(existsSync('/workspace/no-direct')){expect(1).toBe(1)}"
            b"else{expect(magnitude(1)).toBe(1)}",
        )
    files["fixtures/data.bin"] = b"\x00\xff\x80\r\n"
    files["LICENSE"] = LICENSE
    reporter = framework + (".cjs" if framework == "jest" else ".mjs")
    files["reporter" + Path(reporter).suffix] = (
        ROOT / "tools/ts-analyzer/src/frameworks/reporters" / reporter
    ).read_bytes()
    if framework == "jest":
        files["jest.config.cjs"] = files["jest.config.cjs"].replace(
            b"testEnvironment:",
            b"reporters: ['./reporter.cjs'], testLocationInResults: true, testEnvironment:",
        )
    else:
        files["vitest.config.ts"] = files["vitest.config.ts"].replace(
            b"includeTaskLocation: true",
            b"includeTaskLocation: true, pool:'forks', maxWorkers:1, fileParallelism:false, "
            b"reporters:['./reporter.mjs']",
        )
    return files


def screen(files, artifacts, state, run):
    sha = "a" * 40
    repo = Repository(
        repo_id=14,
        state_id=state.upsert_repository(run, 14, "controlled/eligibility", sha),
        full_name="controlled/eligibility",
        url="https://example.test/controlled/eligibility",
        commit_sha=sha,
        default_branch="main",
    )

    def metadata(request):
        if "/tarball/" in request.url.path:
            return httpx.Response(
                200,
                content=direct.archive(
                    {"controlled-" + sha + "/" + p: b for p, b in files.items()}
                ),
            )
        assert request.url.path.endswith("/license") and request.url.params["ref"] == sha
        return httpx.Response(
            200,
            json={
                "type": "file",
                "path": "LICENSE",
                "encoding": "base64",
                "content": base64.b64encode(LICENSE).decode(),
                "license": {"spdx_id": "MIT"},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(metadata)) as http:
        github = GitHubClient(
            DiscoveryConfig(created_from="2020-01-01", created_until="2020-01-02"),
            http=http,
            token=None,
        )
        report = ScreeningService(github, state, artifacts).screen(run, repo, ExecutionConfig())
    model = json.loads(artifacts.get(report.digest))
    statuses = {a["criterion_id"]: a["status"] for a in model["assessments"]}
    assert statuses == {
        "H-R01": "approved",
        "H-R02": "approved",
        "H-R03": "not_evaluated",
        "H-R04": "not_evaluated",
        "H-R05": "not_evaluated",
    }
    return load_snapshot(artifacts, model["snapshot_manifest"]), report


@pytest.mark.docker
@pytest.mark.parametrize(
    "framework,version,missing_ordinal",
    [(*version, None) for version in direct.VERSIONS]
    + [("jest", "29.7.0", 1), ("jest", "29.7.0", 2)],
)
def test_p3_real_originals_three_paired_traces_and_eligibility(
    analyzer, tmp_path, framework, version, missing_ordinal, monkeypatch
):
    module = importlib.import_module("robo_minerador.validation.service")
    assert hasattr(module, "EligibilityService"), "T14 evidence-derived eligibility API is missing"
    from pydantic import TypeAdapter

    files = fixture_files(framework, version, missing_ordinal is not None)
    artifacts = ArtifactStore(tmp_path / "cas")
    state = StateStore(tmp_path / "state.sqlite", artifacts=artifacts)
    run = state.create_run("T14-P3-controlled-engineering-fixture")
    snapshot, screening = screen(files, artifacts, state, run)
    commands = plan_commands(files)
    planned = plan_environment(files, commands, load_catalog(ROOT), ExecutionConfig())
    assert planned.plan is not None
    plan = planned.plan
    client = docker.from_env()
    runner = DockerExecutor(client, artifacts, install_network="bridge")
    reports = []
    if missing_ordinal is not None:
        from docker.models.containers import Container

        exec_run = Container.exec_run
        suite_number = 0

        def select_direct_occurrence(container, cmd, *args, **kwargs):
            nonlocal suite_number
            if isinstance(cmd, list) and len(cmd) == 3 and "/run/robo-executor/ready" in cmd[2]:
                env = dict(value.split("=", 1) for value in container.attrs["Config"]["Env"])
                argv = json.loads(env["EXECUTOR_ARGV"])
                if argv[:2] == ["npm", "test"] or "node_modules/jest/bin/jest.js" in argv:
                    suite_number += 1
                    if (suite_number - 1) % 3 + 1 == missing_ordinal:
                        # Runtime fixture input only; all original source/config
                        # bytes remain untouched in every captured workspace.
                        injected = exec_run(
                            container,
                            [
                                "node",
                                "-e",
                                "require('fs').writeFileSync('/workspace/no-direct',"
                                "'controlled fixture input')",
                            ],
                            user="1000:1000",
                        )
                        assert injected.exit_code == 0
            return exec_run(container, cmd, *args, **kwargs)

        monkeypatch.setattr(Container, "exec_run", select_direct_occurrence)

    def read_report(execution):
        raw = direct.workspace_file(artifacts, execution, ".robo-reports/raw.json")
        report = load_report(direct.normalize(analyzer, raw, files), raw, artifacts)
        reports.append(report)
        return report

    try:
        repository = RepositoryValidation(
            runner, state, artifacts, read_report=read_report
        ).validate(
            run,
            snapshot.repository.state_id,
            plan,
            commands,
            artifacts.get(snapshot.archive_sha256),
        )
        assert repository.outcome == "completed"
        originals = [execution for execution in runner.results if execution.phase == "run"]
        assert len(originals) == 3 and len(reports) == 3
        terminal = json.loads(artifacts.get(repository.evidence_id))
        prepared = TypeAdapter(PreparedEnvironment).validate_json(json.dumps(terminal["prepared"]))
        root = "/workspace/snapshots/p3-" + framework + version
        assert analyzer.put_archive(
            "/", direct.archive({root.lstrip("/") + "/" + p: b for p, b in files.items()})
        )

        def forward(request):
            # Transfer only: the actual HTTP analyzer runs inside the Linux container.
            script = (
                "fetch('http://127.0.0.1:3000'+process.argv[1],"
                "{method:'POST',body:process.argv[2]}).then(async r=>"
                "console.log(JSON.stringify({status:r.status,body:await r.json()})))"
            )
            result = analyzer.exec_run(
                ["node", "-e", script, request.url.path, request.content.decode()]
            )
            assert result.exit_code == 0, result.output.decode()
            response = json.loads(result.output)
            return httpx.Response(response["status"], json=response["body"])

        transport = httpx.MockTransport(forward)
        with AnalysisClient("http://analyzer", transport=transport) as analysis:
            with InstrumentationClient("http://analyzer", transport=transport) as instrument:
                extraction = ExtractionService(
                    analysis, state, artifacts, instrumentation=instrument
                ).extract(
                    run_id=run,
                    snapshot=snapshot,
                    screening_digest=screening.digest,
                    plan=plan,
                    plan_artifact=persist_plan(plan, artifacts),
                    analyzer_root=root,
                    framework=framework,
                    tsconfig_paths=("tsconfig.json",),
                    framework_version=version,
                    prepared=prepared,
                )
        extracted = load_extraction(artifacts, extraction.digest)
        candidate = next(c for c in extracted.candidates if c.target.qualified_name == "magnitude")
        instrumented = InstrumentationResponse.model_validate_json(
            artifacts.get(extracted.instrumentation_artifact)
        )
        assert instrumented.manifest.status == "ready"
        overlay = direct.overlay_archive(
            artifacts.get(prepared.workspace_artifact),
            {f.path: f.source.encode() for f in instrumented.manifest.files},
        )
        traced_prepared = runner.prepare(
            replace(plan, install=Command(("node", "--version"), ".", 30)), overlay
        )
        extension = "cjs" if framework == "jest" else "mjs"
        config_name = "config." + extension
        supplements = {
            **{k: v.encode() for k, v in instrumented.supplements.items()},
            config_name: direct.configuration(framework, True),
            framework + "." + extension: files["reporter." + extension],
        }
        argv = (
            ("node", "node_modules/jest/bin/jest.js", "--runInBand", "--testLocationInResults")
            if framework == "jest"
            else ("node", "node_modules/vitest/vitest.mjs", "run")
        )
        argv += (
            "--config",
            "/opt/robo-reporters/" + config_name,
            "--reporters" if framework == "jest" else "--reporter",
            "/opt/robo-reporters/" + framework + "." + extension,
        )
        if framework == "vitest":
            supplements["launch.cjs"] = (
                b"require('fs').copyFileSync('/opt/robo-reporters/config.mjs','/workspace/.pair.mjs');"
                b"const r=require('child_process').spawnSync(process.execPath,"
                b"process.argv.slice(2),"
                b"{stdio:'inherit'});process.exit(r.status??1);"
            )
            argv = ("node", "/opt/robo-reporters/launch.cjs", *argv[1:])
            argv = tuple(
                "/workspace/.pair.mjs" if a == "/opt/robo-reporters/config.mjs" else a for a in argv
            )
        pairs = []
        for ordinal, (original, report) in enumerate(zip(originals, reports, strict=True), 1):
            traced = runner.run_fresh(
                traced_prepared.prepared_id, argv, plan.limits, supplements=supplements
            )
            raw = direct.workspace_file(artifacts, traced, ".robo-reports/raw.json")
            traced_report = load_report(
                direct.normalize(analyzer, raw, files, instrumented.manifest), raw, artifacts
            )
            pairs.append(
                validate_cases(
                    report,
                    traced_report,
                    instrumented.manifest,
                    candidate.target.id,
                    original,
                    traced,
                    direct.workspace_file(artifacts, traced, ".robo-reports/trace.jsonl"),
                    artifacts,
                    pair_ordinal=ordinal,
                )
            )
        expected_counts = [4, 4, 4]
        if missing_ordinal:
            expected_counts[missing_ordinal - 1] = 3
        assert [len(pair.case_ids) for pair in pairs] == expected_counts
        coverage = None
        if missing_ordinal is None:
            original_prepared = runner.prepare(
                replace(plan, install=Command(("node", "--version"), ".", 30)),
                direct.overlay_archive(artifacts.get(prepared.workspace_artifact), {}),
            )
            task = state.start_task(run, candidate.id, "coverage")
            base_argv = (
                ("node", "node_modules/jest/bin/jest.js", "--runInBand", "--testLocationInResults")
                if framework == "jest"
                else ("node", "node_modules/vitest/vitest.mjs", "run")
            )
            coverage = CoverageService(runner, state, artifacts, read_report=read_report).measure(
                task,
                pairs[0],
                next(t for t in instrumented.manifest.targets if t.id == candidate.target.id),
                original_prepared.prepared_id,
                base_argv,
                plan.limits,
                config_path="jest.config.cjs" if framework == "jest" else "vitest.config.ts",
                source=files["value.ts"],
            )
            if coverage.availability == "unavailable":
                evidence = json.loads(artifacts.get(coverage.evidence_id))
                print({"coverage_reason": coverage.reason, "detail": evidence.get("detail")})
            assert coverage.availability == (
                "available" if framework == "jest" else "unavailable"
            ), coverage
            if framework == "vitest":
                assert coverage.reason == "coverage_tool_unavailable"
        api = module.EligibilityService(state, artifacts)
        arguments = dict(
            run_id=run,
            extraction_digest=extraction.digest,
            candidate_id=candidate.id,
            repository=repository,
            associations=tuple(pairs),
            coverage=coverage,
        )
        if (framework, version, missing_ordinal) == ("jest", "29.7.0", None):
            undocumented = next(
                c for c in extracted.candidates if c.target.qualified_name == "undocumented"
            )
            no_documentation = api.evaluate(
                **{
                    **arguments,
                    "candidate_id": undocumented.id,
                    "associations": (),
                    "coverage": None,
                }
            )
            assert not no_documentation.accepted
            assert (
                next(a for a in no_documentation.assessments if a.criterion_id == "H-F04").status
                == "rejected"
            )
        if (framework, version, missing_ordinal) == ("jest", "29.7.0", None):

            class CoordinatorCrash(BaseException):
                pass

            with monkeypatch.context() as crash:
                finish = state.finish_task

                def interrupt_publish(task_id, *args, **kwargs):
                    if state.get_task(task_id).stage == module.INSTANCE_STAGE:
                        raise CoordinatorCrash()
                    return finish(task_id, *args, **kwargs)

                crash.setattr(state, "finish_task", interrupt_publish)
                with pytest.raises(CoordinatorCrash):
                    api.validate(**arguments)
            state.close()
            state = StateStore(tmp_path / "state.sqlite", artifacts=artifacts)
            api = module.EligibilityService(state, artifacts)
            with pytest.raises(ValueError, match="immutable"):
                api.validate(**{**arguments, "associations": ()})
        result = api.validate(**arguments)
        assert result.occurrence.accepted is (missing_ordinal is None), (
            result.occurrence.model_dump()
        )
        if missing_ordinal is None:
            assert all(a.status == "approved" for a in result.occurrence.assessments)
        else:
            assert (
                next(a for a in result.occurrence.assessments if a.criterion_id == "H-F03").status
                == "rejected"
            )
        assert result.occurrence.case_ids_per_run == tuple(p.case_ids for p in pairs)
        before = len(runner.results)
        state.close()
        state = StateStore(tmp_path / "state.sqlite", artifacts=artifacts)
        api = module.EligibilityService(state, artifacts)
        assert api.validate(**arguments) == result
        assert api.load(run, result.digest) == result
        unowned = artifacts.put(
            result.occurrence.model_copy(update={"accepted": not result.occurrence.accepted})
            .model_dump_json()
            .encode()
        )
        with pytest.raises(ValueError, match="durable request"):
            api.load(run, unowned)
        assert len(state.assessments(result.task_id)) == 12
        assert len(runner.results) == before
        restored = tmp_path / "restored"
        restore_context(load_context(artifacts, extracted.context_reference), artifacts, restored)
        assert {p: (restored / p).read_bytes() for p in files} == files
        missing = api.evaluate(**{**arguments, "associations": ()})
        assert not missing.accepted
        assert (
            next(a for a in missing.assessments if a.criterion_id == "H-F03").status
            == "not_evaluated"
        )
        if (framework, version, missing_ordinal) == ("jest", "29.7.0", None):
            with pytest.raises(ValueError, match="association provenance"):
                api.evaluate(
                    **{
                        **arguments,
                        "associations": (replace(pairs[0], case_ids=("forged",)), *pairs[1:]),
                    }
                )
            with pytest.raises(ValueError, match="ordinal/target/context"):
                api.evaluate(**{**arguments, "associations": (pairs[0], pairs[0], pairs[2])})
            with pytest.raises(ValueError, match="immutable"):
                api.validate(**{**arguments, "associations": ()})
            altered = runner.prepare(
                replace(plan, install=Command(("node", "--version"), ".", 30)),
                direct.overlay_archive(
                    artifacts.get(traced_prepared.workspace_artifact),
                    {"local.ts": files["local.ts"] + b"// changed context\n"},
                ),
            )
            observation = runner.run_fresh(
                altered.prepared_id, argv, plan.limits, supplements=supplements
            )
            observed_raw = direct.workspace_file(artifacts, observation, ".robo-reports/raw.json")
            observed_report = load_report(
                direct.normalize(analyzer, observed_raw, files, instrumented.manifest),
                observed_raw,
                artifacts,
            )
            unrelated_context = validate_cases(
                reports[0],
                observed_report,
                instrumented.manifest,
                candidate.target.id,
                originals[0],
                observation,
                direct.workspace_file(artifacts, observation, ".robo-reports/trace.jsonl"),
                artifacts,
            )
            assert unrelated_context.status == "evaluated"
            with pytest.raises(ValueError, match="trace.*context"):
                api.evaluate(**{**arguments, "associations": (unrelated_context, *pairs[1:])})
        if missing_ordinal is None:
            without_coverage = api.evaluate(**{**arguments, "coverage": None})
            assert without_coverage.accepted
            assert without_coverage.assessments == result.occurrence.assessments
        summary = {
            "rule_version": result.occurrence.rule_version,
            "unresolved_case_ids_per_run": result.occurrence.unresolved_case_ids_per_run,
            "framework": framework,
            "patch": version,
            "missing_direct_ordinal": missing_ordinal,
            "original_status": [r.reason for r in originals],
            "case_ids_per_run": result.occurrence.case_ids_per_run,
            "assessments": [a.model_dump() for a in result.occurrence.assessments],
            "accepted": result.occurrence.accepted,
            "context": "byte-exact",
            "evidence": result.digest,
            "coverage": {
                "availability": coverage.availability,
                "reason": coverage.reason,
                "evidence": coverage.evidence_id,
            }
            if coverage
            else None,
        }
        (tmp_path / "p3-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary))
    finally:
        runner.close()
        state.close()
        client.close()
