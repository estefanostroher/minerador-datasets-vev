"""Docker API executor: immutable prepared evidence, fresh bounded writable state.

Only the coordinator owns the Docker client and ArtifactStore. Project containers
receive neither host mounts nor coordinator environment. Local image IDs are not
registry promises. tmpfs contents are copied explicitly: Docker commit omits them.
"""

import io
import json
import math
import re
import tarfile
import time
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import PurePosixPath
from typing import Any

from docker import DockerClient
from docker.errors import DockerException, NotFound
from docker.models.containers import Container
from docker.types import LogConfig
from requests.exceptions import RequestException

from robo_minerador.environments.plan import Command, EnvironmentPlan, Limits
from robo_minerador.state.artifacts import ArtifactStore

STORAGE_POLICY = "readonly-root+bounded-tmpfs-v1"
_INFRA_ERRORS = (DockerException, RequestException, OSError)
_SUPERVISOR = """
const fs=require('fs'),cp=require('child_process');
const control='/run/robo-executor/';
const timer=setInterval(()=>{
 if(!fs.existsSync(control+'ready')) return;
 clearInterval(timer);
 const child=cp.spawn(JSON.parse(process.env.EXECUTOR_ARGV)[0],
  JSON.parse(process.env.EXECUTOR_ARGV).slice(1),
  {cwd:process.env.EXECUTOR_CWD,stdio:'inherit',uid:1000,gid:1000});
 const record=result=>fs.writeFileSync(control+'result',JSON.stringify(result),{mode:0o600});
 child.on('error',e=>{console.error(e);record({exit:127});});
 child.on('exit',(code,signal)=>record({exit:code,signal}));
},25);
setInterval(()=>{},1000);
"""


@dataclass(frozen=True)
class RunLimits:
    resources: Limits
    timeout_seconds: int

    def __post_init__(self) -> None:
        r = self.resources
        if (
            not math.isfinite(r.cpus)
            or r.cpus <= 0
            or r.memory_mb <= 0
            or r.workspace_limit_mb <= 0
            or self.timeout_seconds <= 0
        ):
            raise ValueError("execution limits must be positive and finite")


@dataclass(frozen=True)
class ExecutionResult:
    container_id: str
    image_id: str
    argv: tuple[str, ...]
    cwd: str
    environment: tuple[tuple[str, str], ...]
    limits: Limits
    timeout_seconds: int
    network_mode: str
    phase: str
    exit_code: int | None
    signal: str | None
    timed_out: bool
    oom_killed: bool
    elapsed_seconds: float
    reason: str
    stdout_artifact: str
    stderr_artifact: str
    workspace_artifact: str | None
    workspace_used_bytes: int | None
    storage_policy: str
    storage_verified: bool
    storage_observation_artifact: str
    memory_oom_events: int
    container_inspect_artifact: str
    evidence_artifact: str = ""
    supplements: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class PreparedEnvironment:
    prepared_id: str
    image_id: str
    parent_image_id: str
    base_ref: str
    plan_digest: str
    plan_artifact: str
    input_archive_artifact: str
    workspace_artifact: str
    commands: tuple[ExecutionResult, ...]
    install_network: str
    storage_policy: str
    evidence_artifact: str = ""


class ProjectExecutionFailure(RuntimeError):
    def __init__(self, result: ExecutionResult) -> None:
        self.result = result
        super().__init__(result.reason)


class InfrastructureInterruption(RuntimeError):
    def __init__(self, message: str, evidence_artifact: str) -> None:
        self.evidence_artifact = evidence_artifact
        super().__init__(message)


