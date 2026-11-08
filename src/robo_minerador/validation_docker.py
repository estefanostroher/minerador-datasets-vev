"""Executor Docker CLI com volumes exclusivos e evidências brutas incorporáveis.

Nenhum resultado de processo é classificado como decisão científica neste módulo.
Logs binários completos permanecem em spools até close(); o coordenador deve
incorporá-los antes disso. Falhas de captura são explícitas em capture_loss.
"""

from datetime import datetime, timezone
import json
import base64
import hashlib
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import uuid

from .config import validate_validation_limits
from .validation_recipe import PHASES, _hash, _validate_recipe
from .validation_workspace import ValidationWorkspace


LABEL = "org.robo-minerador.validation.run"
PATH = "/tools/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Código exclusivamente do minerador, executado antes de qualquer comando candidato.
VERIFY = r"""
const fs=require('node:fs'), crypto=require('node:crypto'), path=require('node:path');
const entries=JSON.parse(fs.readFileSync('/tmp/manifest.json','utf8'));
const limits=JSON.parse(process.argv[1]),read=p=>fs.readFileSync(p,'utf8').trim();
let cpu,memory,swap;
if(fs.existsSync('/sys/fs/cgroup/cgroup.controllers')){
 const v=read('/sys/fs/cgroup/cpu.max').split(' ').map(Number);cpu=v[0]/v[1];
 memory=Number(read('/sys/fs/cgroup/memory.max'));swap=Number(read('/sys/fs/cgroup/memory.swap.max'));
}else{
 cpu=Number(read('/sys/fs/cgroup/cpu/cpu.cfs_quota_us'))/Number(read('/sys/fs/cgroup/cpu/cpu.cfs_period_us'));
 memory=Number(read('/sys/fs/cgroup/memory/memory.limit_in_bytes'));
 swap=Number(read('/sys/fs/cgroup/memory/memory.memsw.limit_in_bytes'))-memory;
}
if(cpu!==limits.cpus||memory!==limits.memory_bytes||swap!==0)throw Error('effective cgroup limits');
for(const e of entries){
 const p='/workspace/'+e.path,s=fs.lstatSync(p);
 const b=e.mode==='120000'?Buffer.from(fs.readlinkSync(p)):fs.readFileSync(p);
 if(crypto.createHash('sha256').update(b).digest('hex')!==e.sha256)throw Error('bytes: '+e.path);
 if(crypto.createHash('sha1').update(Buffer.concat([Buffer.from('blob '+b.length+'\0'),b])).digest('hex')!==e.oid)throw Error('blob: '+e.path);
 if(e.mode==='120000'){
  if(!s.isSymbolicLink())throw Error('link: '+e.path);
  const target=fs.realpathSync(p);
  if(!target.startsWith('/workspace/')||target.startsWith('/workspace/.git/'))throw Error('escape: '+e.path);
 }else if(!s.isFile()||(s.mode&511)!==(e.mode==='100755'?493:420))throw Error('mode: '+e.path);
}
console.log(JSON.stringify({verified_files:entries.length,platform:process.platform,arch:process.arch,
 effective_limits:{cpus:cpu,memory_bytes:memory,extra_swap_bytes:swap}}));
"""

BOOTSTRAP = r"""
const fs=require('node:fs'),crypto=require('node:crypto'),cp=require('node:child_process');
(async()=>{const s=JSON.parse(process.argv[1]);
 const r=await fetch(s.tarball);if(!r.ok)throw Error('registry HTTP '+r.status);
 const b=Buffer.from(await r.arrayBuffer());
 if('sha512-'+crypto.createHash('sha512').update(b).digest('base64')!==s.integrity)throw Error('integrity');
 fs.writeFileSync('/tmp/pnpm.tgz',b);
 cp.execFileSync('npm',['install','--global','--ignore-scripts','--offline','--no-audit','--no-fund','--prefix','/tools','/tmp/pnpm.tgz'],{stdio:'inherit'});
})();
"""

