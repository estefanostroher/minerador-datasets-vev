"""Real-daemon T13 validation with original locked framework fixtures."""

import io
import json
import tarfile
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import docker
import pytest
import test_direct_calls as direct_calls

from robo_minerador.config import ExecutionConfig
from robo_minerador.environments.catalog import load_catalog
from robo_minerador.environments.plan import Command, plan_environment
from robo_minerador.execution.docker import DockerExecutor
from robo_minerador.screening.commands import plan_commands
from robo_minerador.state.artifacts import ArtifactStore
from robo_minerador.state.store import StateStore

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def analyzer():
    yield from direct_calls.analyzer.__wrapped__()


def archive(files):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as tar:
        for name, body in files.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(body)
            tar.addfile(entry, io.BytesIO(body))
    return stream.getvalue()


@pytest.fixture
def session(tmp_path):
    from robo_minerador.execution.repetitions import RepositoryValidation

    client = docker.from_env()
    assert client.ping()
    artifacts = ArtifactStore(tmp_path / "cas")
    state = StateStore(tmp_path / "state.sqlite", artifacts=artifacts)
    runner = DockerExecutor(client, artifacts)
    service = RepositoryValidation(runner, state, artifacts)
    run = state.create_run("T13")
    files = {
        "package.json": b'{"scripts":{"test":"jest"},"devDependencies":{"jest":"29.7.0"}}',
        "package-lock.json": b'{"lockfileVersion":3}',
        "original.txt": b"clean",
    }
    commands = plan_commands(files)
    resolved = plan_environment(files, commands, load_catalog(ROOT), ExecutionConfig())
    assert resolved.plan is not None
    # Controlled commands exercise the actual executor; never claim framework scope.
    plan = replace(
        resolved.plan,
        install=Command(("node", "-e", "require('fs').writeFileSync('deps','installed')"), ".", 20),
        suite=Command(("node", "suite.js"), ".", 20),
    )
    yield service, runner, state, artifacts, run, files, commands, plan
    runner.close()
    state.close()
    client.close()


def validate(session, *, script=b"console.log('pass')", install=None):
    service, _, _, _, run, files, commands, plan = session
    files = {**files, "suite.js": script}
    if install:
        plan = replace(plan, install=Command(("node", "-e", install), ".", 20))
    return service.validate(run, "repository", plan, commands, archive(files))


@contextmanager
def reopen_session(session, *, offline):
    """Close the original connection; reconstruct state/CAS and the service."""
    from robo_minerador.execution.repetitions import RepositoryValidation

    _, old_runner, old_state, old_artifacts, *inputs = session
    old_state.close()
    artifacts = ArtifactStore(old_artifacts.root)
    state = StateStore(old_artifacts.root.parent / "state.sqlite", artifacts=artifacts)
    runner = None if offline else DockerExecutor(old_runner.client, artifacts)
    try:
        yield (RepositoryValidation(runner, state, artifacts), runner, state, artifacts, *inputs)
    finally:
        if runner is not None:
            runner.close()
        state.close()


@pytest.mark.docker
def test_install_failure_excludes_entire_repository_before_any_suite(session):
    result = validate(session, install="console.error('install failed');process.exit(8)")
    _, runner, state, artifacts, _, _, _, _ = session
    assert result.outcome == "rejected"
    assert [(r.phase, r.exit_code) for r in runner.results] == [("install", 8)]
    assert not runner.prepared
    assert {
        a.assessment.criterion_id: a.assessment.status for a in state.assessments(result.task_id)
    } == {"H-R05": "rejected", "H-R06": "approved"}
    assert b"install failed" in artifacts.get(runner.results[0].stderr_artifact)
    before = len(runner.results)
    assert validate(session, install="console.error('install failed');process.exit(8)") == result
    assert len(runner.results) == before


