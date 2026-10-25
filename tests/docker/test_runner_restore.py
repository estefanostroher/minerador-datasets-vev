"""Real-daemon restoration and failure evidence; no daemon skips."""

import importlib.util
import io
import json
import tarfile
from dataclasses import asdict, replace
from pathlib import Path

import docker
import pytest

from robo_minerador.config import ExecutionConfig
from robo_minerador.environments.catalog import load_catalog
from robo_minerador.environments.plan import Command, plan_environment
from robo_minerador.screening.commands import plan_commands
from robo_minerador.state.artifacts import ArtifactStore


def archive(files):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as tar:
        for name, body in files.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(body)
            tar.addfile(entry, io.BytesIO(body))
    return stream.getvalue()


@pytest.fixture
def runner(tmp_path):
    assert importlib.util.find_spec("robo_minerador.execution"), "T12 executor is missing"
    from robo_minerador.execution.docker import DockerExecutor

    client = docker.from_env()
    assert client.ping()
    executor = DockerExecutor(client, ArtifactStore(tmp_path / "artifacts"))
    yield executor
    executor.close()
    client.close()


@pytest.fixture
def plan():
    contents = {
        "package.json": json.dumps(
            {"scripts": {"test": "vitest"}, "devDependencies": {"vitest": "^3"}}
        ).encode(),
        "package-lock.json": b'{"lockfileVersion":3}',
    }
    result = plan_environment(
        contents, plan_commands(contents), load_catalog(Path("/app")), ExecutionConfig()
    )
    assert result.plan is not None
    return replace(
        result.plan,
        install=Command(
            ("node", "-e", "require('fs').writeFileSync('derived.lock','deps')"), ".", 20
        ),
        build=Command(("node", "-e", "require('fs').writeFileSync('build.js','built')"), ".", 20),
        suite=Command(("node", "--version"), ".", 20),
    )


@pytest.mark.docker
def test_prepared_dependencies_build_and_locks_survive_but_run_writes_do_not(runner, plan):
    prepared = runner.prepare(plan, archive({"original.txt": b"original"}))
    assert prepared.image_id.startswith("sha256:")
    assert prepared.parent_image_id == plan.derived_image_id
    assert len(prepared.commands) == 2
    json.dumps(asdict(prepared))
    script = """
const fs=require('fs');
for(const f of ['original.txt','derived.lock','build.js']) if(!fs.existsSync(f)) process.exit(2);
if(fs.existsSync('generated')) process.exit(3);
fs.writeFileSync('generated','run'); console.log('fresh');
"""
    first = runner.run_fresh(prepared.prepared_id, ("node", "-e", script), plan.limits)
    second = runner.run_fresh(prepared.prepared_id, ("node", "-e", script), plan.limits)
    assert first.container_id != second.container_id
    assert runner.artifacts.get(first.stdout_artifact) == b"fresh\n"
    assert first.network_mode == second.network_mode == "none"
    assert first.limits == plan.limits
    assert "PATH" in dict(first.environment)
    inspected = json.loads(runner.artifacts.get(first.container_inspect_artifact))
    assert inspected["HostConfig"]["NanoCpus"] == 2_000_000_000
    assert inspected["HostConfig"]["Memory"] == 4096 * 1024**2
    assert not inspected["HostConfig"]["Binds"]
    assert not runner.client.containers.list(all=True, filters={"id": first.container_id})
    state = runner.prepared_archive(prepared.prepared_id)
    with tarfile.open(fileobj=io.BytesIO(state)) as tar:
        names = tar.getnames()
        assert "workspace/derived.lock" in names and "workspace/build.js" in names
        assert "workspace/generated" not in names
    runner.dispose(prepared.prepared_id)
    assert runner.artifacts.get(first.stdout_artifact) == b"fresh\n"
    assert runner.artifacts.get(prepared.evidence_artifact)


@pytest.mark.docker
def test_prepared_copy_preserves_long_pax_paths(runner, plan):
    name = "node_modules/" + "long-segment/" * 12 + "original.js"
    link = "node_modules/" + "long-segment/" * 12 + "alias.js"
    # A prepared copy can contain paths longer than a classic TAR header.
    prepared = runner.prepare(plan, archive({name: b"original", link: b"original"}))
    result = runner.run_fresh(
        prepared.prepared_id,
        (
            "node",
            "-e",
            "const fs=require('fs');for(const p of process.argv.slice(1))"
            "if(fs.readFileSync(p,'utf8')!=='original')process.exit(2)",
            name,
            link,
        ),
        plan.limits,
    )
    assert result.exit_code == 0


