"""Private analyzer HTTP over Docker exec, with verified CAS-to-volume transfer."""

import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path, PurePosixPath
from uuid import uuid4

import docker
import httpx

from robo_minerador.environments.catalog import npm_satisfies
from robo_minerador.extraction.context import preserve_context

FETCH = """
const p=JSON.parse(process.argv[1]);
fetch('http://127.0.0.1:3000'+p.path,{
 method:p.method, ...(p.method==='POST'?{headers:{'Content-Type':'application/json'},
 body:p.body}:{}), signal:AbortSignal.timeout(45000)
}).then(async r=>console.log(JSON.stringify({status:r.status,body:await r.json()})))
.catch(()=>process.exit(1));
"""


class DockerTransport(httpx.BaseTransport):
    def __init__(self, container):
        self.container = container

    def handle_request(self, request):
        if (request.method, request.url.path) not in {("GET", "/health"), ("POST", "/v1/analyze")}:
            raise httpx.TransportError("unsupported analyzer route")
        payload = {
            "method": request.method,
            "path": request.url.path,
            "body": request.content.decode("utf-8"),
        }
        try:
            result = self.container.exec_run(["node", "-e", FETCH, json.dumps(payload)])
            if result.exit_code:
                raise ValueError
            envelope = json.loads(result.output)
            return httpx.Response(envelope["status"], json=envelope["body"], request=request)
        except Exception:
            raise httpx.TransportError("private analyzer transport unavailable") from None


def execute(container, script, value):
    response = container.exec_run(["node", "-e", script, json.dumps(value)])
    if response.exit_code:
        raise ValueError("analyzer helper operation failed")
    return json.loads(response.output)