@pytest.mark.docker
def test_second_suite_failure_stops_without_third_or_environment_fallback(session, monkeypatch):
    from docker.models.containers import Container

    # Controlled fault input through the REAL daemon, after restore and before
    # launching the second project's process. No fake executor or outcome.
    _, runner, state, _, _, _, _, _ = session
    original = Container.exec_run
    ready_containers = []

    def second_fails(container, cmd, *args, **kwargs):
        if isinstance(cmd, list) and len(cmd) == 3 and "/run/robo-executor/ready" in cmd[2]:
            ready_containers.append(container.id)
            if len(ready_containers) == 3:  # install, first suite, second suite
                fault = original(
                    container,
                    [
                        "node",
                        "-e",
                        "require('fs').writeFileSync('/workspace/fault','second repetition')",
                    ],
                    user="1000:1000",
                )
                assert fault.exit_code == 0
        return original(container, cmd, *args, **kwargs)

    monkeypatch.setattr(Container, "exec_run", second_fails)
    result = validate(
        session,
        script=(b"console.log('suite');if(require('fs').existsSync('fault'))process.exit(7)"),
    )
    assert result.outcome == "rejected"
    assert len(ready_containers) == 3
    assert [r.reason for r in runner.results] == ["passed", "passed", "exit_nonzero"]
    suites = [r for r in runner.results if r.phase == "run"]
    assert suites[0].argv == suites[1].argv
    assert suites[0].limits == suites[1].limits
    assert suites[1].exit_code == 7
    assert len(state.suite_runs(result.task_id)) == 2
    assert not runner.prepared


@pytest.mark.docker
def test_three_real_clean_processes_are_not_scope_proof(session):
    result = validate(
        session,
        script=(
            b"const fs=require('fs');if(fs.existsSync('dirty'))process.exit(2);"
            b"if(fs.readFileSync('deps','utf8')!=='installed')process.exit(3);"
            b"fs.writeFileSync('dirty','yes');console.log('fresh')"
        ),
    )
    _, runner, state, artifacts, _, _, _, _ = session
    assert result.outcome == "unsupported"
    suites = [r for r in runner.results if r.phase == "run"]
    assert len(suites) == len({r.container_id for r in suites}) == 3
    assert all(artifacts.get(r.stdout_artifact) == b"fresh\n" for r in suites)
    assert all(r.network_mode == "none" for r in suites)
    assert len(state.suite_runs(result.task_id)) == 3
    assert [a.assessment.status for a in state.assessments(result.task_id)] == [
        "not_evaluated",
        "approved",
    ]
    assert not runner.prepared


@pytest.mark.docker
def test_missing_image_is_inconclusive_and_keeps_history(session):
    service, runner, state, artifacts, run, files, commands, plan = session
    missing = replace(plan, derived_image_id="sha256:" + "0" * 64)
    result = service.validate(run, "repository", missing, commands, archive(files))
    assert result.outcome == "interrupted"
    assert not runner.results
    assert all(a.assessment.status == "not_evaluated" for a in state.assessments(result.task_id))
    assert json.loads(artifacts.get(result.evidence_id))["outcome"] == "interrupted"


@pytest.mark.docker
def test_interruption_between_passes_requires_three_new_consecutive_runs(session, monkeypatch):
    from docker.errors import DockerException
    from docker.models.containers import Container

    original = Container.exec_run
    captures = 0

    def interrupt_second_suite(container, cmd, *args, **kwargs):
        nonlocal captures
        if cmd == ["tar", "-C", "/", "-cf", "-", "workspace"]:
            captures += 1
            if captures == 3:  # install, first suite, second suite
                raise DockerException("controlled transport interruption during capture")
        return original(container, cmd, *args, **kwargs)

    monkeypatch.setattr(Container, "exec_run", interrupt_second_suite)
    first = validate(session)
    _, runner, state, _, _, _, _, _ = session
    assert first.outcome == "interrupted"
    assert len([r for r in runner.results if r.phase == "run"]) == 1
    monkeypatch.setattr(Container, "exec_run", original)
    resumed = validate(session)
    assert resumed.outcome == "unsupported"
    assert len([r for r in runner.results if r.phase == "run"]) == 4
    assert state.get_task(first.task_id).attempt == 2
    assert len(state.suite_runs(first.task_id)) == 5
    assert [r["outcome"] for r in state.suite_runs(first.task_id)] == [
        "completed",
        "interrupted",
        "completed",
        "completed",
        "completed",
    ]


class CoordinatorCrash(BaseException):
    pass