@pytest.mark.docker
def test_nonzero_and_timeout_keep_logs_and_cleanup(runner, plan):
    from robo_minerador.execution.docker import ProjectExecutionFailure, RunLimits

    prepared = runner.prepare(plan, archive({"original.txt": b"original"}))
    for script, timeout, reason in [
        ("console.log('before'); console.error('error'); process.exit(7)", 10, "exit_nonzero"),
        ("console.log('before'); setInterval(()=>{},1000)", 1, "timeout"),
    ]:
        with pytest.raises(ProjectExecutionFailure) as failure:
            runner.run_fresh(
                prepared.prepared_id, ("node", "-e", script), RunLimits(plan.limits, timeout)
            )
        result = failure.value.result
        assert result.reason == reason
        assert b"before" in runner.artifacts.get(result.stdout_artifact)
        assert result.timed_out == (reason == "timeout")
        assert not runner.client.containers.list(all=True, filters={"id": result.container_id})
        assert runner.artifacts.get(result.evidence_artifact)


@pytest.mark.docker
def test_install_failure_stops_before_build_and_retains_evidence(runner, plan):
    from robo_minerador.execution.docker import ProjectExecutionFailure

    broken = replace(
        plan, install=Command(("node", "-e", "console.log('install');process.exit(8)"), ".", 10)
    )
    with pytest.raises(ProjectExecutionFailure) as failure:
        runner.prepare(broken, archive({"original.txt": b"original"}))
    assert failure.value.result.exit_code == 8
    assert len(runner.results) == 1
    assert not runner.prepared


@pytest.mark.docker
def test_missing_runtime_is_infrastructure_interruption(runner, plan):
    from robo_minerador.execution.docker import InfrastructureInterruption

    with pytest.raises(InfrastructureInterruption) as failure:
        runner.prepare(replace(plan, derived_image_id="sha256:" + "0" * 64), archive({"a": b"a"}))
    assert runner.artifacts.get(failure.value.evidence_artifact)


@pytest.mark.docker
def test_actual_npm_install_build_and_recovered_dependency_tree(runner, plan):
    files = {
        "package.json": (
            b'{"name":"fixture","version":"1.0.0","dependencies":{"local-dep":"file:./dep"}}'
        ),
        "dep/package.json": b'{"name":"local-dep","version":"1.0.0","main":"index.js"}',
        "dep/index.js": b"module.exports=42;",
    }
    selected = replace(
        plan,
        install=Command(
            ("npm", "install", "--offline", "--ignore-scripts", "--no-audit", "--no-fund"), ".", 30
        ),
        build=Command(
            ("node", "-e", "require('fs').writeFileSync('build.js',String(require('local-dep')))"),
            ".",
            20,
        ),
    )
    prepared = runner.prepare(selected, archive(files))
    assert prepared.plan_artifact
    script = (
        "console.log(require('local-dep'));"
        "console.log(require('fs').readFileSync('build.js','utf8'))"
    )
    result = runner.run_fresh(prepared.prepared_id, ("node", "-e", script), selected.limits)
    assert runner.artifacts.get(result.stdout_artifact) == b"42\n42\n"
    with tarfile.open(fileobj=io.BytesIO(runner.prepared_archive(prepared.prepared_id))) as tar:
        assert "workspace/package-lock.json" in tar.getnames()
        assert "workspace/node_modules/local-dep" in tar.getnames()
    assert prepared.commands[1].network_mode == "none"
    assert runner.artifacts.get(result.storage_observation_artifact)


@pytest.mark.docker
def test_real_oom_is_project_failure_with_evidence(runner, plan):
    from robo_minerador.execution.docker import ProjectExecutionFailure

    small = replace(plan, limits=replace(plan.limits, memory_mb=128))
    prepared = runner.prepare(small, archive({"original.txt": b"original"}))
    script = (
        "console.log('allocating');let a=[];"
        "setInterval(()=>a.push(Buffer.alloc(16*1024*1024,1)),10)"
    )
    with pytest.raises(ProjectExecutionFailure) as failure:
        runner.run_fresh(prepared.prepared_id, ("node", "-e", script), small.limits)
    assert failure.value.result.reason == "oom"
    assert failure.value.result.oom_killed
    assert b"allocating" in runner.artifacts.get(failure.value.result.stdout_artifact)


def test_daemon_unavailable_is_interruption_with_persistent_evidence(tmp_path, plan):
    from unittest.mock import Mock

    from docker.errors import DockerException

    from robo_minerador.execution.docker import DockerExecutor, InfrastructureInterruption

    client = Mock()
    client.images.get.side_effect = DockerException("daemon unavailable")
    executor = DockerExecutor(client, ArtifactStore(tmp_path))
    with pytest.raises(InfrastructureInterruption) as failure:
        executor.prepare(plan, archive({"a": b"a"}))
    assert b"daemon unavailable" in executor.artifacts.get(failure.value.evidence_artifact)
    client.containers.create.assert_not_called()