def _canonical_archive(raw: bytes) -> bytes:
    """Accept repository-relative tar (or GitHub's single prefix), never host extract.

    Snapshot callers may supply the original single-root GitHub tar or a canonical
    tar assembled from Snapshot.files. Links must remain within the copied tree.
    """
    output = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as source:
        entries = source.getmembers()
        roots = {m.name.split("/")[0] for m in entries}
        strip = len(roots) == 1 and bool(re.fullmatch(r".+-[0-9a-f]{7,40}", next(iter(roots))))
        links = {(m.name.partition("/")[2] if strip else m.name) for m in entries if m.issym()}
        with tarfile.open(fileobj=output, mode="w") as target:
            seen = set()
            for entry in entries:
                name = entry.name.rstrip("/")
                if strip:
                    _, _, name = name.partition("/")
                    if not name:
                        continue
                parts = name.split("/")
                if (
                    not name
                    or any(p in ("", ".", "..") for p in parts)
                    or "\\" in name
                    or ":" in name
                    or "\x00" in name
                    or name in seen
                    or entry.issparse()
                    or not (entry.isfile() or entry.isdir() or entry.issym())
                ):
                    raise ValueError("unsafe repository archive")
                if any(p in links for p in map(str, PurePosixPath(name).parents)):
                    raise ValueError("archive entry below symlink")
                seen.add(name)
                if entry.issym():
                    link = entry.linkname
                    if not link or link.startswith("/") or "\\" in link or ":" in link:
                        raise ValueError("unsafe archive link")
                    destination = parts[:-1]
                    for index, component in enumerate(link.split("/")):
                        if component == "..":
                            if not destination:
                                raise ValueError("archive link escapes repository")
                            destination.pop()
                        elif component not in ("", "."):
                            destination.append(component)
                        if index < len(link.split("/")) - 1 and "/".join(destination) in links:
                            raise ValueError("archive link traverses another symlink")
                # PAX overrides TarInfo.name/linkname when serialized. Regenerate
                # those headers from the canonical names rather than stale input.
                entry.pax_headers = {
                    k: v for k, v in entry.pax_headers.items() if k not in {"path", "linkpath"}
                }
                entry.name = "workspace/" + name
                entry.uid = entry.gid = 1000
                entry.uname = entry.gname = "node"
                entry.mode &= 0o777
                if entry.issym():
                    entry.mode = 0o777  # Linux symlink permissions are always 0777.
                target.addfile(entry, source.extractfile(entry) if entry.isfile() else None)
    return output.getvalue()