@pytest.mark.docker
@pytest.mark.parametrize("crash_ordinal", [1, 3])
def test_crash_at_durable_suite_result_restarts_or_finishes_offline(
    session, monkeypatch, crash_ordinal
):
    _, runner, state, _, _, _, _, _ = session
    # _record_execution returns only after both suite row and checkpoint commit.
    service = session[0]
    record = service._record_execution

    def record_then_crash(*args, **kwargs):
        result = record(*args, **kwargs)
        if args[3].phase == "run" and result.ordinal == crash_ordinal:
            raise CoordinatorCrash()
        return result

    monkeypatch.setattr(service, "_record_execution", record_then_crash)
    with pytest.raises(CoordinatorCrash):
        validate(session)
    before = len(runner.results)
    with reopen_session(session, offline=crash_ordinal == 3) as restarted:
        result = validate(restarted)
        _, fresh_runner, fresh_state, fresh_artifacts, *_ = restarted
        assert result.outcome == "unsupported"
        assert len(runner.results) == before
        if fresh_runner is not None:
            assert len(fresh_runner.results) == 4
        else:
            assert crash_ordinal == 3
        assert len(fresh_state.suite_runs(result.task_id)) == (4 if crash_ordinal == 1 else 3)
        histories = [
            (event.attempt, json.loads(fresh_artifacts.get(str(event.payload["digest"]))))
            for event in fresh_state.task_checkpoints(result.task_id)
        ]
        sequences = {}
        for attempt, model in histories:
            if model["kind"] == "result" and model["phase"] == "run" and model["ordinal"]:
                sequences.setdefault(attempt, []).append(model["ordinal"])
        assert sequences == ({1: [1], 2: [1, 2, 3]} if crash_ordinal == 1 else {1: [1, 2, 3]})


@pytest.mark.docker
def test_crash_after_persisted_install_failure_never_reopens_repository(session, monkeypatch):
    service, runner, state, _, _, _, _, _ = session
    original = service._record_execution

    def crash_after_failure(*args, **kwargs):
        original(*args, **kwargs)
        raise CoordinatorCrash()

    monkeypatch.setattr(service, "_record_execution", crash_after_failure)
    with pytest.raises(CoordinatorCrash):
        validate(session, install="process.exit(7)")
    with reopen_session(session, offline=True) as restarted:
        result = validate(restarted, install="process.exit(7)")
        fresh_state = restarted[2]
        assert result.outcome == "rejected"
        assert len(runner.results) == 1
        assert not fresh_state.suite_runs(result.task_id)
        assert validate(restarted, install="process.exit(7)") == result
        assert fresh_state.get_task(result.task_id).outcome == "rejected"
        with reopen_session(restarted, offline=True) as terminal_replay:
            assert validate(terminal_replay, install="process.exit(7)") == result
            assert terminal_replay[2].get_task(result.task_id).outcome == "rejected"
            assert len(runner.results) == 1