DEPENDENCIES = r"""
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const seen=new Set(),packages=[],installedLocks=[];
// Percorre manifests como dados, deduplicando links sem importar código instalado.
function visit(p){
 const real=fs.realpathSync(p);if(seen.has(real))return;seen.add(real);
 if(real!=='/workspace'&&!real.startsWith('/workspace/'))throw Error('dependency escapes workspace');
 for(const item of fs.readdirSync(p,{withFileTypes:true})){
  if(item.name==='.git')continue;const child=path.join(p,item.name);
  if(item.isDirectory()||item.isSymbolicLink()){
   const stat=fs.statSync(child);if(stat.isDirectory())visit(child);
  }else if(item.name==='.package-lock.json'&&p.includes('/node_modules')){
   const raw=fs.readFileSync(child),v=JSON.parse(raw);
   installedLocks.push({path:child.slice(11),sha256:crypto.createHash('sha256').update(raw).digest('hex'),
    packages:v.packages??v.dependencies??{}});
  }else if(item.name==='package.json'&&p.includes('/node_modules/')){
   const raw=fs.readFileSync(child),v=JSON.parse(raw);
   packages.push({path:child.slice(11),name:v.name,version:v.version,integrity:v._integrity??null,
    resolved:v._resolved??null,manifest_sha256:crypto.createHash('sha256').update(raw).digest('hex')});
  }
 }
}
visit('/workspace');packages.sort((a,b)=>a.path.localeCompare(b.path));
console.log(JSON.stringify({packages,...(installedLocks.length?{installed_lockfiles:installedLocks}:{})}));
"""


def _utc():
    """Registra instantes UTC independentes do relógio usado para deadlines."""
    return datetime.now(timezone.utc).isoformat()


class DockerInfrastructureError(RuntimeError):
    """Sinaliza falha operacional sem atribuir culpa ao candidato."""