class DockerExecutor:
    def __init__(
        self, client: DockerClient, artifacts: ArtifactStore, *, install_network: str = "none"
    ) -> None:
        if install_network not in {"none", "bridge"}:
            raise ValueError("installation network must be none or bridge")
        self.client, self.artifacts = client, artifacts
        self.install_network = install_network
        self.prepared: dict[str, PreparedEnvironment] = {}
        self._plans: dict[str, EnvironmentPlan] = {}
        self.results: list[ExecutionResult] = []

    def _record(self, data: object) -> str:
        return self.artifacts.put(json.dumps(data, sort_keys=True, allow_nan=False).encode())

    def _interruption(self, error: object, **context: object) -> InfrastructureInterruption:
        evidence = self._record(
            {"kind": "infrastructure_interruption", "error": str(error), **context}
        )
        return InfrastructureInterruption(str(error), evidence)

    def prepare(self, plan: EnvironmentPlan, archive: bytes) -> PreparedEnvironment:
        """Install once, build offline, retain complete workspace and real local image."""
        RunLimits(plan.limits, plan.install.timeout_seconds)
        raw_digest = self.artifacts.put(archive)
        state = _canonical_archive(archive)
        commands = []
        image_id: str | None = None
        try:
            parent = self.client.images.get(plan.derived_image_id)
            if parent.id != plan.derived_image_id or parent.attrs.get("Os") != "linux":
                raise self._interruption("runtime image identity/platform mismatch")
            if parent.attrs.get("Architecture") != "amd64" or parent.attrs["Config"].get("Volumes"):
                raise self._interruption("unsupported runtime architecture or image volumes")
            for phase, command in (("install", plan.install), ("build", plan.build)):
                if command is None:
                    continue
                result = self._execute(
                    plan.derived_image_id,
                    plan,
                    state,
                    command,
                    RunLimits(plan.limits, command.timeout_seconds),
                    self.install_network if phase == "install" else "none",
                    phase,
                )
                commands.append(result)
                assert result.workspace_artifact is not None
                state = self.artifacts.get(result.workspace_artifact)
            # A stopped, non-executing container materializes tmpfs state into an image
            # layer. No project code ever runs with a writable root filesystem.
            image_id = self._stage(plan.derived_image_id, state)
            prepared = PreparedEnvironment(
                uuid.uuid4().hex,
                image_id,
                plan.derived_image_id,
                plan.base_ref,
                plan.digest,
                self._record(asdict(plan)),
                raw_digest,
                self.artifacts.put(state),
                tuple(commands),
                self.install_network,
                STORAGE_POLICY,
            )
            prepared = replace(prepared, evidence_artifact=self._record(asdict(prepared)))
            self.prepared[prepared.prepared_id] = prepared
            self._plans[prepared.prepared_id] = plan
            return prepared
        except _INFRA_ERRORS as error:
            raise self._interruption(error, phase="prepare", plan_digest=plan.digest) from error
        finally:
            if image_id and not any(p.image_id == image_id for p in self.prepared.values()):
                self._remove_image(image_id)

    def _stage(self, parent: str, state: bytes) -> str:
        seed = self.client.containers.create(
            parent,
            ["node", "--version"],
            network_mode="none",
            labels={"robo-minerador.role": "prepared-seed"},
        )
        image_id = None
        succeeded = False
        try:
            output = io.BytesIO()
            with (
                tarfile.open(fileobj=io.BytesIO(state)) as source,
                tarfile.open(fileobj=output, mode="w") as dest,
            ):
                members = source.getmembers()
                file_targets = {m.name for m in members if m.isfile() or m.islnk()}
                for member in members:
                    body = source.extractfile(member) if member.isfile() else None
                    member.pax_headers = {
                        k: v for k, v in member.pax_headers.items() if k not in {"path", "linkpath"}
                    }
                    if member.islnk():
                        target = member.linkname
                        parts = target.split("/")
                        if (
                            len(parts) < 2
                            or parts[0] != "workspace"
                            or any(p in ("", ".", "..") for p in parts)
                            or "\\" in target
                            or ":" in target
                            or "\x00" in target
                            or target not in file_targets
                        ):
                            raise self._interruption("unsafe prepared hardlink target")
                        member.linkname = "prepared/" + target
                    member.name = "prepared/" + member.name
                    dest.addfile(member, body)
            if not seed.put_archive("/", output.getvalue()):
                raise self._interruption("prepared image archive transfer failed")
            image = seed.commit(conf={"Labels": {"robo-minerador.parent": parent}})
            image.reload()
            image_id = image.id
            if not image_id:
                raise self._interruption("daemon returned no prepared image ID")
            check, _ = seed.get_archive("/prepared/workspace")
            observed = _manifest(b"".join(check))
            expected = _manifest(state)
            observed.pop("workspace", None)
            expected.pop("workspace", None)
            if any(observed.get(k) != v for k, v in expected.items()) or any(
                k not in expected and v[0] != "5" for k, v in observed.items()
            ):
                raise self._interruption(
                    "prepared image archive roundtrip mismatch",
                    expected=str(expected),
                    observed=str(observed),
                )
            succeeded = True
            return image_id
        finally:
            try:
                self._remove(seed)
            finally:
                if image_id and not succeeded:
                    self._remove_image(image_id)

    def prepared_archive(self, prepared_id: str) -> bytes:
        """Recover dependencies, build outputs and derived locks for T14/T19."""
        return self.artifacts.get(self.prepared[prepared_id].workspace_artifact)

    def run_fresh(
        self,
        prepared_id: str,
        argv: tuple[str, ...],
        limits: Limits | RunLimits,
        *,
        supplements: dict[str, bytes] | None = None,
    ) -> ExecutionResult:
        prepared, plan = self.prepared[prepared_id], self._plans[prepared_id]
        selected = (
            limits
            if isinstance(limits, RunLimits)
            else RunLimits(limits, plan.suite.timeout_seconds)
        )
        if selected.resources != plan.limits:
            raise ValueError("resources must match the preselected environment plan")
        return self._execute(
            prepared.image_id,
            plan,
            self.prepared_archive(prepared_id),
            Command(argv, plan.suite.cwd, selected.timeout_seconds),
            selected,
            "none",
            "run",
            supplements,
        )

    def _stage_supplements(self, image_id: str, files: dict[str, bytes]) -> str:
        """Separate immutable root layer; never inject modules into the project archive."""
        for name in files:
            if not re.fullmatch(r"[a-zA-Z0-9_.-]+\.(?:cjs|mjs|js)", name):
                raise ValueError("unsafe supplement name")
        seed = self.client.containers.create(image_id, ["node", "--version"], network_mode="none")
        try:
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w") as tar:
                directory = tarfile.TarInfo("opt/robo-reporters")
                directory.type, directory.mode = tarfile.DIRTYPE, 0o555
                tar.addfile(directory)
                for name, body in sorted(files.items()):
                    entry = tarfile.TarInfo("opt/robo-reporters/" + name)
                    entry.size, entry.mode = len(body), 0o444
                    tar.addfile(entry, io.BytesIO(body))
            if not seed.put_archive("/", stream.getvalue()):
                raise self._interruption("supplement transfer failed")
            image = seed.commit()
            image.reload()
            assert image.id
            return image.id
        finally:
            self._remove(seed)

    def _execute(
        self,
        image_id: str,
        plan: EnvironmentPlan,
        state: bytes,
        command: Command,
        limits: RunLimits,
        network: str,
        phase: str,
        supplements: dict[str, bytes] | None = None,
    ) -> ExecutionResult:
        cwd = PurePosixPath(command.cwd)
        if cwd.is_absolute() or ".." in cwd.parts or "\\" in command.cwd or not command.argv:
            raise ValueError("unsafe command cwd or empty argv")
        env = dict(plan.environment)
        allowed = {"CI", "TZ", "LC_ALL", "LANG", "NODE_ENV", "FORCE_COLOR"}
        if env.keys() - allowed:
            raise ValueError("unsupported project environment variable")
        env.update(HOME="/workspace/.home", TMPDIR="/tmp", npm_config_cache="/workspace/.npm")
        env.update(
            EXECUTOR_ARGV=json.dumps(command.argv),
            EXECUTOR_CWD=str(PurePosixPath("/workspace") / cwd),
        )
        container: Container | None = None
        started = time.monotonic()
        stdout = stderr = b""
        storage_verified = False
        timed_out = False
        workspace = None
        used = None
        staged_image = None
        storage_observation = ""
        try:
            if supplements:
                staged_image = self._stage_supplements(image_id, supplements)
                image_id = staged_image
            if phase != "run":
                staged_image = self._stage(image_id, state)
                image_id = staged_image
            container = self.client.containers.create(
                image_id,
                ["node", "-e", _SUPERVISOR],
                entrypoint=[],
                working_dir="/workspace",
                environment=env,
                # Only the trusted supervisor can write the reserved control mount.
                # Its child drops to UID/GID 1000, with no effective capabilities.
                user="0:0",
                network_mode=network,
                read_only=True,
                cap_drop=["ALL"],
                cap_add=["SETUID", "SETGID"],
                security_opt=["no-new-privileges:true"],
                nano_cpus=int(limits.resources.cpus * 1_000_000_000),
                mem_limit=limits.resources.memory_mb * 1024**2,
                memswap_limit=limits.resources.memory_mb * 1024**2,
                pids_limit=256,
                shm_size=1024**2,
                tmpfs={
                    "/workspace": (
                        f"rw,exec,nosuid,nodev,size={limits.resources.workspace_limit_mb}m,"
                        "uid=1000,gid=1000"
                    ),
                    "/tmp": "rw,nosuid,nodev,noexec,size=64m,uid=1000,gid=1000",
                    "/run/robo-executor": "rw,nosuid,nodev,noexec,size=1m,uid=0,gid=0,mode=0700",
                },
                labels={"robo-minerador.role": "execution"},
                log_config=LogConfig(type="json-file", config={"max-size": "16m", "max-file": "1"}),
            )
            container.start()
            container.reload()
            inspected = self._record(container.attrs)
            effective_env = dict(item.split("=", 1) for item in container.attrs["Config"]["Env"])
            if not container.attrs["HostConfig"]["ReadonlyRootfs"]:
                raise self._interruption("daemon did not apply read-only root")
            # Check actual filesystem type and capacity before any repository code.
            probe = container.exec_run(
                [
                    "node",
                    "-e",
                    """
const fs=require('fs');let out={};
for(const p of ['/workspace','/tmp','/run/robo-executor']){
 const s=fs.statfsSync(p),m=fs.statSync(p);
 out[p]={type:s.type,size:s.bsize*s.blocks,uid:m.uid,mode:m.mode&0o777};
}
console.log(JSON.stringify(out));
""",
                ]
            )
            if probe.exit_code != 0:
                raise self._interruption(
                    "storage capability probe failed", output=str(probe.output)
                )
            assert isinstance(probe.output, bytes)
            sizes = json.loads(probe.output)
            self._verify_storage(sizes, limits)
            storage_observation = self._record(sizes)
            storage_verified = True
            restored = container.exec_run(
                ["cp", "-a", "/prepared/workspace/.", "/workspace/"], user="1000:1000"
            )
            if restored.exit_code != 0:
                raise self._interruption(
                    "bounded workspace restore failed", output=str(restored.output)
                )
            home = container.exec_run(["mkdir", "-p", "/workspace/.home"], user="1000:1000")
            if home.exit_code != 0:
                raise self._interruption(
                    "project home initialization failed", output=str(home.output)
                )
            ready = container.exec_run(
                [
                    "node",
                    "-e",
                    "require('fs').writeFileSync('/run/robo-executor/ready','ready',{mode:0o600})",
                ]
            )
            if ready.exit_code != 0:
                raise self._interruption("execution bootstrap failed", output=str(ready.output))
            started = time.monotonic()
            outcome: dict[str, Any] = {}
            while True:
                container.reload()
                if not container.attrs["State"]["Running"]:
                    if not container.attrs["State"].get("OOMKilled"):
                        raise self._interruption(
                            "container stopped without process outcome",
                            state=container.attrs["State"],
                        )
                    break
                observed = container.exec_run(
                    [
                        "node",
                        "-e",
                        "const fs=require('fs');if(fs.existsSync('/run/robo-executor/result'))"
                        "process.stdout.write(fs.readFileSync('/run/robo-executor/result'));"
                        "else process.exit(1)",
                    ]
                )
                if observed.exit_code == 0:
                    assert isinstance(observed.output, bytes)
                    outcome = json.loads(observed.output)
                    break
                if time.monotonic() - started >= limits.timeout_seconds:
                    timed_out = True
                    container.kill()
                    container.reload()
                    break
                time.sleep(0.05)
            oom_events = 0
            if container.attrs["State"]["Running"]:
                memory = container.exec_run(["cat", "/sys/fs/cgroup/memory.events"])
                if memory.exit_code == 0:
                    assert isinstance(memory.output, bytes)
                    events = {}
                    for line in memory.output.decode().splitlines():
                        name, value = line.split()
                        events[name] = value
                    oom_events = int(events.get("oom_kill", 0))
                usage = container.exec_run(
                    [
                        "node",
                        "-e",
                        "const s=require('fs').statfsSync('/workspace');"
                        "console.log((s.blocks-s.bfree)*s.bsize)",
                    ]
                )
                if usage.exit_code == 0:
                    assert isinstance(usage.output, bytes)
                    used = int(usage.output)
                captured = container.exec_run(["tar", "-C", "/", "-cf", "-", "workspace"])
                if captured.exit_code != 0:
                    raise self._interruption("workspace capture failed")
                assert isinstance(captured.output, bytes)
                workspace = self.artifacts.put(captured.output)
            stdout = container.logs(stdout=True, stderr=False)
            stderr = container.logs(stdout=False, stderr=True)
            oom = bool(container.attrs["State"].get("OOMKilled")) or oom_events > 0
            code = outcome.get("exit")
            if oom:
                reason = "oom"
            elif timed_out:
                reason = "timeout"
            else:
                reason = "exit_nonzero" if code != 0 else "passed"
            result = ExecutionResult(
                str(container.id),
                image_id,
                command.argv,
                env["EXECUTOR_CWD"],
                tuple(sorted(effective_env.items())),
                limits.resources,
                limits.timeout_seconds,
                network,
                phase,
                code,
                outcome.get("signal"),
                timed_out,
                oom,
                time.monotonic() - started,
                reason,
                self.artifacts.put(stdout),
                self.artifacts.put(stderr),
                workspace,
                used,
                STORAGE_POLICY,
                storage_verified,
                storage_observation,
                oom_events,
                inspected,
            )
            result = replace(
                result,
                supplements=tuple(
                    ("/opt/robo-reporters/" + name, self.artifacts.put(body))
                    for name, body in sorted((supplements or {}).items())
                ),
            )
            result = replace(result, evidence_artifact=self._record(asdict(result)))
            self.results.append(result)
            if reason != "passed":
                raise ProjectExecutionFailure(result)
            return result
        except ProjectExecutionFailure:
            raise  # The result already references both persisted log streams.
        except Exception as error:
            if container is not None:
                # Explicit InfrastructureInterruption and unexpected coordinator
                # errors need the same audit capture as Docker transport errors.
                for stream in ("stdout", "stderr"):
                    try:
                        body = container.logs(stdout=stream == "stdout", stderr=stream == "stderr")
                        if stream == "stdout":
                            stdout = body
                        else:
                            stderr = body
                    except _INFRA_ERRORS:
                        pass
            raise self._interruption(
                error,
                phase=phase,
                container_id=container.id if container else None,
                stdout_artifact=self.artifacts.put(stdout),
                stderr_artifact=self.artifacts.put(stderr),
                cause_evidence_artifact=getattr(error, "evidence_artifact", None),
            ) from error
        finally:
            try:
                if container is not None:
                    self._remove(container)
            finally:
                if staged_image is not None:
                    self._remove_image(staged_image)

    def _remove(self, container: Container) -> None:
        try:
            container.remove(force=True, v=True)
        except NotFound:
            pass
        except _INFRA_ERRORS as error:
            raise self._interruption(error, cleanup_container=container.id) from error

    def _verify_storage(self, sizes: dict[str, Any], limits: RunLimits) -> None:
        for path, maximum in (
            ("/workspace", limits.resources.workspace_limit_mb),
            ("/tmp", 64),
            ("/run/robo-executor", 1),
        ):
            if (
                sizes[path]["type"] != 0x01021994
                or not 0 < sizes[path]["size"] <= maximum * 1024**2
            ):
                raise self._interruption("bounded tmpfs unavailable", observed=sizes)
        control = sizes["/run/robo-executor"]
        if control["uid"] != 0 or control["mode"] != 0o700:
            raise self._interruption("private supervisor control mount unavailable", observed=sizes)

    def dispose(self, prepared_id: str) -> None:
        prepared = self.prepared.get(prepared_id)
        if prepared is None:
            return
        self._remove_image(prepared.image_id)
        del self.prepared[prepared_id]
        del self._plans[prepared_id]

    def _remove_image(self, image_id: str) -> None:
        try:
            self.client.images.remove(image_id, force=True, noprune=True)
        except NotFound:
            pass
        except _INFRA_ERRORS as error:
            raise self._interruption(error, cleanup_image=image_id) from error

    def close(self) -> None:
        for prepared_id in tuple(self.prepared):
            self.dispose(prepared_id)


def _manifest(raw: bytes) -> dict[str, tuple[str, int, bytes | str]]:
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        members = {m.name.rstrip("/"): m for m in tar.getmembers()}
        groups: dict[str, list[str]] = {}
        for name, member in members.items():
            if not (member.isfile() or member.islnk()):
                continue
            root = member
            visited = set()
            while root.islnk():
                if root.name in visited:
                    raise ValueError("cyclic prepared hardlink")
                visited.add(root.name)
                root = members[root.linkname]
            if not root.isfile():
                raise ValueError("prepared hardlink target is not a regular file")
            groups.setdefault(root.name, []).append(name)
        canonical = {name: min(group) for group in groups.values() for name in group}
        result: dict[str, tuple[str, int, bytes | str]] = {}
        for name, member in members.items():
            if name in canonical:
                if canonical[name] == name:
                    body = tar.extractfile(member)
                    assert body is not None
                    result[name] = ("0", member.mode, body.read())
                else:
                    result[name] = ("1", member.mode, canonical[name])
            else:
                result[name] = (member.type.decode(), member.mode, member.linkname)
        return result