@pytest.mark.docker
@pytest.mark.parametrize(("framework", "version"), direct_calls.VERSIONS)
def test_full_original_collection_approves_and_replays_after_reopen(
    session, analyzer, monkeypatch, framework, version
):
    from robo_minerador.execution.repetitions import RepositoryValidation
    from robo_minerador.execution.results import load_report

    _, runner, state, artifacts, run, _, _, _ = session
    files = direct_calls.fixture_files(framework, version)
    original_test = files["value.test.ts"]
    extension = "cjs" if framework == "jest" else "mjs"
    reporter = f"reporter.{extension}"
    files[reporter] = (
        ROOT / f"tools/ts-analyzer/src/frameworks/reporters/{framework}.{extension}"
    ).read_bytes()
    if framework == "jest":
        files["jest.config.cjs"] += (
            b"\nmodule.exports.reporters=['./reporter.cjs'];"
            b"\nmodule.exports.testLocationInResults=true;\n"
        )
    else:
        # The already-proved T11 serial forks mode, preserving the original
        # fixture's include and includeTaskLocation. No test source replacement.
        files["vitest.config.ts"] = files["vitest.config.ts"].replace(
            b"includeTaskLocation: true",
            b"includeTaskLocation: true, pool: 'forks', maxWorkers: 1, "
            b"fileParallelism: false, reporters: ['./reporter.mjs']",
        )
    assert files["value.test.ts"] == original_test
    commands = plan_commands(files)
    planned = plan_environment(files, commands, load_catalog(ROOT), ExecutionConfig())
    assert planned.plan is not None
    plan = planned.plan
    runner.install_network = "bridge"
    reports = []

    def read_report(execution):
        raw = direct_calls.workspace_file(artifacts, execution, ".robo-reports/raw.json")
        report = load_report(direct_calls.normalize(analyzer, raw, files), raw, artifacts)
        reports.append(report)
        assert report.framework_version == version
        assert len(report.cases) == 10
        assert any(c.status in {"skipped", "pending"} for c in report.cases)
        assert any(c.status == "todo" for c in report.cases)
        assert any(c.expected_failure and c.status == "passed" for c in report.cases)
        assert max(Counter(c.title_path for c in report.cases).values()) >= 6
        from robo_minerador.execution.repetitions import prove_suite_scope

        assert prove_suite_scope(plan, commands, execution, report, artifacts)
        if framework == "vitest":
            from robo_minerador.execution.repetitions import _vitest_collection

            metadata = json.loads(raw)
            for key, active in {
                "shard": {"index": 1, "count": 2},
                "changed": "HEAD",
                "related": ["value.ts"],
                "project": ["unit"],
                "filters": ["value.test.ts"],
                "cliExclude": ["other.test.ts"],
                "standalone": True,
                "mergeReports": "./reports",
            }.items():
                assert key in metadata["configuration"]
                missing = deepcopy(metadata)
                del missing["configuration"][key]
                assert _vitest_collection(missing, report) == [], key
                selected = deepcopy(metadata)
                selected["configuration"][key] = active
                assert _vitest_collection(selected, report) == [], key
        assert not prove_suite_scope(plan, commands, execution, None, artifacts)
        assert not prove_suite_scope(
            plan,
            commands,
            replace(execution, argv=(*execution.argv, "value.test.ts")),
            report,
            artifacts,
        )
        assert not prove_suite_scope(
            plan,
            commands,
            replace(execution, workspace_artifact=None),
            report,
            artifacts,
        )
        return report

    service = RepositoryValidation(runner, state, artifacts, read_report=read_report)
    save = service._save

    def crash_after_last_scope(task_id, kind, model):
        digest = save(task_id, kind, model)
        if kind == "scope" and model["ordinal"] == 3:
            raise CoordinatorCrash()
        return digest

    monkeypatch.setattr(service, "_save", crash_after_last_scope)
    with pytest.raises(CoordinatorCrash):
        service.validate(run, "repository", plan, commands, archive(files))
    before = len(runner.results)
    with reopen_session(session, offline=True) as restarted:
        offline, _, reopened_state, reopened_artifacts, *_ = restarted
        payload = archive(files)
        result = offline.validate(run, "repository", plan, commands, payload)
        assert result.outcome == "completed"
        assert len(reports) == 3
        assert all(r.completeness == "not_evaluated" and r.scope == "unknown" for r in reports)
        assert [a.assessment.status for a in reopened_state.assessments(result.task_id)] == [
            "approved",
            "approved",
        ]
        checkpoints = [
            json.loads(reopened_artifacts.get(str(event.payload["digest"])))
            for event in reopened_state.task_checkpoints(result.task_id)
        ]
        scopes = [m for m in checkpoints if m["kind"] == "scope"]
        assert [m["ordinal"] for m in scopes] == [1, 2, 3]
        assert [m["report"] for m in scopes] == [
            [r.evidence_id, r.normalized_evidence_id] for r in reports
        ]
        suites = [m for m in checkpoints if m["kind"] == "result" and m["phase"] == "run"]
        assert len({m["execution"]["container_id"] for m in suites}) == 3
        assert all(m["outcome"] == "passed" for m in suites)
        result_digests = {
            model["ordinal"]: str(event.payload["digest"])
            for event in reopened_state.task_checkpoints(result.task_id)
            if (model := json.loads(reopened_artifacts.get(str(event.payload["digest"]))))["kind"]
            == "result"
            and model["phase"] == "run"
        }
        assert all(scope["artifacts"][0] == result_digests[scope["ordinal"]] for scope in scopes)
        facts = reopened_state.assessments(result.task_id)
        for changed_plan, changed_commands, changed_archive in [
            (plan, commands, payload + b"changed"),
            (plan, replace(commands, reason_code="changed"), payload),
            (replace(plan, node_version="changed"), commands, payload),
            (
                replace(plan, node_version="changed"),
                replace(commands, reason_code="changed"),
                payload + b"changed",
            ),
        ]:
            with pytest.raises(ValueError, match="immutable"):
                offline.validate(run, "repository", changed_plan, changed_commands, changed_archive)
        # Each of the THREE actual reports, raw and normalized, must be verified
        # even when the final task is already approved. Restore only test bytes.
        for report in reports:
            for digest in (report.evidence_id, report.normalized_evidence_id):
                path = reopened_artifacts.root / digest
                original = path.read_bytes()
                path.write_bytes(b"corrupt report")
                try:
                    with pytest.raises(ValueError, match="digest mismatch"):
                        offline.validate(run, "repository", plan, commands, payload)
                finally:
                    path.write_bytes(original)
                assert reopened_state.get_task(result.task_id).outcome == "completed"
                assert reopened_state.assessments(result.task_id) == facts
        assert offline.validate(run, "repository", plan, commands, payload) == result
    assert len(runner.results) == before
    print(f"{framework} {version}: 3 full passes, 3 linked reports, H-R05=approved after DB reopen")