class DockerExecutor:
    """Possui somente recursos rotulados desta instância e não executa suítes implicitamente."""

    def __init__(self, *, docker="docker", clock=time.monotonic):
        """Inicializa ownership, cancelamento e spools temporários sem iniciar Docker."""
        self.docker = docker
        self.clock = clock
        self.run_id = uuid.uuid4().hex
        self._cancelled = threading.Event()
        self._containers = set()
        self._volumes = set()
        self._logs = tempfile.TemporaryDirectory(prefix="robo-validation-")
        self.events = []
        self.cleanup_errors = []
        self.daemon = None
        self._closed = False
        self._active = threading.Event()
        self._instrument_resources = []

    def _cli(self, args, *, data=None, timeout=60):
        """Executa administração confiável sem shell e recusa erros Docker explícitos."""
        try:
            result = subprocess.run([self.docker, *args], input=data, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, shell=False, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DockerInfrastructureError("Docker indisponível ou prazo administrativo excedido.") from error
        if result.returncode:
            raise DockerInfrastructureError(result.stderr.decode("utf-8", errors="replace"))
        return result.stdout

    def preflight(self):
        """Exige daemon Linux com CPU/memória/swap limitáveis, sem abrir candidatos."""
        info = json.loads(self._cli(["info", "--format", "{{json .}} "]))
        if info.get("OSType") != "linux" or not all(info.get(key) for key in (
                "MemoryLimit", "SwapLimit", "CpuCfsQuota", "CpuCfsPeriod")):
            raise DockerInfrastructureError("Daemon deve ser Linux com limites efetivos de CPU, memória e swap.")
        self.daemon = {key: info.get(key) for key in (
            "ID", "ServerVersion", "OSType", "Architecture", "KernelVersion", "CgroupVersion", "NCPU", "MemTotal")}
        return dict(self.daemon)

    def _volume(self):
        """Reserva volume nomeado exclusivo e rotulado antes de qualquer materialização."""
        name = "robo-validation-" + self.run_id + "-" + uuid.uuid4().hex
        self._volumes.add(name)
        self._cli(["volume", "create", "--label", LABEL + "=" + self.run_id, name])
        return name

    def _create(self, recipe, argv, *, volume=None, network="none", cwd="/", environment=None,
                tool_volume=None, instrument_mount=None, writable_tools=False):
        """Cria container com limites, rede e montagens explícitas, sem entrypoint da imagem."""
        if self._closed or self._cancelled.is_set():
            raise DockerInfrastructureError("Executor encerrado ou cancelado.")
        limits = validate_validation_limits(recipe["limits"])
        name = "robo-validation-" + self.run_id + "-" + uuid.uuid4().hex
        self._containers.add(name)
        args = ["create", "--name", name, "--label", LABEL + "=" + self.run_id,
                "--platform", recipe["platform"], "--cpus", str(limits["cpus"]),
                "--memory", str(limits["memory_bytes"]), "--memory-swap", str(limits["memory_bytes"]),
                "--network", network, "--workdir", cwd, "--init", "--entrypoint", "/usr/bin/env"]
        # Docker pode injetar proxies do cliente automaticamente; limpa também o ambiente do init.
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "FTP_PROXY", "ALL_PROXY", "NO_PROXY",
                    "http_proxy", "https_proxy", "ftp_proxy", "all_proxy", "no_proxy"):
            args += ["--env", key + "="]
        if volume:
            args += ["--mount", "type=volume,src=" + volume + ",dst=/workspace,volume-nocopy"]
        if tool_volume:
            args += ["--mount", "type=volume,src=" + tool_volume + ",dst=/tools,volume-nocopy"
                     + ("" if writable_tools else ",readonly")]
        if instrument_mount:
            source = str(Path(instrument_mount).resolve(strict=True))
            if "," in source:
                raise ValueError("Caminho do instrumento contém separador de montagem.")
            args += ["--mount", "type=bind,src=" + source + ",dst=/instrument,readonly"]
        # env -i elimina também variáveis da imagem; nenhuma variável Windows entra no container.
        args += [recipe["image"], "-i", "PATH=" + PATH, "HOME=/tmp/robo-home",
                 *[key + "=" + value for key, value in sorted((environment or {}).items())], *argv]
        cid = self._cli(args).decode().strip()
        inspected = self._inspect(name)
        host = inspected["HostConfig"]
        if (host["NanoCpus"] != limits["cpus"] * 1_000_000_000
                or host["Memory"] != limits["memory_bytes"] or host["MemorySwap"] != limits["memory_bytes"]
                or host["NetworkMode"] != network):
            self._remove(name)
            raise DockerInfrastructureError("Daemon não aplicou os limites/rede solicitados.")
        return name, cid

    def _inspect(self, name):
        """Obtém estado/OOM/configuração antes da remoção do container próprio."""
        return json.loads(self._cli(["inspect", name]))[0]

    def _remove(self, name):
        """Remove apenas container registrado, preservando erro de limpeza para auditoria."""
        if name in self._containers:
            try:
                self._cli(["rm", "--force", name])
                self._containers.remove(name)
            except DockerInfrastructureError as error:
                self.cleanup_errors.append({"resource": name, "error": str(error)})

    def _execute(self, name, cid, deadline, metadata):
        """Captura streams em disco e recolhe inspect/OOM inclusive após timeout/cancelamento."""
        started = self.clock()
        index = uuid.uuid4().hex
        stdout = Path(self._logs.name) / (index + ".stdout")
        stderr = Path(self._logs.name) / (index + ".stderr")
        event = {**metadata, "container_id": cid, "container_name": name, "start": _utc(),
                 "end": None, "duration_seconds": None, "pid": None, "exitcode": None,
                 "oom": None, "timeout": False, "cancelled": False, "daemon_error": None,
                 "stdout_path": str(stdout), "stderr_path": str(stderr), "capture_loss": None,
                 "inspect": None, "inspect_error": None, "cleanup_error": None}
        process = None
        self._active.set()
        try:
            with stdout.open("wb") as out, stderr.open("wb") as err:
                event["cancelled"] = self._cancelled.is_set()
                event["timeout"] = self.clock() >= deadline
                if event["cancelled"] or event["timeout"]:
                    return event
                process = subprocess.Popen([self.docker, "start", "--attach", name], stdout=out,
                                           stderr=err, stdin=subprocess.DEVNULL, shell=False)
                event["pid"] = process.pid
                while process.poll() is None:
                    event["cancelled"] = self._cancelled.is_set()
                    event["timeout"] = self.clock() >= deadline
                    if event["cancelled"] or event["timeout"]:
                        self._cli(["kill", name])
                        break
                    time.sleep(0.02)
                process.wait(timeout=10)
                event["docker_cli_exitcode"] = process.returncode
            inspected = self._inspect(name)
            event["inspect"] = {key: inspected.get(key) for key in ("Id", "Image", "Platform", "State", "HostConfig", "Mounts", "Config")}
            event["oom"] = inspected["State"]["OOMKilled"]
            event["exitcode"] = inspected["State"]["ExitCode"]
            if process.returncode != event["exitcode"]:
                event["capture_loss"] = "Saída do Docker attach diverge do processo; captura pode estar incompleta."
            if inspected["State"].get("Error"):
                event["daemon_error"] = inspected["State"]["Error"]
        except (OSError, subprocess.TimeoutExpired, DockerInfrastructureError) as error:
            event["daemon_error"] = str(error)
            if isinstance(error, OSError):
                event["capture_loss"] = str(error)
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
            if event["inspect"] is None:
                try:
                    inspected = self._inspect(name)
                    if inspected["State"].get("Running"):
                        self._cli(["kill", name])
                        inspected = self._inspect(name)
                    event["inspect"] = {key: inspected.get(key) for key in (
                        "Id", "Image", "Platform", "State", "HostConfig", "Mounts", "Config")}
                    event["oom"] = inspected["State"]["OOMKilled"]
                    event["exitcode"] = inspected["State"]["ExitCode"]
                except DockerInfrastructureError as error:
                    event["inspect_error"] = str(error)
            self._remove(name)
            if name in self._containers:
                event["cleanup_error"] = "Docker não confirmou a remoção do container próprio."
            event["end"] = _utc()
            event["duration_seconds"] = self.clock() - started
            for key, path in (("stdout", stdout), ("stderr", stderr)):
                try:
                    event[key + "_bytes"] = path.stat().st_size
                except OSError as error:
                    event["capture_loss"] = str(error)
            self.events.append(event)
            self._active.clear()
            if self._cancelled.is_set():
                self._release_resources()
        return event

    def read_log(self, event, stream="stdout"):
        """Devolve bytes integrais incorporáveis antes de close, sem truncamento oculto."""
        if stream not in {"stdout", "stderr"}:
            raise ValueError("Stream desconhecido.")
        if event.get("status") == "not_started":
            return b""
        return Path(event[stream + "_path"]).read_bytes()

    def _trusted(self, recipe, argv, *, volume=None, tool_volume=None, network="none", data=None,
                 manifest=None, writable_tools=False):
        """Executa somente infraestrutura confiável e falha fechado antes do candidato."""
        name, cid = self._create(recipe, argv, volume=volume, tool_volume=tool_volume,
                                 network=network, writable_tools=writable_tools)
        try:
            if data is not None:
                self._cli(["cp", "-", name + ":/workspace"], data=data, timeout=300)
            if manifest is not None:
                # docker cp aceita tar no stdin; manifesto fica fora da árvore candidata.
                import io
                import tarfile
                from .validation_workspace import _add
                buffer = io.BytesIO()
                with tarfile.open(fileobj=buffer, mode="w") as archive:
                    _add(archive, "manifest.json", json.dumps(manifest).encode())
                self._cli(["cp", "-", name + ":/tmp"], data=buffer.getvalue())
            event = self._execute(name, cid, self.clock() + 300, {"kind": "infrastructure", "argv": argv})
            if not self._success(event):
                raise DockerInfrastructureError("Falha de infraestrutura confiável: " + self.read_log(event, "stderr").decode(errors="replace"))
            return event
        finally:
            self._remove(name)

    @staticmethod
    def _success(event):
        """Determina término operacional limpo sem interpretar a causa científica."""
        return (event["exitcode"] == 0 and not any(event.get(key) for key in (
            "oom", "timeout", "cancelled", "daemon_error", "capture_loss", "inspect_error", "cleanup_error")))

    def materialize(self, artifact, recipe, recipe_sha256, manifest, archive):
        """Verifica imagem/ferramentas e bytes no Linux antes de liberar o workspace."""
        if self.daemon is None:
            self.preflight()
        if self._closed or self._cancelled.is_set():
            raise DockerInfrastructureError("Executor encerrado ou cancelado.")
        _validate_recipe(recipe)
        try:
            self._cli(["image", "inspect", recipe["image"]])
        except DockerInfrastructureError:
            self._cli(["pull", "--platform", recipe["platform"], recipe["image"]], timeout=600)
        tools = None
        try:
            if "tool_bootstrap" in recipe:
                tools = self._volume()
                self._trusted(recipe, ["node", "-e", BOOTSTRAP, json.dumps(recipe["tool_bootstrap"])],
                              tool_volume=tools, writable_tools=True, network="bridge")
            versions = {}
            for tool in ("node", "pnpm" if "pnpm" in recipe["tools"] else "npm"):
                event = self._trusted(recipe, [tool, "--version"], tool_volume=tools)
                actual = self.read_log(event).decode().strip().removeprefix("v")
                if actual != recipe["tools"][tool]:
                    raise DockerInfrastructureError("Versão real divergente de " + tool)
                versions[tool] = actual
            base = self._trusted(recipe, ["node", "-e", "console.log(require('node:fs').readFileSync('/etc/os-release','utf8'))"])
            volume = self._volume()
            event = self._trusted(recipe, ["node", "-e", VERIFY, json.dumps(recipe["limits"])],
                                  volume=volume, data=archive, manifest=manifest)
            identity = json.loads(self.read_log(event))
            expected_arch = {"linux/amd64": "x64", "linux/arm64": "arm64"}[recipe["platform"]]
            if identity["platform"] != "linux" or identity["arch"] != expected_arch:
                raise DockerInfrastructureError("Plataforma efetiva diverge da receita.")
            self._trusted(recipe, ["git", "-C", "/workspace", "read-tree", artifact.commit_sha], volume=volume)
            environment = {"daemon": dict(self.daemon), "tools": versions,
                           "image": recipe["image"], "image_id": event["inspect"]["Image"],
                           "platform": recipe["platform"], "os_release": self.read_log(base).decode(),
                           "materialization": identity,
                           "tool_bootstrap": recipe.get("tool_bootstrap")}
            return ValidationWorkspace(volume, self.run_id, artifact.repository_id, artifact.commit_sha,
                                       json.dumps(recipe, sort_keys=True), recipe_sha256, manifest, environment, tools)
        except BaseException:
            self._release_resources()
            raise

    def collect_dependencies(self, workspace):
        """Lê manifests instalados como dados, sem importar configs ou executar pacotes.

        A T5 deve chamar após instalação/preparação antes de publicar environment;
        falha de leitura é operacional e não comprova falha científica candidata.
        Versões e integridades/origens disponíveis são mantidas sem inventar as ausentes.
        """
        if workspace.run_id != self.run_id or workspace.volume not in self._volumes:
            raise ValueError("Workspace não pertence a este executor.")
        event = self._trusted(workspace.recipe(), ["node", "-e", DEPENDENCIES], volume=workspace.volume)
        inventory = json.loads(self.read_log(event))
        workspace.environment["dependencies"] = inventory
        locks = []
        for lock in workspace.recipe()["lockfiles"]:
            content = self.read_workspace_files(workspace, [lock["path"]])[lock["path"]]
            actual = hashlib.sha256(content).hexdigest()
            locks.append({**lock, "effective_sha256": actual, "matches_original": actual == lock["sha256"]})
        workspace.environment["lockfiles"] = locks
        if not all(lock["matches_original"] for lock in locks):
            raise DockerInfrastructureError("Lockfile efetivo diverge dos bytes originais.")
        return inventory

    def observe_resources(self, workspace, verified, recipe, observation):
        """Confere implementações Vitest/Vite e produz provas global/locais sem comandos candidatos.

        A árvore do pacote é comparada pelo produtor aos bytes oficiais fixados no
        catálogo de modelos. Retorna resource_evidence, resource_local_method e
        instance_resource_evidence; fontes ficam somente na primeira tabela.
        Dependências/configs candidatas desconhecidas não são
        liberadas por esse binding do framework. A leitura ocorre após a passagem;
        não instala, importa ou executa pacotes para obter evidência adicional.
        """
        from .validation_resources import produce_resource_audit
        from .validation_resources_local import produce_instance_resource_audits
        installations = observation.get("installations", [])
        vitest_roots = {item["root"] for item in installations}
        vite_roots = {item["vite_root"] for item in installations if item.get("vite_root")}
        roots = sorted(vitest_roots | vite_roots)
        script = r"""
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const sha=b=>crypto.createHash('sha256').update(b).digest('hex'),out=[];
for(const root of JSON.parse(process.argv[1])){
 if(!root.startsWith('/workspace/')||fs.realpathSync(root)!==root)throw Error('resource package root');
 const files=[];
 // Recusa links e inclui todos os bytes do pacote, sem executar sua implementação.
 function visit(directory){
  for(const name of fs.readdirSync(directory)){
   if(name==='node_modules')continue;
   const file=path.join(directory,name),stat=fs.lstatSync(file);
   if(stat.isSymbolicLink())throw Error('resource package link');
   if(stat.isDirectory())visit(file);
   else if(stat.isFile())files.push({path:path.relative(root,file).split(path.sep).join('/'),sha256:sha(fs.readFileSync(file))});
   else throw Error('resource package special file');
  }
 }
 visit(root);files.sort((a,b)=>a.path<b.path?-1:a.path>b.path?1:0);
 const manifest=JSON.parse(fs.readFileSync(path.join(root,'package.json'),'utf8'));
 out.push({root,version:manifest.version,tree_sha256:sha(JSON.stringify(files))});
}console.log(JSON.stringify(out));
"""
        event = self._trusted(recipe, ["node", "-e", script, json.dumps(roots)], volume=workspace.volume)
        inspected = json.loads(self.read_log(event))
        workspace.environment["resource_bindings"] = {"vitest": [item for item in inspected if item["root"] in vitest_roots],
                                                       "vite": [item for item in inspected if item["root"] in vite_roots]}
        # .npmrc não pertence à projeção textual v3, mas pode alterar o launcher.
        # Incorpora bytes originais conferidos, sem reescrever contexto/extração.
        projected = {file["path"] for file in verified["context"]["files"]}
        extra = [entry for entry in workspace.manifest if entry["path"].split("/")[-1] == ".npmrc" and entry["path"] not in projected]
        if extra:
            contents = self.read_workspace_files(workspace, [entry["path"] for entry in extra])
            supplemental = []
            for entry in extra:
                raw = contents[entry["path"]]
                if entry["mode"] not in {"100644", "100755"} or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
                    raise ValueError("Configuração npm efetiva diverge dos bytes originais.")
                supplemental.append({"path": entry["path"], "content": raw.decode("utf-8", errors="strict")})
            verified["resource_sources"] = supplemental
        shared = produce_resource_audit(verified, recipe, observation, workspace.environment)
        return {"resource_evidence": shared, **produce_instance_resource_audits(verified, recipe, observation, workspace.environment, shared)}

    def read_workspace_files(self, workspace, paths):
        """Extrai evidências binárias da T3 sem executar arquivos/configs do candidato.

        Caminhos POSIX relativos são conferidos antes da leitura; links não podem
        redirecionar a coleta para fora do volume. Não há truncamento da captura.
        """
        from .validation_recipe import _path
        if workspace.run_id != self.run_id or workspace.volume not in self._volumes:
            raise ValueError("Workspace não pertence a este executor.")
        paths = list(paths)
        for path in paths:
            _path(path)
        script = r"""
const fs=require('node:fs'),path=require('node:path'),out={};
for(const p of JSON.parse(process.argv[1])){
 const real=fs.realpathSync(path.join('/workspace',p));
 if(!real.startsWith('/workspace/'))throw Error('evidence escapes workspace');
 out[p]=fs.readFileSync(real).toString('base64');
}console.log(JSON.stringify(out));
"""
        event = self._trusted(workspace.recipe(), ["node", "-e", script, json.dumps(list(paths))],
                              volume=workspace.volume)
        return {path: base64.b64decode(content, validate=True)
                for path, content in json.loads(self.read_log(event)).items()}

    def register_instrument_directory(self, directory):
        """Assume limpeza de área temporária confiável montada readonly pela T3."""
        self._instrument_resources.append(directory)

    def write_workspace_files(self, workspace, files):
        """Grava somente arquivos novos no namespace exclusivo da T3, sem fontes originais.

        O payload segue por tar administrativo para /tmp, evitando limite de argv
        do Windows. O programa confiável confere pais reais, links e criação wx.
        """
        from .validation_recipe import _path
        prefix = ".robo-validation-" + workspace.run_id + "/"
        if workspace.run_id != self.run_id or workspace.volume not in self._volumes:
            raise ValueError("Workspace não pertence a este executor.")
        for path, content in files.items():
            _path(path)
            if not path.startswith(prefix) or type(content) is not bytes:
                raise ValueError("Escrita fora do namespace exclusivo do instrumento.")
        payload = {path: base64.b64encode(content).decode() for path, content in files.items()}
        script = r"""
const fs=require('node:fs'),path=require('node:path');
for(const [relative,content] of Object.entries(JSON.parse(fs.readFileSync('/tmp/manifest.json','utf8')))){
 const destination=path.join('/workspace',relative),parts=relative.split('/');
 let parent='/workspace';
 for(const part of parts.slice(0,-1)){
  parent=path.join(parent,part);
  if(!fs.existsSync(parent))fs.mkdirSync(parent);
  if(fs.lstatSync(parent).isSymbolicLink()||!fs.statSync(parent).isDirectory()||fs.realpathSync(parent)!==parent)throw Error('instrument path');
 }
 fs.writeFileSync(destination,Buffer.from(content,'base64'),{flag:'wx'});
}console.log('instrument-files-written');
"""
        return self._trusted(workspace.recipe(), ["node", "-e", script], volume=workspace.volume, manifest=payload)

    def inspect_instrument_installations(self, workspace, paths):
        """Lê manifests e CLI efetivos como dados, sem importar configurações/pacotes."""
        script = r"""
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),{createRequire}=require('node:module');
const result=[];
for(const relative of JSON.parse(process.argv[1])){
 const root=fs.realpathSync(path.join('/workspace',relative));
 if(!root.startsWith('/workspace/'))throw Error('vitest outside workspace');
 const manifest=JSON.parse(fs.readFileSync(path.join(root,'package.json'),'utf8'));
 const resolver=createRequire(path.join(root,'package.json'));
 const vitePath=resolver.resolve('vite/package.json');
 const vite=JSON.parse(fs.readFileSync(vitePath,'utf8'));
 const candidates=fs.readdirSync(path.join(root,'dist/chunks')).filter(function cli(file){return /^cac\..*\.js$/.test(file)});
 const cliPath=candidates.map(function full(file){return path.join(root,'dist/chunks',file)}).filter(function matches(file){return fs.readFileSync(file,'utf8').includes('await startVitest(')});
 if(cliPath.length!==1)throw Error('unsupported CLI API');
 const source=fs.readFileSync(cliPath[0],'utf8');if(source.split('await startVitest(').length!==2)throw Error('ambiguous CLI API');
 const api=fs.readFileSync(path.join(root,'dist/index.js'),'utf8');if(!api.includes('TestRunner'))throw Error('unsupported runner export');
 const coverageProviders=[];
 for(const provider of ['v8','istanbul']){
  try{const p=resolver.resolve('@vitest/coverage-'+provider+'/package.json'),v=JSON.parse(fs.readFileSync(p,'utf8'));
   coverageProviders.push({provider,version:v.version,path:p,compatible:v.version===manifest.version});
  }catch(error){if(error.code!=='MODULE_NOT_FOUND'&&error.code!=='ERR_PACKAGE_PATH_NOT_EXPORTED')throw error;}
 }
 result.push({root,cli_path:cliPath[0],cli_sha256:crypto.createHash('sha256').update(source).digest('hex'),coverage_providers:coverageProviders,
  tools:{node:process.versions.node,vitest:manifest.version,vite:vite.version,typescript:'6.0.3'},
   vite_root:path.dirname(vitePath),vite_manifest_sha256:crypto.createHash('sha256').update(fs.readFileSync(vitePath)).digest('hex'),
  vitest_manifest_sha256:crypto.createHash('sha256').update(fs.readFileSync(path.join(root,'package.json'))).digest('hex')});
}console.log(JSON.stringify(result));
"""
        event = self._trusted(workspace.recipe(), ["node", "-e", script, json.dumps(paths)], volume=workspace.volume)
        return json.loads(self.read_log(event))

    def _not_started(self, metadata, *, timeout=False, error=None):
        """Representa passo não iniciado sem fabricar processo, captura ou timestamps de execução."""
        event = {**metadata, "status": "not_started", "start": None, "end": None, "duration_seconds": 0,
                 "pid": None, "container_id": None, "container_name": None, "exitcode": None,
                 "oom": None, "timeout": timeout, "cancelled": self._cancelled.is_set(),
                 "daemon_error": error, "capture_loss": None, "inspect": None, "inspect_error": None,
                 "cleanup_error": None, "stdout_path": None, "stderr_path": None,
                 "stdout_bytes": 0, "stderr_bytes": 0}
        self.events.append(event)
        return event

    def run_phase(self, workspace, phase, recipe):
        """Executa fase uma vez, com prazo agregado e processos novos por comando.

        Retorna phase/required/status/commands/first_failure. Cada comando contém
        argv original, execution_argv, cwd, environment, limites, rede, timestamps,
        inspect anterior ao rm e caminhos de spools. Fases posteriores à primeira
        falha retornam not_started; não há retry nem classificação científica.
        """
        if workspace.run_id != self.run_id or workspace.volume not in self._volumes:
            raise ValueError("Workspace não pertence a este executor.")
        if _hash(recipe) != workspace.recipe_sha256:
            raise ValueError("Receita foi alterada após congelamento.")
        frozen = workspace.recipe()
        if _hash(frozen) != workspace.recipe_sha256:
            raise ValueError("Receita congelada do workspace foi alterada.")
        if phase not in PHASES or PHASES.index(phase) != workspace.next_phase:
            raise ValueError("Ordem de fases inválida ou tentativa repetida.")
        started = self.clock()
        result = {"phase": phase, "required": bool(frozen["phases"][phase]), "status": "not_started", "start": _utc(),
                  "commands": [], "first_failure": workspace.first_failure,
                  "not_started_commands": list(frozen["phases"][phase])}

        def finish():
            """Fecha duração monotônica da fase inclusive quando nenhum passo pôde iniciar."""
            result.update(end=_utc(), duration_seconds=self.clock() - started)
            return result

        workspace.next_phase += 1
        if workspace.first_failure or self._cancelled.is_set() or self._closed:
            return finish()
        deadline = started + frozen["limits"][phase + "_seconds"]
        network = "none" if phase == "unit_suite" else "bridge"
        for command_index, command in enumerate(frozen["phases"][phase]):
            original = command["argv"]
            argv = list(workspace.bootstrap_argv) + original if phase == "unit_suite" else original
            environment = dict(frozen["environment"])
            if phase == "unit_suite":
                if any(key in {"PATH", "HOME"} or "=" in key or "\0" in key + value
                       for key, value in workspace.instrument_environment.items()):
                    raise ValueError("Ambiente do instrumento inválido.")
                environment.update(workspace.instrument_environment)
                if workspace.bootstrap_argv:
                    environment["ROBO_VALIDATION_COMMAND_INDEX"] = str(command_index)
            metadata = {"kind": "candidate", "phase": phase, "argv": original, "execution_argv": argv,
                        "cwd": command["cwd"], "environment": {"PATH": PATH, "HOME": "/tmp/robo-home", **environment},
                        "limits": dict(frozen["limits"]), "network": network, "image": frozen["image"]}
            try:
                if self.clock() >= deadline:
                    event = self._not_started(metadata, timeout=True)
                else:
                    name, cid = self._create(frozen, argv, volume=workspace.volume, network=network,
                                             cwd="/workspace" + ("" if command["cwd"] == "." else "/" + command["cwd"]),
                                             environment=environment, tool_volume=workspace.tool_volume,
                                             instrument_mount=workspace.instrument_mount if phase == "unit_suite" else None)
                    event = self._execute(name, cid, deadline, metadata)
            except DockerInfrastructureError as error:
                event = self._not_started(metadata, error=str(error))
            result["commands"].append(event)
            result["not_started_commands"] = result["not_started_commands"][1:]
            if not self._success(event):
                workspace.first_failure = event
                result.update(status="failed", first_failure=event)
                return finish()
        result["status"] = "completed"
        return finish()

    def cancel(self):
        """Sinaliza cancelamento e mata containers próprios; close remove volumes após coleta."""
        self._cancelled.set()
        for name in list(self._containers):
            try:
                self._cli(["kill", name])
            except DockerInfrastructureError as error:
                self.cleanup_errors.append({"resource": name, "error": str(error)})
        if not self._active.is_set():
            self._release_resources()

    def _release_resources(self):
        """Recolhe containers/volumes próprios mantendo logs disponíveis para publicação."""
        for name in list(self._containers):
            self._remove(name)
        for name in list(self._volumes):
            try:
                self._cli(["volume", "rm", name])
                self._volumes.remove(name)
            except DockerInfrastructureError as error:
                self.cleanup_errors.append({"resource": name, "error": str(error)})

    def close(self):
        """Remove somente recursos próprios e spools; erros ficam em cleanup_errors."""
        self._closed = True
        self._release_resources()
        self._logs.cleanup()
        for directory in self._instrument_resources:
            directory.cleanup()
        self._instrument_resources.clear()
