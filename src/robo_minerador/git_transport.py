"""Transporta objetos Git sem materializar ou executar arquivos candidatos."""

import base64
import hashlib
import os
from pathlib import Path
import re
import queue
import signal
import subprocess
import threading
import time

from . import config
from .triage_snapshot import SnapshotUnavailable


BARE_CONFIG = b"[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tbare = true\n"


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    class _JobLimits(ctypes.Structure):
        """Representa JOBOBJECT_BASIC_LIMIT_INFORMATION com alinhamento nativo."""

        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class _IoCounters(ctypes.Structure):
        """Representa os seis contadores de 64 bits de IO_COUNTERS."""

        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _ExtendedJobLimits(ctypes.Structure):
        """Representa JOBOBJECT_EXTENDED_LIMIT_INFORMATION sem truncar ponteiros."""

        _fields_ = [("BasicLimitInformation", _JobLimits), ("IoInfo", _IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class _ThreadEntry(ctypes.Structure):
        """Representa THREADENTRY32 usado para encontrar a thread inicial suspensa."""

        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ThreadID", wintypes.DWORD), ("th32OwnerProcessID", wintypes.DWORD),
                    ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD)]

    class _JobAccounting(ctypes.Structure):
        """Representa JOBOBJECT_BASIC_ACCOUNTING_INFORMATION para aguardar saída real."""

        _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                    ("ThisPeriodTotalUserTime", ctypes.c_int64),
                    ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                    ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                    ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]

    _KERNEL = ctypes.WinDLL("kernel32", use_last_error=True)
    for _name, _arguments, _result in (
        ("CreateJobObjectW", [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        ("SetInformationJobObject", [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
        ("AssignProcessToJobObject", [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        ("TerminateJobObject", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        ("QueryInformationJobObject", [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                       wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
        ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
        ("CreateToolhelp32Snapshot", [wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
        ("Thread32First", [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
        ("Thread32Next", [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
        ("OpenThread", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        ("OpenProcess", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        ("WaitForSingleObject", [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
        ("ResumeThread", [wintypes.HANDLE], wintypes.DWORD),
        ("CancelSynchronousIo", [wintypes.HANDLE], wintypes.BOOL),
        ("GetShortPathNameW", [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD], wintypes.DWORD),
    ):
        getattr(_KERNEL, _name).argtypes = _arguments
        getattr(_KERNEL, _name).restype = _result


def _repository_path(repository):
    """Usa alias Win32 existente quando o setup inicial do Git excederia MAX_PATH.

    core.longpaths cobre objetos internos, mas não todo o setup inicial do Git
    2.53 para Windows. O alias 8.3 não move nem duplica o cache. Em volumes sem
    aliases curtos disponíveis, recusa explicitamente e pede uma raiz menor.
    """
    path = str(Path(repository).absolute())
    if os.name == "nt":
        size = _KERNEL.GetShortPathNameW(path, None, 0)
        if size:
            buffer = ctypes.create_unicode_buffer(size)
            length = _KERNEL.GetShortPathNameW(path, buffer, size)
            if 0 < length < size and len(buffer.value) < 180:
                return buffer.value
        if len(path) >= 180:
            raise SnapshotUnavailable("SNAPSHOT_PATH_LIMIT", "Git requer uma raiz de cache mais curta neste volume.")
    return path


class _WindowsJob:
    """Contém descendentes antes da primeira instrução do processo no Windows.

    Usa somente APIs Win32 documentadas. Popen fecha o handle da thread inicial;
    Toolhelp recupera essa thread enquanto CREATE_SUSPENDED impede qualquer
    execução. O handle de processo de Popen é específico do CPython Windows.
    """

    def __init__(self):
        """Cria job não herdável com JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE."""
        self.handle = _KERNEL.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedJobLimits()
        limits.BasicLimitInformation.LimitFlags = 0x00002000
        if not _KERNEL.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def attach_and_resume(self, process):
        """Associa antes de retomar a thread, eliminando a janela de fuga de filhos."""
        if not _KERNEL.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())
        snapshot = _KERNEL.CreateToolhelp32Snapshot(0x00000004, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = _ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            found = _KERNEL.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.th32OwnerProcessID == process.pid:
                    thread = _KERNEL.OpenThread(0x0002, False, entry.th32ThreadID)
                    if not thread:
                        raise ctypes.WinError(ctypes.get_last_error())
                    try:
                        if _KERNEL.ResumeThread(thread) == 0xFFFFFFFF:
                            raise ctypes.WinError(ctypes.get_last_error())
                        return
                    finally:
                        _KERNEL.CloseHandle(thread)
                found = _KERNEL.Thread32Next(snapshot, ctypes.byref(entry))
            raise OSError("Thread inicial suspensa não encontrada.")
        finally:
            _KERNEL.CloseHandle(snapshot)

    def close(self):
        """Fecha o último handle do job e mata filhos mesmo após a saída do pai."""
        if self.handle is not None:
            _KERNEL.CloseHandle(self.handle)
            self.handle = None

    def terminate(self):
        """Termina o job e espera até dois segundos pela saída dos descendentes.

        TerminateJobObject é assíncrono; somente fechar pipes ou observar a
        saída do pai não comprova que os filhos já terminaram.
        """
        handles = self._process_handles()
        deadline = time.monotonic() + 2
        try:
            if not _KERNEL.TerminateJobObject(self.handle, 1):
                raise ctypes.WinError(ctypes.get_last_error())
            # ActiveProcesses pode chegar a zero antes de o handle de processo
            # ser sinalizado; os handles fixados comprovam a saída efetiva.
            for handle in handles:
                remaining = max(0, int((deadline - time.monotonic()) * 1000))
                if _KERNEL.WaitForSingleObject(handle, remaining) != 0:
                    raise SnapshotUnavailable("SNAPSHOT_CLEANUP_TIMEOUT", "Windows não confirmou a saída do descendente.")
            accounting = _JobAccounting()
            while True:
                if not _KERNEL.QueryInformationJobObject(self.handle, 1, ctypes.byref(accounting),
                                                          ctypes.sizeof(accounting), None):
                    raise ctypes.WinError(ctypes.get_last_error())
                if accounting.ActiveProcesses == 0:
                    return
                if time.monotonic() >= deadline:
                    raise SnapshotUnavailable("SNAPSHOT_CLEANUP_TIMEOUT", "Windows não confirmou o encerramento do job.")
                time.sleep(0.001)
        finally:
            for handle in handles:
                _KERNEL.CloseHandle(handle)

    def _process_handles(self):
        """Fixa handles sincronizáveis dos processos ainda associados ao job."""
        capacity = 16
        while True:
            buffer = ctypes.create_string_buffer(8 + capacity * ctypes.sizeof(ctypes.c_size_t))
            valid = _KERNEL.QueryInformationJobObject(self.handle, 3, buffer, len(buffer), None)
            if valid:
                break
            error = ctypes.get_last_error()
            if error != 234:
                raise ctypes.WinError(error)
            capacity = max(capacity * 2, wintypes.DWORD.from_buffer(buffer).value)
        count = wintypes.DWORD.from_buffer(buffer, 4).value
        ids = (ctypes.c_size_t * count).from_buffer(buffer, 8)
        handles = []
        try:
            for pid in ids:
                handle = _KERNEL.OpenProcess(0x00100000, False, pid)
                if handle:
                    handles.append(handle)
                elif ctypes.get_last_error() != 87:
                    raise ctypes.WinError(ctypes.get_last_error())
            return handles
        except OSError:
            for handle in handles:
                _KERNEL.CloseHandle(handle)
            raise

    @staticmethod
    def cancel_io(threads):
        """Desbloqueia I/O síncrono dos pumps após encerrar todo o job."""
        for worker in threads:
            if worker.is_alive():
                handle = _KERNEL.OpenThread(0x0001, False, worker.native_id)
                if handle:
                    try:
                        _KERNEL.CancelSynchronousIo(handle)
                    finally:
                        _KERNEL.CloseHandle(handle)


class GitTransport:
    """Executa Git isolado com limites compartilhados por aquisição."""

    def __init__(self, *, max_entries=config.SNAPSHOT_MAX_ENTRIES,
                 max_blob_bytes=config.SNAPSHOT_MAX_BLOB_BYTES,
                 max_disk_bytes=config.SNAPSHOT_MAX_DISK_BYTES,
                 timeout=config.SNAPSHOT_TIMEOUT_SECONDS):
        """Define os orçamentos sem modificar os limites da triagem."""
        self.max_entries = max_entries
        self.max_blob_bytes = max_blob_bytes
        self.max_disk_bytes = max_disk_bytes
        self.timeout = timeout
        self._cancelled = threading.Event()

    def cancel(self):
        """Interrompe operações ativas e futuras nas próximas verificações."""
        self._cancelled.set()

    def check(self, cancel=None, deadline=None, disk_root=None):
        """Verifica cancelamento, prazo e soma dos tamanhos de arquivos no staging.

        A soma usa st_size, inclusive packs comprimidos, e é amostrada pelo
        runner. Não representa quota física rígida: alocação em clusters,
        metadados do filesystem e crescimento entre amostras podem divergir.
        """
        if self._cancelled.is_set() or (cancel is not None and cancel.is_set()):
            raise SnapshotUnavailable("SNAPSHOT_CANCELLED", "Aquisição cancelada.")
        if deadline is not None and time.monotonic() >= deadline:
            raise SnapshotUnavailable("SNAPSHOT_TIMEOUT", "Prazo da aquisição excedido.")
        if disk_root is not None and disk_root.exists():
            total = 0
            for directory, _, files in os.walk(disk_root):
                for name in files:
                    try:
                        total += (Path(directory) / name).lstat().st_size
                    except FileNotFoundError:
                        continue
                    if total > self.max_disk_bytes:
                        raise SnapshotUnavailable("SNAPSHOT_DISK_LIMIT", "Limite de disco excedido.")

    def _environment(self):
        """Elimina configurações herdadas, credenciais e mecanismos interativos."""
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith(("GIT_", "GCM_", "SSH_"))}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_SYSTEM=os.devnull,
                   GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0",
                   GIT_ASKPASS="", GCM_INTERACTIVE="never", GIT_NO_REPLACE_OBJECTS="1",
                   GIT_ATTR_NOSYSTEM="1", GIT_LFS_SKIP_SMUDGE="1", GIT_NO_LAZY_FETCH="1")
        return env

    def run(self, args, *, repository=None, input_data=None, cancel=None,
            deadline=None, disk_root=None, consume=None, output_limit=1024 * 1024):
        """Drena stdout incrementalmente com fila limitada e descarta stderr sanitizado.

        O consumidor pode abortar assim que conhecer um orçamento excedido. A fila
        retém no máximo quatro blocos de 64 KiB; saídas comuns também têm teto.
        """
        deadline = deadline if deadline is not None else time.monotonic() + self.timeout
        self.check(cancel, deadline, disk_root)
        command = ["git", "-c", "credential.helper=", "-c", "core.hooksPath=" + os.devnull,
                   "-c", "init.templateDir=", "-c", "protocol.allow=never",
                   "-c", "protocol.https.allow=always", "-c", "http.followRedirects=false",
                   "-c", "core.attributesFile=" + os.devnull,
                   "-c", "maintenance.auto=false", "-c", "gc.auto=0",
                   "-c", "core.longpaths=true"]
        if repository is not None:
            command.append("--git-dir=" + _repository_path(repository))
        command.extend(args)
        job = None
        process = None
        try:
            if os.name == "nt":
                job = _WindowsJob()
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL, env=self._environment(), shell=False,
                                       start_new_session=os.name != "nt",
                                       creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000004)
                                       if os.name == "nt" else 0)
            if job is not None:
                job.attach_and_resume(process)
        except OSError as error:
            if job is not None:
                job.close()
            if process is not None:
                process.kill()
                process.wait()
                process.stdin.close()
                process.stdout.close()
                process._handle.Close()
            raise SnapshotUnavailable("SNAPSHOT_GIT_UNAVAILABLE", "Git indisponível.") from error
        chunks = queue.Queue(maxsize=4)
        stopped = threading.Event()

        def enqueue(chunk):
            """Aplica contrapressão sem prender a thread durante cancelamento."""
            while not stopped.is_set():
                try:
                    chunks.put(chunk, timeout=0.05)
                    return
                except queue.Full:
                    continue

        def drain():
            """Lê blocos limitados sem esperar EOF para entregar o inventário."""
            try:
                while not stopped.is_set():
                    chunk = process.stdout.read1(65536)
                    if not chunk:
                        break
                    enqueue(chunk)
            except OSError as error:
                enqueue(error)
            finally:
                enqueue(None)

        def feed():
            """Alimenta batch em paralelo para evitar deadlock entre stdin e stdout."""
            try:
                if input_data:
                    process.stdin.write(input_data)
                    process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            finally:
                try:
                    process.stdin.close()
                except OSError:
                    pass

        reader = threading.Thread(target=drain, name="git-stdout")
        writer = threading.Thread(target=feed, name="git-stdin")
        started = []
        output = bytearray()
        try:
            reader.start()
            started.append(reader)
            writer.start()
            started.append(writer)
            while True:
                self.check(cancel, deadline, disk_root)
                try:
                    chunk = chunks.get(timeout=0.05)
                except queue.Empty:
                    continue
                if chunk is None:
                    break
                if isinstance(chunk, Exception):
                    raise SnapshotUnavailable("SNAPSHOT_GIT_FAILED", "Falha na leitura de objetos.") from chunk
                if consume is not None:
                    consume(chunk)
                else:
                    if len(output) + len(chunk) > output_limit:
                        raise SnapshotUnavailable("SNAPSHOT_OUTPUT_LIMIT", "Saída Git excede o limite de captura.")
                    output.extend(chunk)
            while process.poll() is None:
                self.check(cancel, deadline, disk_root)
                stopped.wait(0.01)
            self.check(cancel, deadline, disk_root)
            if process.returncode:
                raise SnapshotUnavailable("SNAPSHOT_GIT_FAILED", "Git recusou a operação de objetos.")
            return bytes(output)
        finally:
            stopped.set()
            try:
                try:
                    if job is not None:
                        try:
                            job.terminate()
                        finally:
                            job.close()
                            job.cancel_io(started)
                    else:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait()
            finally:
                for worker in started:
                    worker.join()
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
                if job is not None:
                    process._handle.Close()

    def preflight(self):
        """Confirma disponibilidade do executável sem usar rede."""
        return self.run(["--version"]).decode("ascii").strip()

    def acquire(self, repository, url, sha, **options):
        """Inicializa bare e busca apenas o SHA público validado."""
        if (not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git", url)
                or url.split("/")[3] in {".", ".."} or url.split("/")[4][:-4] in {".", ".."}):
            raise SnapshotUnavailable("SNAPSHOT_INVALID_URL", "URL pública inválida.")
        repository.mkdir()
        self.run(["init", "--bare", "--template="], repository=repository, **options)
        (repository / "config").write_bytes(BARE_CONFIG)
        self._fetch(repository, url, sha, **options)

    def _fetch(self, repository, url, sha, **options):
        """Busca objetos sem tags, submódulos, filtros ou fallback de branch."""
        self.run(["fetch", "--depth=1", "--no-tags", "--no-recurse-submodules",
                  "--", url, sha + ":refs/minerador/snapshot"], repository=repository, **options)

    def _check_storage(self, repository, **options):
        """Recusa configurações e referências externas antes de invocar Git no cache."""
        if (repository / "config").read_bytes() != BARE_CONFIG:
            raise SnapshotUnavailable("SNAPSHOT_CORRUPT", "Configuração bare divergente.")
        for path in repository.rglob("*"):
            self.check(options.get("cancel"), options.get("deadline"))
            if path.is_symlink() or path.name in {"alternates", "http-alternates", "grafts"}:
                raise SnapshotUnavailable("SNAPSHOT_CORRUPT", "Referência externa de objetos recusada.")

    def inventory(self, repository, sha, **options):
        """Confere objetos e limita entradas/bytes assim que o stream os revela.

        Disco e tempo são monitorados no fetch; o tamanho expandido dos blobs
        só é conhecido neste inventário posterior, antes de publicar o cache.
        """
        self._check_storage(repository, **options)
        actual = self.run(["rev-parse", "refs/minerador/snapshot^{commit}"],
                          repository=repository, **options).decode("ascii").strip()
        if actual != sha:
            raise SnapshotUnavailable("SNAPSHOT_SHA_MISMATCH", "Commit recebido diverge do SHA fixado.")
        ref = self.run(["rev-parse", "--verify", "refs/minerador/snapshot"],
                       repository=repository, **options).decode("ascii").strip()
        if ref != sha:
            raise SnapshotUnavailable("SNAPSHOT_SHA_MISMATCH", "Referência não aponta diretamente ao commit fixado.")
        tree = self.run(["rev-parse", "refs/minerador/snapshot^{tree}"],
                        repository=repository, **options).decode("ascii").strip()
        self.run(["fsck", "--full", "--strict", "--no-reflogs"], repository=repository, **options)
        entries = []
        total = 0
        pending = bytearray()

        def consume(chunk):
            """Valida cada registro NUL antes de reter a próxima entrada da árvore."""
            nonlocal total
            pending.extend(chunk)
            while b"\0" in pending:
                self.check(options.get("cancel"), options.get("deadline"))
                record, _, rest = pending.partition(b"\0")
                pending[:] = rest
                metadata, path = record.split(b"\t", 1)
                mode, kind, oid, size = metadata.split()
                entry = {"mode": mode.decode("ascii"), "type": kind.decode("ascii"),
                         "oid": oid.decode("ascii"), "size": None if size == b"-" else int(size)}
                total += entry["size"] or 0
                if len(entries) >= self.max_entries:
                    raise SnapshotUnavailable("SNAPSHOT_ENTRY_LIMIT", "Limite de entradas excedido.")
                if total > self.max_blob_bytes:
                    raise SnapshotUnavailable("SNAPSHOT_BLOB_LIMIT", "Limite acumulado de blobs excedido.")
                try:
                    entry["path"] = path.decode("utf-8")
                except UnicodeDecodeError:
                    entry["path_base64"] = base64.b64encode(path).decode("ascii")
                entries.append((path, entry))
            if len(pending) > 1024 * 1024:
                raise SnapshotUnavailable("SNAPSHOT_OUTPUT_LIMIT", "Registro Git excessivamente longo.")

        self.run(["ls-tree", "-r", "-z", "-l", "refs/minerador/snapshot"],
                 repository=repository, consume=consume, **options)
        if pending:
            raise SnapshotUnavailable("SNAPSHOT_CORRUPT", "Inventário Git truncado.")
        small = {entry["oid"]: entry for _, entry in entries
                 if entry["mode"] in {"100644", "100755"} and entry["size"] <= 1024}
        contents = self.read_blobs(repository, list(small.values()), **options) if small else []
        pointers = {}
        for entry, content in zip(small.values(), contents):
            pointer = re.fullmatch(
                rb"version https://git-lfs.github.com/spec/v1\r?\n"
                rb"(?:ext-[0-9]+-[a-z0-9.-]+ [^\r\n]+\r?\n)*"
                rb"oid sha256:([0-9a-f]{64})\r?\nsize (0|[1-9][0-9]*)\r?\n?", content)
            if pointer:
                pointers[entry["oid"]] = {"oid": "sha256:" + pointer[1].decode("ascii"),
                                          "size": int(pointer[2])}
        for _, entry in entries:
            if entry["mode"] in {"100644", "100755"} and entry["oid"] in pointers:
                entry["lfs_pointer"] = pointers[entry["oid"]]
        entries.sort(key=self._entry_path)
        return tree, [entry for _, entry in entries], total

    @staticmethod
    def _entry_path(item):
        """Ordena sem normalizar caixa ou Unicode dos caminhos Git."""
        return item[0]

    def read_blob(self, repository, oid, **options):
        """Obtém tamanho e lê um blob pelo mesmo batch limitado e verificado."""
        self._check_storage(repository, **options)
        if not re.fullmatch(r"[0-9a-f]{40}", oid):
            raise SnapshotUnavailable("SNAPSHOT_CORRUPT", "OID de blob inválido.")
        size = int(self.run(["cat-file", "-s", oid], repository=repository, **options))
        return self.read_blobs(repository, [{"oid": oid, "size": size}], **options)[0]

    def read_blobs(self, repository, entries, **options):
        """Lê lote em um cat-file, conferindo cabeçalho, tamanho e hash de cada blob."""
        self._check_storage(repository, **options)
        if (len(entries) > self.max_entries
                or sum(entry["size"] for entry in entries) > self.max_blob_bytes):
            raise SnapshotUnavailable("SNAPSHOT_BLOB_LIMIT", "Lote excede o orçamento de leitura.")
        if not entries:
            return []
        pending = bytearray()
        results = []
        header_checked = False

        def consume(chunk):
            """Decodifica frames incrementais usando somente tamanhos já fixados."""
            nonlocal header_checked
            pending.extend(chunk)
            while len(results) < len(entries):
                entry = entries[len(results)]
                if not header_checked:
                    if b"\n" not in pending:
                        if len(pending) > 128:
                            raise SnapshotUnavailable("SNAPSHOT_CORRUPT", "Cabeçalho de blob excessivo.")
                        return
                    header, _, rest = pending.partition(b"\n")
                    pending[:] = rest
                    expected = f"{entry['oid']} blob {entry['size']}".encode("ascii")
                    if header != expected:
                        raise SnapshotUnavailable("SNAPSHOT_CORRUPT", "Cabeçalho diverge do blob fixado.")
                    header_checked = True
                size = entry["size"]
                if len(pending) < size + 1:
                    return
                content = bytes(pending[:size])
                if pending[size] != 10:
                    raise SnapshotUnavailable("SNAPSHOT_CORRUPT", "Frame de blob inválido.")
                del pending[:size + 1]
                digest = hashlib.sha1(f"blob {size}\0".encode("ascii") + content).hexdigest()
                if digest != entry["oid"]:
                    raise SnapshotUnavailable("SNAPSHOT_CORRUPT", "Hash diverge do blob fixado.")
                results.append(content)
                header_checked = False
            if pending:
                raise SnapshotUnavailable("SNAPSHOT_CORRUPT", "Resposta batch contém dados excedentes.")

        self.run(["cat-file", "--batch"], repository=repository, consume=consume,
                 input_data=b"".join((entry["oid"] + "\n").encode("ascii") for entry in entries),
                 **options)
        if len(results) != len(entries) or pending:
            raise SnapshotUnavailable("SNAPSHOT_CORRUPT", "Resposta batch incompleta.")
        return results