@pytest.mark.docker
@pytest.mark.parametrize("version", ["3.2.4", "4.0.0"])
def test_vitest_config_shard_is_three_partial_passes_not_full_scope(session, analyzer, version):
    from robo_minerador.execution.repetitions import RepositoryValidation
    from robo_minerador.execution.results import load_report

    _, runner, state, artifacts, run, *_ = session
    files = direct_calls.fixture_files("vitest", version)
    files["other.test.ts"] = (
        b"import {test,expect} from 'vitest';"
        b"test('other file',()=>{console.log('other shard fixture');expect(1).toBe(1)});"
    )
    files["reporter.mjs"] = (
        ROOT / "tools/ts-analyzer/src/frameworks/reporters/vitest.mjs"
    ).read_bytes()
    files["vitest.config.ts"] = files["vitest.config.ts"].replace(
        b"includeTaskLocation: true",
        b"includeTaskLocation: true, pool: 'forks', maxWorkers: 1, fileParallelism: false, "
        b"shard: '1/2', reporters: ['./reporter.mjs']",
    )
    assert json.loads(files["package.json"])["scripts"]["test"] == "vitest run"
    commands = plan_commands(files)
    planned = plan_environment(files, commands, load_catalog(ROOT), ExecutionConfig())
    assert planned.plan is not None
    plan = planned.plan
    runner.install_network = "bridge"
    reports = []
    raw_reports = []

    def read_report(execution):
        raw = direct_calls.workspace_file(artifacts, execution, ".robo-reports/raw.json")
        report = load_report(direct_calls.normalize(analyzer, raw, files), raw, artifacts)
        reports.append(report)
        raw_reports.append(json.loads(raw))
        return report

    result = RepositoryValidation(runner, state, artifacts, read_report=read_report).validate(
        run, "repository", plan, commands, archive(files)
    )
    suites = [execution for execution in runner.results if execution.phase == "run"]
    assert len(suites) == len(reports) == len(raw_reports) == 3
    assert all(execution.exit_code == 0 and execution.reason == "passed" for execution in suites)
    assert all(
        len(raw["files"]) == len(report.scope_evidence.observed_files) == 1
        for raw, report in zip(raw_reports, reports, strict=True)
    )
    assert all(
        set(report.scope_evidence.observed_files) < {"value.test.ts", "other.test.ts"}
        for report in reports
    )
    assert result.outcome == "unsupported"
    assert [a.assessment.status for a in state.assessments(result.task_id)] == [
        "not_evaluated",
        "approved",
    ]
    assert not json.loads(artifacts.get(result.evidence_id))["repository_excluded"]
    for raw, report, execution in zip(raw_reports, reports, suites, strict=True):
        assert raw["configuration"]["shard"] == {"index": 1, "count": 2}
        assert not any("shard" in arg for arg in execution.argv)
        assert report.evidence_id == artifacts.put(
            direct_calls.workspace_file(artifacts, execution, ".robo-reports/raw.json")
        )
        assert b"vitest run" in artifacts.get(execution.stdout_artifact)
        assert isinstance(artifacts.get(execution.stderr_artifact), bytes)
    checkpoints = [
        json.loads(artifacts.get(str(event.payload["digest"])))
        for event in state.task_checkpoints(result.task_id)
    ]
    scopes = [model for model in checkpoints if model["kind"] == "scope"]
    assert len(scopes) == 3 and all(not scope["scope_proven"] for scope in scopes)
    assert [scope["report"] for scope in scopes] == [
        [report.evidence_id, report.normalized_evidence_id] for report in reports
    ]
    print(
        f"Vitest {version}: exits={[execution.exit_code for execution in suites]}, "
        f"files_per_run={[len(raw['files']) for raw in raw_reports]}, shard=1/2 from config; "
        "unsupported, H-R05=not_evaluated; "
        "3 raw/normalized reports and stdout/stderr pairs captured"
    )