class AnalyzerSession:
    """Reuse exactly one healthy analyzer owned by the expected Compose directory.

    Nothing is started implicitly. --build-analyzer explicitly builds/starts only
    the analyzer, with token variables removed from the subprocess environment.
    """

    def __init__(self, root, *, build=False):
        self.root = root
        self.client = None
        self.container = None
        self.metadata = None
        self.build = build

    def __enter__(self):
        import os

        if self.build:
            env = {k: v for k, v in os.environ.items() if k.upper() != "GITHUB_TOKEN"}
            # Prevent Compose's implicit dotenv loading (the file may contain credentials).
            command = [
                "docker",
                "compose",
                "--env-file",
                os.devnull,
                "--project-directory",
                str(self.root),
                "-f",
                str(self.root / "compose.yaml"),
                "--profile",
                "analyzer",
                "up",
                "-d",
                "--wait",
                "--build",
                "analyzer",
            ]
            subprocess.run(
                command,
                cwd=self.root,
                env=env,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=600,
            )
        self.client = docker.from_env(timeout=60)
        try:
            candidates = []
            for container in self.client.containers.list(
                filters={"label": "com.docker.compose.service=analyzer"}
            ):
                labels = container.labels
                working = labels.get("com.docker.compose.project.working_dir", "")
                files = labels.get("com.docker.compose.project.config_files", "").split(",")
                if Path(working).resolve() == self.root and files == [
                    str(self.root / "compose.yaml")
                ]:
                    candidates.append(container)
            if len(candidates) != 1:
                raise RuntimeError("prepare exactly one analyzer for this Compose directory")
            self.container = candidates[0]
            self.container.reload()
            attrs = self.container.attrs
            if attrs["State"].get("Health", {}).get("Status") != "healthy":
                raise RuntimeError("analyzer must be healthy")
            if any(e.split("=", 1)[0] == "GITHUB_TOKEN" for e in attrs["Config"]["Env"]):
                raise ValueError("analyzer must not receive credentials")
            with httpx.Client(transport=DockerTransport(self.container)) as http:
                response = http.get("http://analyzer/health")
                response.raise_for_status()
                health = response.json()
            package = json.loads((self.root / "tools/ts-analyzer/package.json").read_bytes())
            if (
                health["protocol_version"] != "1.0"
                or health["library_version"] != package["version"]
                or health["typescript_version"] != package["dependencies"]["typescript"]
                or health["ts_morph_version"] != package["dependencies"]["ts-morph"]
                or not npm_satisfies(health["node_version"], package["engines"]["node"])
            ):
                raise ValueError("stale analyzer versions")
            expected = {}
            for folder, remote in (
                ("tools/ts-analyzer/src", "/app/src"),
                ("schemas", "/app/schemas"),
            ):
                for path in (self.root / folder).rglob("*"):
                    if path.is_file():
                        expected[remote + "/" + path.relative_to(self.root / folder).as_posix()] = (
                            hashlib.sha256(path.read_bytes()).hexdigest()
                        )
            for name in ("package.json", "package-lock.json", "tsconfig.json"):
                expected["/app/" + name] = hashlib.sha256(
                    (self.root / "tools/ts-analyzer" / name).read_bytes()
                ).hexdigest()
            actual = execute(
                self.container,
                """
const fs=require('fs'),c=require('crypto'),paths=JSON.parse(process.argv[1]);
console.log(JSON.stringify(Object.fromEntries(paths.map(p=>
 [p,c.createHash('sha256').update(fs.readFileSync(p)).digest('hex')]))));
""",
                list(expected),
            )
            if expected != actual:
                raise ValueError("stale analyzer sources; explicitly build analyzer")
            mounts = [m for m in attrs["Mounts"] if m["Destination"] == "/workspace"]
            if len(mounts) != 1 or mounts[0]["Type"] != "volume" or mounts[0]["RW"]:
                raise ValueError("analyzer requires read-only named workspace volume")
            self.volume = mounts[0]["Name"]
            self.metadata = {
                "health": health,
                "source_verified": True,
                "image_id": attrs["Image"],
                "container_id": self.container.id,
                "workspace_volume": self.volume,
            }
            return self
        except BaseException:
            self.client.close()
            raise

    def __exit__(self, *args):
        self.client.close()

    def transfer(self, snapshot, artifacts, run_id):
        preserve_context(snapshot, artifacts)  # Validates paths, bytes and internal symlinks first.
        if not run_id or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for c in run_id
        ):
            raise ValueError("unsafe run identity")
        # The existing analyzer intentionally confines reads to /workspace/snapshots.
        parent = "/workspace/snapshots/manual-stages/" + run_id
        destination = parent + "/" + snapshot.tree_sha256
        expected = []
        stream = io.BytesIO()
        directories = set()
        for entry in snapshot.files:
            directories.update(str(p) for p in PurePosixPath(entry.path).parents if str(p) != ".")
            if entry.kind == "directory":
                directories.add(entry.path)
        with tarfile.open(fileobj=stream, mode="w") as archive:
            for path in sorted(directories):
                member = tarfile.TarInfo(path)
                member.type, member.mode = tarfile.DIRTYPE, 0o755
                archive.addfile(member)
                expected.append({"path": path, "kind": "directory", "mode": 0o755})
            for entry in snapshot.files:
                if entry.kind == "directory":
                    continue
                member = tarfile.TarInfo(entry.path)
                if entry.kind == "symlink":
                    member.type, member.linkname, member.mode = tarfile.SYMTYPE, entry.target, 0o777
                    archive.addfile(member)
                    expected.append(
                        {
                            "path": entry.path,
                            "kind": "symlink",
                            "target": entry.target,
                            "mode": 0o777,
                        }
                    )
                else:
                    body = snapshot.read(entry.path, artifacts)
                    member.size, member.mode = len(body), 0o664
                    archive.addfile(member, io.BytesIO(body))
                    expected.append(
                        {"path": entry.path, "kind": "file", "sha256": entry.sha256, "mode": 0o664}
                    )
        helper = None
        try:
            helper = self.client.containers.run(
                self.container.attrs["Image"],
                ["node", "-e", "setInterval(()=>{},1000)"],
                detach=True,
                network_mode="none",
                user="0:0",
                read_only=True,
                volumes={self.volume: {"bind": "/workspace", "mode": "rw"}},
                tmpfs={"/tmp": "rw,noexec,nosuid,size=16m"},
                environment={},
                labels={"robo-minerador.manual-helper": "true"},
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                mem_limit="128m",
                pids_limit=32,
            )
            staging = parent + "/.upload-" + uuid4().hex
            exists = execute(
                helper,
                """
const fs=require('fs'),p=JSON.parse(process.argv[1]);
let current=''; for(const part of p.parent.split('/').filter(Boolean)){
 current+='/'+part;
 if(fs.existsSync(current)){if(!fs.lstatSync(current).isDirectory())process.exit(1);}
 else fs.mkdirSync(current,{mode:0o755});
}
const exists=fs.existsSync(p.destination);
if(!exists)fs.mkdirSync(p.staging,{mode:0o755});
console.log(JSON.stringify(exists));
""",
                {"parent": parent, "destination": destination, "staging": staging},
            )
            if not exists:
                if not helper.put_archive(staging, stream.getvalue()):
                    raise RuntimeError("snapshot upload failed")
            checked = destination if exists else staging
            execute(
                helper,
                """
const fs=require('fs'),path=require('path'),crypto=require('crypto');
const p=JSON.parse(process.argv[1]), actual=[];
function walk(dir,prefix=''){
 for(const name of fs.readdirSync(dir).sort()){
  const rel=prefix+name,file=path.join(dir,name),s=fs.lstatSync(file);
  const kind=s.isDirectory()?'directory':s.isSymbolicLink()?'symlink':'file';
  let e={path:rel,kind,mode:s.mode&511};
  if(e.kind==='file')e.sha256=crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
  if(e.kind==='symlink')e.target=fs.readlinkSync(file);
  actual.push(e); if(s.isDirectory())walk(file,rel+'/');
 }
}
if(!fs.lstatSync(p.root).isDirectory())process.exit(1);
walk(p.root);
const normalize=v=>JSON.stringify(v.sort((a,b)=>a.path.localeCompare(b.path))
 .map(e=>Object.fromEntries(Object.entries(e).sort())));
if(normalize(actual)!==normalize(p.expected))process.exit(1);
if(p.root!==p.destination)fs.renameSync(p.root,p.destination);
console.log('true');
""",
                {"root": checked, "destination": destination, "expected": expected},
            )
            return destination
        finally:
            if helper is not None:
                helper.remove(force=True)