def test_cleanup_daemon_failure_is_interruption_not_project_failure(tmp_path):
    from unittest.mock import Mock

    from docker.errors import DockerException

    from robo_minerador.execution.docker import DockerExecutor, InfrastructureInterruption

    client = Mock()
    client.images.remove.side_effect = DockerException("daemon disappeared during cleanup")
    executor = DockerExecutor(client, ArtifactStore(tmp_path))
    with pytest.raises(InfrastructureInterruption) as failure:
        executor._remove_image("sha256:" + "0" * 64)
    assert executor.artifacts.get(failure.value.evidence_artifact)


@pytest.mark.docker
def test_absent_storage_capability_prevents_project_execution(runner, plan, monkeypatch):
    from robo_minerador.execution.docker import InfrastructureInterruption

    # Keep the daemon real; simulate only a platform capability observation.
    monkeypatch.setattr(
        runner,
        "_verify_storage",
        lambda *_: (_ for _ in ()).throw(runner._interruption("bounded tmpfs unavailable")),
    )
    with pytest.raises(InfrastructureInterruption):
        runner.prepare(plan, archive({"a": b"a"}))
    assert runner.results == []
    assert not runner.prepared


@pytest.mark.docker
def test_only_install_can_use_bridge_build_and_run_are_offline(runner, plan):
    from robo_minerador.execution.docker import DockerExecutor

    executor = DockerExecutor(runner.client, runner.artifacts, install_network="bridge")
    install = "if(!require('os').networkInterfaces().eth0)process.exit(2)"
    offline = "if(require('os').networkInterfaces().eth0)process.exit(3)"
    selected = replace(
        plan,
        install=Command(("node", "-e", install), ".", 20),
        build=Command(("node", "-e", offline), ".", 20),
    )
    try:
        prepared = executor.prepare(selected, archive({"original.txt": b"original"}))
        assert prepared.install_network == "bridge"
        assert [r.network_mode for r in prepared.commands] == ["bridge", "none"]
        result = executor.run_fresh(prepared.prepared_id, ("node", "-e", offline), plan.limits)
        assert result.reason == "passed"
    finally:
        executor.close()


@pytest.mark.docker
def test_archive_preserves_single_directory_tree_and_safe_parent_symlink(runner, plan):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        entry = tarfile.TarInfo("src/value.txt")
        entry.size = 5
        tar.addfile(entry, io.BytesIO(b"value"))
        link = tarfile.TarInfo("src/nested/link.txt")
        link.type, link.linkname = tarfile.SYMTYPE, "../value.txt"
        tar.addfile(link)
    prepared = runner.prepare(plan, raw.getvalue())
    script = "console.log(require('fs').readFileSync('src/nested/link.txt','utf8'))"
    result = runner.run_fresh(prepared.prepared_id, ("node", "-e", script), plan.limits)
    assert runner.artifacts.get(result.stdout_artifact) == b"value\n"


@pytest.mark.docker
def test_github_gzip_archive_with_short_commit_prefix(runner, plan):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as tar:
        root = tarfile.TarInfo("owner-project-abcdef0")
        root.type = tarfile.DIRTYPE
        tar.addfile(root)
        entry = tarfile.TarInfo("owner-project-abcdef0/original.txt")
        entry.size = 8
        tar.addfile(entry, io.BytesIO(b"original"))
    prepared = runner.prepare(plan, raw.getvalue())
    script = "console.log(require('fs').readFileSync('original.txt','utf8'))"
    result = runner.run_fresh(prepared.prepared_id, ("node", "-e", script), plan.limits)
    assert runner.artifacts.get(result.stdout_artifact) == b"original\n"