@pytest.mark.docker
def test_node_report_selectors_and_t10_compatibility(analyzer, tmp_path):
    # Compile/test the trusted analyzer in its existing Linux image. Neither
    # repository code nor its dependencies run on the host; no image rebuild.
    files = {}
    for directory in ("src", "test"):
        base = ROOT / "tools/ts-analyzer" / directory
        files.update(
            {
                directory + "/" + path.relative_to(base).as_posix(): path.read_bytes()
                for path in base.rglob("*")
                if path.is_file()
            }
        )
    assert analyzer.put_archive("/app", archive(files))
    built = analyzer.exec_run(["npm", "run", "build"], user="0:0")
    assert built.exit_code == 0, built.output.decode()
    tested = analyzer.exec_run(["node", "--test", "dist/test/frameworks.test.js"], user="1000:1000")
    (tmp_path / "node-frameworks.tap").write_bytes(tested.output)
    print(tested.output.decode())
    assert tested.exit_code == 0, tested.output.decode()
    assert b"# tests 12" in tested.output and b"# fail 0" in tested.output


@pytest.mark.docker
@pytest.mark.parametrize("failed", [False, True])
def test_cleanup_incident_never_erases_failure_or_joins_successes(session, monkeypatch, failed):
    service, runner, state, artifacts, _, _, _, _ = session
    original = runner.dispose

    def cleanup_then_interrupt(prepared_id):
        original(prepared_id)
        from robo_minerador.execution.docker import InfrastructureInterruption

        raise InfrastructureInterruption("controlled cleanup incident", artifacts.put(b"cleanup"))

    monkeypatch.setattr(runner, "dispose", cleanup_then_interrupt)
    script = b"process.exit(7)" if failed else b"console.log('pass')"
    result = validate(session, script=script)
    assert result.outcome == ("rejected" if failed else "interrupted")
    before = len(runner.results)
    monkeypatch.setattr(runner, "dispose", original)
    resumed = validate(session, script=script)
    assert resumed.outcome == ("rejected" if failed else "unsupported")
    assert len(runner.results) - before == (0 if failed else 4)
    assert state.get_task(result.task_id).attempt == (1 if failed else 2)


@pytest.mark.docker
def test_required_build_failure_stops_before_any_suite(session):
    service, runner, state, artifacts, run, files, commands, plan = session
    selected = replace(
        plan,
        build=Command(("node", "-e", "console.error('build failed');process.exit(9)"), ".", 20),
    )
    service.subsequent_criteria = ("H-F01", "H-T01")
    result = service.validate(run, "repository", selected, commands, archive(files))
    assert result.outcome == "rejected"
    assert [(r.phase, r.exit_code) for r in runner.results] == [("install", 0), ("build", 9)]
    assert not state.suite_runs(result.task_id)
    assert b"build failed" in artifacts.get(runner.results[-1].stderr_artifact)
    assert [
        (a.assessment.criterion_id, a.assessment.status, a.assessment.reason_code)
        for a in state.assessments(result.task_id)
    ][-2:] == [
        ("H-F01", "not_evaluated", "prerequisite_not_met"),
        ("H-T01", "not_evaluated", "prerequisite_not_met"),
    ]
    checkpoint_models = [
        json.loads(artifacts.get(str(event.payload["digest"])))
        for event in state.task_checkpoints(result.task_id)
    ]
    assert [(m["phase"], m["outcome"]) for m in checkpoint_models if m["kind"] == "result"] == [
        ("install", "passed"),
        ("build", "failed"),
    ]


@pytest.mark.docker
def test_executor_cleanup_cannot_mask_a_durable_project_failure(session, monkeypatch):
    from robo_minerador.execution.docker import InfrastructureInterruption

    _, runner, state, artifacts, _, _, _, _ = session
    original = runner._remove
    interrupted = False

    def remove_then_interrupt(container):
        nonlocal interrupted
        original(container)
        if runner.results and runner.results[-1].reason != "passed" and not interrupted:
            interrupted = True
            raise InfrastructureInterruption(
                "cleanup after factual failure", artifacts.put(b"incident")
            )

    monkeypatch.setattr(runner, "_remove", remove_then_interrupt)
    result = validate(session, script=b"console.error('real failure');process.exit(7)")
    assert interrupted
    assert result.outcome == "rejected"
    assert len(state.suite_runs(result.task_id)) >= 1
    assert state.assessments(result.task_id)[0].assessment.status == "rejected"
