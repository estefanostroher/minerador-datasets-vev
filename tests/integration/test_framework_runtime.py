"""Real Docker adapter evidence; no project code runs on the coordinator."""

import hashlib
import io
import json
import os
import tarfile
from dataclasses import replace
from pathlib import Path

import docker
import pytest

from robo_minerador.config import ExecutionConfig
from robo_minerador.environments.catalog import load_catalog
from robo_minerador.environments.plan import Command, plan_environment
from robo_minerador.execution.docker import DockerExecutor, ProjectExecutionFailure
from robo_minerador.execution.results import load_report
from robo_minerador.screening.commands import plan_commands
from robo_minerador.state.artifacts import ArtifactStore

ROOT = Path(__file__).resolve().parents[2]
VERSIONS = [("jest", "29.7.0"), ("jest", "30.0.0"), ("vitest", "3.2.4"), ("vitest", "4.0.0")]


def archive(files):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as tar:
        for name, body in files.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(body)
            tar.addfile(entry, io.BytesIO(body))
    return stream.getvalue()


def fixture_files(framework, version):
    base = ROOT / "tests/fixtures/projects" / f"{framework}-basic"
    files = {p.name: p.read_bytes() for p in base.iterdir() if p.is_file()}
    alternate = base / "versions" / version
    if alternate.exists():
        files.update({p.name: p.read_bytes() for p in alternate.iterdir() if p.is_file()})
    return files


def normalize_report(client, raw, files, store):
    """Use the current static analyzer in Docker, without a checked-in runtime export."""
    payload = json.dumps(
        {
            "raw": json.loads(raw),
            "sources": {
                name: body.decode() for name, body in files.items() if name.endswith(".ts")
            },
            "evidence_id": hashlib.sha256(raw).hexdigest(),
        }
    )
    container = client.containers.create(
        "robo-minerador-analyzer",
        [
            "node",
            "-e",
            "process.stdout.write(require('child_process').execFileSync('node',"
            "['dist/src/frameworks/normalize.js'],{input:process.argv[1]}))",
            payload,
        ],
        network_mode="none",
    )
    try:
        current = ROOT / "tools/ts-analyzer/dist/src"
        assert container.put_archive(
            "/app",
            archive(
                {
                    "dist/src/" + p.relative_to(current).as_posix(): p.read_bytes()
                    for p in current.rglob("*")
                    if p.is_file()
                }
            ),
        )
        container.start()
        assert container.wait()["StatusCode"] == 0, container.logs().decode()
        return load_report(container.logs(stdout=True, stderr=False), raw, store)
    finally:
        container.remove(force=True)


