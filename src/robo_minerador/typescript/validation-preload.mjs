import fs from 'node:fs';
import { registerHooks, syncBuiltinESMExports } from 'node:module';
import { threadId, isMainThread } from 'node:worker_threads';
import { randomUUID, createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import childProcess from 'node:child_process';
import { observeNativeResources } from './validation-resource-runtime.mjs';
import path from 'node:path';
import { settings, writeRecord } from './validation-channel.mjs';

const config = settings();
const processId = `${process.pid}:${threadId}:${randomUUID()}`;
const processRecord = { id: processId, pid: process.pid, parent_pid: process.ppid, thread_id: threadId,
  logical_parent: process.env.ROBO_VALIDATION_PROCESS_PARENT ?? null, start_ns: process.hrtime.bigint().toString(),
  main_thread: isMainThread, argv: process.argv, suite_id: process.env.ROBO_VALIDATION_SUITE_ID ?? null,
  started: true, closed: false, exitcode: null };
writeRecord('process', processId, processRecord);
process.env.ROBO_VALIDATION_PROCESS_PARENT = processId;
process.once('exit', /** Registra fechamento real mesmo quando o comando termina não zero. */
  function onExit(code) { writeRecord('process', processId, { ...processRecord, closed: true, exitcode: code }); });

let childOrdinal = 0;
const originalSpawn = childProcess.ChildProcess.prototype.spawn;
/** Observa a API comum de spawn sem mudar argv, opções, retorno ou eventos originais. */
function observedSpawn(options) {
  const id = processId + ':' + ++childOrdinal;
  const value = { id, parent: processId, file: options.file, argv: options.args,
    preload_inherited: options.envPairs?.some(/** Confere propagação explícita do preload. */
      function inherited(pair) { return pair.startsWith('NODE_OPTIONS=') && pair.includes('/instrument/validation-preload.mjs'); }) ?? false,
    pid: null, closed: false, exitcode: null, signal: null };
  const result = Reflect.apply(originalSpawn, this, [options]);
  value.pid = this.pid ?? null;
  writeRecord('child', id, value);
  this.once('close', /** Preserva exit/signal sem inferir sucesso científico. */
    function closed(code, signal) { writeRecord('child', id, { ...value, closed: true, exitcode: code, signal }); });
  return result;
}
childProcess.ChildProcess.prototype.spawn = observedSpawn;

/** Resolve o executável efetivo como dados, sem executar wrappers para reconhecê-los. */
function resolveExecutable(command, options) {
  const cwd = options.cwd ? path.resolve(String(options.cwd)) : process.cwd();
  const environment = options.env ?? process.env;
  const candidates = command.includes('/') ? [path.resolve(cwd, command)]
    : String(environment.PATH ?? '/usr/bin:/bin').split(':').map(/** Reproduz busca POSIX sem aceitar nome como identidade. */
      function candidate(directory) { return path.resolve(cwd, directory, command); });
  for (const candidate of candidates) {
    try {
      fs.accessSync(candidate, fs.constants.X_OK);
      if (fs.statSync(candidate).isFile()) return fs.realpathSync(candidate);
    } catch { /* Executável não resolvido permanece uma limitação de observação. */ }
  }
  return null;
}

/** Só reconhece Node direto ou shebang Node simples; nativos/delegações desconhecidos ficam opacos. */
function synchronousLauncher(command, options, implicitShell) {
  const executable = resolveExecutable(command, options);
  const shells = new Set(['sh', 'bash', 'dash', 'ash', 'zsh', 'ksh', 'fish', 'csh', 'tcsh', 'busybox', 'pwsh', 'powershell', 'cmd']);
  const opaqueShell = implicitShell || !!options.shell || shells.has(path.basename(command))
    || executable !== null && shells.has(path.basename(executable));
  const directNode = executable !== null && executable === fs.realpathSync(process.execPath);
  let nodeScript = false;
  if (executable && !opaqueShell && !directNode) {
    let descriptor;
    try {
      descriptor = fs.openSync(executable, 'r');
      const head = Buffer.alloc(256);
      const length = fs.readSync(descriptor, head, 0, head.length, 0);
      const shebang = head.subarray(0, length).toString('utf8').split(/\r?\n/, 1)[0];
      nodeScript = /^#!\s*\/usr\/bin\/env\s+node\s*$/.test(shebang);
      if (!nodeScript && /^#!\s*\/\S+\s*$/.test(shebang)) {
        nodeScript = fs.realpathSync(shebang.slice(2).trim()) === fs.realpathSync(process.execPath);
      }
    } catch { /* Não supõe ausência de descendentes quando não consegue conferir o launcher. */ }
    finally { if (descriptor !== undefined) fs.closeSync(descriptor); }
  }
  return { executable, direct_node: directNode, node_launcher: directNode || nodeScript,
    opaque_shell: opaqueShell, delegation_observable: !opaqueShell && (directNode || nodeScript) };
}

for (const name of ['spawnSync', 'execFileSync', 'execSync']) {
  const original = childProcess[name];
  /** Observa execução síncrona sem reexecutar nem normalizar seus argumentos. */
  function observedSync(...args) {
    const id = processId + ':' + ++childOrdinal;
    const last = args.at(-1);
    const options = last && typeof last === 'object' && !Array.isArray(last) ? last : {};
    const command = String(args[0]);
    const argv = Array.isArray(args[1]) ? args[1].map(/** Mantém somente argv textual, sem serializar opções possivelmente circulares. */
      function argument(value) { return String(value); }) : [];
    const launcher = synchronousLauncher(command, options, name === 'execSync');
    const value = { id, parent: processId, file: command, argv, closed: true, ...launcher,
      start_ns: process.hrtime.bigint().toString(),
      preload_inherited: String((options.env ?? process.env).NODE_OPTIONS ?? '').includes('/instrument/validation-preload.mjs')
        && !/env\s+-i|NODE_OPTIONS\s*=/.test(command),
      code_free: launcher.direct_node && ['--version', '-v', '--help', '--check'].includes(argv[0]) };
    try {
      const result = Reflect.apply(original, this, args);
      writeRecord('sync-child', id, { ...value, end_ns: process.hrtime.bigint().toString(),
        status: name === 'spawnSync' ? result.status : 0, error: result?.error?.message ?? null });
      return result;
    } catch (error) {
      writeRecord('sync-child', id, { ...value, end_ns: process.hrtime.bigint().toString(), status: error.status ?? null });
      throw error;
    }
  }
  childProcess[name] = observedSync;
}
observeNativeResources(config.workspace_root,
  /** Liga o evento de recurso ao canal da mesma passagem, sem usar porta como classificação. */
  function resourceEffect(value) { writeRecord('resource-effect', randomUUID(), { ...value, suite_id: process.env.ROBO_VALIDATION_SUITE_ID ?? null }); });
syncBuiltinESMExports();

registerHooks({
  /** Intercepta exclusivamente o módulo CLI Vitest cujo hash foi negociado antes da suíte. */
  load(url, context, nextLoad) {
    const result = nextLoad(url, context);
    if (!url.startsWith('file:')) return result;
    const file = fileURLToPath(url);
    const relative = path.relative(config.workspace_root, file).replaceAll('\\', '/');
    if (!relative.startsWith('../') && !path.isAbsolute(relative) && !relative.startsWith('.robo-validation-')
        && !relative.split('/').includes('node_modules')) {
      const raw = fs.readFileSync(file);
      const sha256 = createHash('sha256').update(raw).digest('hex');
      writeRecord('resource-module', processId + ':' + relative + ':' + sha256,
        { path: relative, sha256, origin: 'node_loader', suite_id: process.env.ROBO_VALIDATION_SUITE_ID ?? null });
    }
    const installation = config.installations.find(/** Usa caminho real efetivo do pacote, não nome semelhante. */
      function matches(value) { return value.cli_path === file; });
    if (!installation) return result;
    const source = typeof result.source === 'string' ? result.source : Buffer.from(result.source).toString('utf8');
    if (createHash('sha256').update(source).digest('hex') !== installation.cli_sha256
        || source.split('await startVitest(').length !== 2) throw new Error('CLI Vitest diverge da negociação.');
    return { ...result, source: `import {instrumentStart as __roboStart} from 'file:///instrument/validation-bootstrap.mjs';\n`
      + source.replace('await startVitest(', 'await __roboStart(startVitest,import.meta.url,') };
  },
});