@pytest.mark.docker
def test_official_npm_postinstall_hardlinks_survive_preparation_and_fresh_restore(runner):
    files = {
        "package.json": json.dumps(
            {
                "name": "hardlink-fixture",
                "version": "1.0.0",
                "scripts": {"postinstall": "node postinstall.js", "test": "vitest"},
                "config": {"robo_minerador": {"allow_lockfile_generation": True}},
            }
        ).encode(),
        "postinstall.js": (
            b"const fs=require('fs');fs.writeFileSync('a','prepared');"
            b"fs.linkSync('a','b');fs.symlinkSync('a','relative-link');"
        ),
        "vitest.config.js": b"export default {};",
    }
    resolved = plan_environment(
        files, plan_commands(files), load_catalog(Path("/app")), ExecutionConfig()
    )
    assert resolved.plan is not None
    selected = resolved.plan
    assert selected.install.argv == ("npm", "install")
    prepared = runner.prepare(selected, archive(files))
    with tarfile.open(fileobj=io.BytesIO(runner.prepared_archive(prepared.prepared_id))) as tar:
        links = [m for m in tar if m.islnk()]
        assert links and all(m.linkname.startswith("workspace/") for m in links)
        assert tar.getmember("workspace/relative-link").linkname == "a"
    script = """
const fs=require('fs');
if(fs.readFileSync('a','utf8')!=='prepared'||fs.readFileSync('b','utf8')!=='prepared')process.exit(2);
if(fs.statSync('a').ino!==fs.statSync('b').ino)process.exit(3);
if(fs.readlinkSync('relative-link')!=='a')process.exit(4);
fs.writeFileSync('b','changed');console.log(fs.readFileSync('a','utf8'));
"""
    first = runner.run_fresh(prepared.prepared_id, ("node", "-e", script), selected.limits)
    second = runner.run_fresh(prepared.prepared_id, ("node", "-e", script), selected.limits)
    assert first.container_id != second.container_id
    assert runner.artifacts.get(first.stdout_artifact) == b"changed\n"
    assert runner.artifacts.get(second.stdout_artifact) == b"changed\n"


@pytest.mark.docker
def test_full_project_tmp_is_project_failure_and_both_audit_logs_survive(runner, plan):
    from robo_minerador.execution.docker import ProjectExecutionFailure

    prepared = runner.prepare(plan, archive({"original.txt": b"original"}))
    script = """
const fs=require('fs');
console.log('before stdout');console.error('before stderr');
const fd=fs.openSync('/tmp/full','w'),block=Buffer.alloc(1024*1024,1);
try{for(;;)fs.writeSync(fd,block);}catch(e){
 console.log('stdout '+e.code);console.error('stderr '+e.code);throw e;
}
"""
    with pytest.raises(ProjectExecutionFailure) as failure:
        runner.run_fresh(prepared.prepared_id, ("node", "-e", script), plan.limits)
    result = failure.value.result
    assert result.reason == "exit_nonzero" and result.exit_code == 1
    assert not result.timed_out and not result.oom_killed
    runner.dispose(prepared.prepared_id)
    evidence = json.loads(runner.artifacts.get(result.evidence_artifact))
    assert b"stdout ENOSPC" in runner.artifacts.get(evidence["stdout_artifact"])
    assert b"stderr ENOSPC" in runner.artifacts.get(evidence["stderr_artifact"])
    assert not runner.client.containers.list(all=True, filters={"id": result.container_id})


@pytest.mark.docker
def test_project_cannot_write_reserved_control_or_forge_outcome(runner, plan):
    from robo_minerador.execution.docker import ProjectExecutionFailure

    prepared = runner.prepare(plan, archive({"original.txt": b"original"}))
    script = r"""
const fs=require('fs');
if(process.getuid()!==1000)process.exit(2);
const status=fs.readFileSync('/proc/self/status','utf8');
if(!/^CapEff:\s+0+$/m.test(status))process.exit(3);
try{fs.writeFileSync('/run/robo-executor/result','{"exit":0}');process.exit(4);}
catch(e){if(e.code!=='EACCES')throw e;console.log('control denied');}
fs.writeFileSync('/tmp/executor-result','{"exit":0}');
console.error('actual failure');process.exit(7);
"""
    with pytest.raises(ProjectExecutionFailure) as failure:
        runner.run_fresh(prepared.prepared_id, ("node", "-e", script), plan.limits)
    assert failure.value.result.exit_code == 7
    assert b"control denied" in runner.artifacts.get(failure.value.result.stdout_artifact)


@pytest.mark.docker
def test_explicit_infrastructure_error_also_captures_logs_before_cleanup(runner, plan, monkeypatch):
    from docker.models.containers import Container

    from robo_minerador.execution.docker import InfrastructureInterruption

    prepared = runner.prepare(plan, archive({"original.txt": b"original"}))
    original = Container.exec_run

    def fail_capture(container, cmd, *args, **kwargs):
        if cmd == ["tar", "-C", "/", "-cf", "-", "workspace"]:
            raise runner._interruption("injected workspace capture error")
        return original(container, cmd, *args, **kwargs)

    monkeypatch.setattr(Container, "exec_run", fail_capture)
    with pytest.raises(InfrastructureInterruption) as failure:
        runner.run_fresh(
            prepared.prepared_id,
            ("node", "-e", "console.log('audit stdout');console.error('audit stderr')"),
            plan.limits,
        )
    evidence = json.loads(runner.artifacts.get(failure.value.evidence_artifact))
    assert b"audit stdout" in runner.artifacts.get(evidence["stdout_artifact"])
    assert b"audit stderr" in runner.artifacts.get(evidence["stderr_artifact"])
    assert not runner.client.containers.list(all=True, filters={"id": evidence["container_id"]})