@pytest.mark.docker
@pytest.mark.parametrize(("framework", "version"), VERSIONS)
def test_real_framework_report(tmp_path, framework, version):
    files = fixture_files(framework, version)
    planned = plan_environment(files, plan_commands(files), load_catalog(ROOT), ExecutionConfig())
    assert planned.plan is not None, planned
    plan = replace(
        planned.plan,
        build=None,
        install=Command(("npm", "ci", "--no-audit", "--no-fund"), ".", 180),
    )
    client = docker.from_env()
    executor = DockerExecutor(
        client, ArtifactStore(tmp_path / "artifacts"), install_network="bridge"
    )
    extension = "cjs" if framework == "jest" else "mjs"
    reporter = f"{framework}.{extension}"
    supplement = {
        reporter: (ROOT / "tools/ts-analyzer/src/frameworks/reporters" / reporter).read_bytes()
    }
    argv = (
        "node",
        "node_modules/jest/bin/jest.js"
        if framework == "jest"
        else "node_modules/vitest/vitest.mjs",
    )
    argv += ("--runInBand", "--testLocationInResults") if framework == "jest" else ("run",)
    argv += (
        "--reporters" if framework == "jest" else "--reporter",
        f"/opt/robo-reporters/{reporter}",
    )
    try:
        prepared = executor.prepare(plan, archive(files))
        probe = executor.run_fresh(
            prepared.prepared_id,
            (
                "node",
                "-e",
                "const fs=require('fs'),cp=require('child_process');"
                "fs.copyFileSync('/bin/true','/workspace/native');"
                "fs.chmodSync('/workspace/native',0o755);cp.execFileSync('/workspace/native');",
            ),
            plan.limits,
        )
        assert probe.exit_code == 0
        try:
            result = executor.run_fresh(
                prepared.prepared_id, argv, plan.limits, supplements=supplement
            )
        except ProjectExecutionFailure as error:
            pytest.fail(executor.artifacts.get(error.result.stderr_artifact).decode())
        assert result.exit_code == 0
        assert result.workspace_artifact
        with tarfile.open(
            fileobj=io.BytesIO(executor.artifacts.get(result.workspace_artifact))
        ) as tar:
            raw = tar.extractfile("workspace/.robo-reports/raw.json").read()
            assert json.loads(raw)["version"] == version
            assert tar.extractfile("workspace/value.test.ts").read() == files["value.test.ts"]
        assert result.container_id and result.evidence_artifact
        report = normalize_report(client, raw, files, executor.artifacts)
        assert len(report.cases) == 10
        assert sum(case.eligible for case in report.cases) == 7
        assert sum(case.expected_failure for case in report.cases) == 1
        assert report.support.status == "tested"
        assert result.supplements
        inspected = json.loads(executor.artifacts.get(result.container_inspect_artifact))
        assert inspected["HostConfig"]["ReadonlyRootfs"]
        for path, digest in result.supplements:
            assert executor.artifacts.get(digest) == supplement[Path(path).name]
        readonly = executor.run_fresh(
            prepared.prepared_id,
            (
                "node",
                "-e",
                "const fs=require('fs');try{fs.writeFileSync('/opt/robo-reporters/"
                + reporter
                + "','forged');process.exit(2)}catch(e){"
                "if(e.code!=='EROFS'&&e.code!=='EACCES')throw e}",
            ),
            plan.limits,
            supplements=supplement,
        )
        assert readonly.exit_code == 0
        if os.environ.get("ROBO_REFRESH_FRAMEWORK_REPORTS") == "1":
            from dataclasses import asdict

            destination = Path(
                os.environ.get(
                    "ROBO_FRAMEWORK_EVIDENCE", str(ROOT / "results/artifacts/frameworks")
                )
            )
            folder = destination / f"{framework}-{version}"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "raw.json").write_bytes(raw)
            (folder / "stdout.log").write_bytes(executor.artifacts.get(result.stdout_artifact))
            (folder / "stderr.log").write_bytes(executor.artifacts.get(result.stderr_artifact))
            (folder / "execution.json").write_text(
                json.dumps(asdict(result), indent=2), encoding="utf-8"
            )
            (folder / "prepared.json").write_text(
                json.dumps(asdict(prepared), indent=2), encoding="utf-8"
            )
            # Keep audit refs resolvable without storing the huge node_modules workspace.
            audit = ArtifactStore(folder / "storefiles")
            for digest in (
                result.evidence_artifact,
                result.stdout_artifact,
                result.stderr_artifact,
                result.container_inspect_artifact,
                result.storage_observation_artifact,
                prepared.evidence_artifact,
                prepared.plan_artifact,
                prepared.input_archive_artifact,
                *[d for _, d in result.supplements],
                *[
                    digest
                    for command in prepared.commands
                    for digest in (
                        command.evidence_artifact,
                        command.stdout_artifact,
                        command.stderr_artifact,
                        command.container_inspect_artifact,
                        command.storage_observation_artifact,
                    )
                ],
            ):
                audit.put(executor.artifacts.get(digest))
            audit.put(raw)
        failing_argv = argv + (
            ("--testMatch", "**/*.case.ts")
            if framework == "jest"
            else ("--config", "failure.config.ts")
        )
        with pytest.raises(ProjectExecutionFailure) as failure:
            executor.run_fresh(
                prepared.prepared_id, failing_argv, plan.limits, supplements=supplement
            )
        failed = failure.value.result
        assert failed.exit_code == 1 and failed.workspace_artifact
        with tarfile.open(
            fileobj=io.BytesIO(executor.artifacts.get(failed.workspace_artifact))
        ) as tar:
            failed_raw = tar.extractfile("workspace/.robo-reports/raw.json").read()
        failed_report = normalize_report(client, failed_raw, files, executor.artifacts)
        assert len(failed_report.cases) == 1
        assert failed_report.cases[0].status == "failed"
        assert not any(case.eligible for case in failed_report.cases)
        if os.environ.get("ROBO_REFRESH_FRAMEWORK_REPORTS") == "1":
            (folder / "failure.raw.json").write_bytes(failed_raw)
            (folder / "failure.execution.json").write_text(
                json.dumps(asdict(failed), indent=2), encoding="utf-8"
            )
            for digest in (
                failed.evidence_artifact,
                failed.stdout_artifact,
                failed.stderr_artifact,
                failed.container_inspect_artifact,
                failed.storage_observation_artifact,
            ):
                audit.put(executor.artifacts.get(digest))
            audit.put(failed_raw)
    finally:
        executor.close()
        client.close()
