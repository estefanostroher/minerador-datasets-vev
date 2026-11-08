import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { randomUUID, createHash } from 'node:crypto';
import { createRuntime } from './validation-runtime.mjs';
import { settings } from './validation-channel.mjs';

const config = settings();
for (const [relative, hash] of Object.entries(config.manifest)) {
  if (createHash('sha256').update(fs.readFileSync(path.join('/instrument', relative))).digest('hex') !== hash) {
    throw new Error('Bytes do pacote confiável divergem do manifesto.');
  }
}
const ordinal = process.env.ROBO_VALIDATION_COMMAND_INDEX;
if (!/^\d+$/.test(ordinal ?? '')) throw new Error('Índice de comando ausente no executor confiável.');
const directory = path.join(config.output_root, 'command-' + ordinal);
fs.mkdirSync(directory, { recursive: false });
process.env.ROBO_VALIDATION_CHANNEL = directory;
const argv = process.argv.slice(2);
if (!argv.length) throw new Error('Comando original ausente.');
const events = [];
/** Retém eventos do coordenador próprio, sem observar somente emissores presentes. */
function write(event) { events.push(event); }
const coordinatorId = 'command:' + randomUUID();
const runtime = createRuntime({ ...config.identity, emitter_id: coordinatorId }, write);
runtime.start('coordinator', { node: process.versions.node });
const environment = { ...process.env,
  NODE_OPTIONS: (process.env.NODE_OPTIONS ? process.env.NODE_OPTIONS + ' ' : '') + '--import=file:///instrument/validation-preload.mjs' };
const child = spawn(argv[0], argv.slice(1), { env: environment, stdio: 'inherit' });
let spawnError = null;
child.once('error', /** Preserva falha de lançamento como operacional. */
  function error(value) { spawnError = value.message; });
const exit = await new Promise(/** Aguarda fechamento real do comando original e de seus pipes. */
  function wait(resolve) { child.once('close', /** Registra código e sinal sem coerção para sucesso. */
    function closed(code, signal) { resolve({ code, signal }); }); });

const limitations = [], suites = [], processes = [], workers = [], children = [], synchronousChildren = [], diagnostics = [], transforms = [];
const declarations = { ...config.plan.declarations }, emitters = { [coordinatorId]: 'coordinator' };
const resourceModules = [];
const resourceEffects = [];
const workerEvents = [];
/** Confere término pelo kernel quando o Vitest encerra worker por sinal sem evento JS exit. */
function processEnded(pid) {
  if (!pid) return false;
  try {
    const stat = fs.readFileSync('/proc/' + pid + '/stat', 'utf8');
    return stat.slice(stat.lastIndexOf(')') + 2).split(' ')[0] === 'Z';
  } catch (error) { return error.code === 'ENOENT'; }
}
for (const file of fs.readdirSync(directory)) {
  try {
    const text = fs.readFileSync(path.join(directory, file), 'utf8');
    if (file.endsWith('.jsonl')) {
      workerEvents.push(...text.trim().split('\n').map(/** Decodifica stream integral antes de conferir censo. */
        function parse(line) { return JSON.parse(line); }));
    } else if (file.endsWith('.json')) {
      const value = JSON.parse(text);
      if (file.startsWith('suite-')) suites.push(value);
      else if (file.startsWith('process-')) processes.push(value);
      else if (file.startsWith('worker-')) { workers.push(value); Object.assign(declarations, value.declarations); }
      else if (file.startsWith('child-')) children.push(value);
      else if (file.startsWith('sync-child-')) synchronousChildren.push(value);
      else if (file.startsWith('resolution-')) diagnostics.push(value);
      else if (file.startsWith('transform-')) transforms.push(value);
      else if (file.startsWith('resource-module-')) resourceModules.push(value);
      else if (file.startsWith('resource-effect-')) resourceEffects.push(value);
      else if (file.startsWith('limitation-')) limitations.push(JSON.stringify(value));
    } else limitations.push('Arquivo de captura não finalizado: ' + file);
  } catch (error) { limitations.push('Captura ilegível: ' + file + ': ' + error.message); }
}
for (const suite of suites) {
  if (!suite.terminal) limitations.push('Processo Vitest sem relatório terminal: ' + suite.suite_id);
  if (!Array.isArray(suite.planned_modules) || suite.reason === 'interrupted') limitations.push('Passagem interrompida ou sem censo inicial de módulos.');
  const plannedModules = (suite.planned_modules ?? []).map(/** Usa identidade projeto/arquivo da seleção original. */
    function key(value) { return JSON.stringify([value.project_id, value.file]); }).toSorted();
  const collectedModules = suite.modules.map(/** Compara módulos coletados com especificações anteriores à execução. */
    function key(value) { return JSON.stringify([value.project_id, value.file]); }).toSorted();
  if (JSON.stringify(plannedModules) !== JSON.stringify(collectedModules)) limitations.push('Módulos planejados não foram todos coletados.');
  const selected = new Set((suite.planned_modules ?? []).map(/** Restringe obrigação de transformação ao escopo original selecionado. */
    function file(value) { return value.file; }));
  const requiredFiles = new Set();
  for (const point of Object.values(config.plan.points)) {
    if (selected.has(point.path)) {
      requiredFiles.add(point.path);
      requiredFiles.add(config.plan.targets[point.instance_id].path);
    }
  }
  for (const file of requiredFiles) {
    if (!transforms.some(/** Exige fonte original processada nesta passagem Vitest, não em outro pacote. */
      function processed(value) { return value.suite_id === suite.suite_id && value.path === file; })) {
      limitations.push('Fonte necessária sem transformação observada: ' + file);
    }
  }
  for (const module of suite.modules) {
    if (!module.worker_id) limitations.push('Módulo sem registro de worker: ' + module.file);
    else emitters[module.worker_id] = 'worker';
  }
  for (const task of suite.tasks) {
    const module = suite.modules.find(/** Liga task ao módulo/projeto censado pelo reporter. */
      function same(value) { return value.file === task.file && value.project_id === task.project_id; });
    const started = workerEvents.filter(/** Exige identidade completa, nunca somente título ou contagem. */
      function same(event) {
        return event.event === 'case_started' && event.emitter_id === module?.worker_id
          && event.case.runtime_id === task.id && event.case.file === task.file && event.case.project_id === task.project_id;
      });
    if (started.length !== 1) { limitations.push('Caso do reporter sem correspondência única: ' + task.id); continue; }
    const finished = workerEvents.filter(/** Confere resultado final independente do stream do corpo. */
      function same(event) { return event.event === 'case_finished' && event.case_id === started[0].case_id; });
    const status = { passed: ['passed'], failed: ['failed'], skipped: ['skipped', 'todo'], pending: [] }[task.result.state] ?? [];
    if (finished.length !== 1 || !status.includes(finished[0].status)) limitations.push('Resultado/captura divergente: ' + task.id);
  }
}
const observedCases = workerEvents.filter(/** Conta casos de stream apenas depois de obter o censo independente. */
  function started(event) { return event.event === 'case_started'; });
if (observedCases.length !== suites.reduce(/** Compara o universo coletado pelo framework. */
  function count(total, suite) { return total + suite.tasks.length; }, 0)) limitations.push('Censo de casos diverge entre reporter e workers.');
for (const event of workerEvents) {
  if (!(event.emitter_id in emitters)) {
    limitations.push('Emissor não declarado na coleta do framework: ' + event.emitter_id);
    emitters[event.emitter_id] = 'worker';
  }
}
for (const emitterId of Object.keys(emitters)) {
  if (emitterId === coordinatorId) continue;
  if (!workerEvents.some(/** Exige fechamento do emissor esperado, não somente dos presentes. */
    function closed(event) { return event.emitter_id === emitterId && event.event === 'run_finished' && event.complete; })) {
    limitations.push('Worker esperado sem fechamento completo: ' + emitterId);
  }
}
for (const record of processes) {
  record.terminal_observed = record.closed || processEnded(record.pid);
  record.terminal_basis = record.closed ? 'node_exit' : record.terminal_observed ? 'kernel_process_ended' : null;
  if (!record.terminal_observed) limitations.push('Processo/thread Node sem fechamento: ' + record.id);
  let entry = null;
  try { if (record.argv[1]) entry = fs.realpathSync(record.argv[1]); } catch { /* Script inline não possui entrypoint em disco. */ }
  if (config.installations.some(/** Reconhece CLI real por caminho do pacote instalado. */
    function same(value) { return entry === path.join(value.root, 'vitest.mjs'); })
      && !suites.some(/** Exige que cada processo Vitest registrado tenha aberto um run report. */
        function same(suite) { return suite.pid === record.pid; })) limitations.push('Processo Vitest sem reporter: ' + record.pid);
}
for (const record of children) {
  record.terminal_observed = record.closed || processEnded(record.pid);
  record.terminal_basis = record.closed ? 'parent_close' : record.terminal_observed ? 'kernel_process_ended' : null;
  if (!record.terminal_observed) limitations.push('Subprocesso sem fechamento observado: ' + record.file);
  if (!record.preload_inherited) limitations.push('Subprocesso sem propagação comprovada do preload: ' + record.file);
}
const expectedCwds = config.command_packages[Number(ordinal)] ?? [];
for (const record of synchronousChildren) {
  record.process_observed = record.delegation_observable === true && record.node_launcher === true && !record.opaque_shell
    && (record.code_free || processes.some(/** Confere filho Node mesmo quando API síncrona retorna somente Buffer. */
    function observed(value) {
      let entry = null;
      try { entry = fs.realpathSync(value.argv[record.direct_node ? 0 : 1]); } catch { /* Filho sem entrypoint verificável não prova o launcher. */ }
      return entry === record.executable && value.logical_parent === record.parent && BigInt(value.start_ns) >= BigInt(record.start_ns)
        && BigInt(value.start_ns) <= BigInt(record.end_ns);
    }));
  if (!record.preload_inherited || !record.delegation_observable || record.opaque_shell || !record.process_observed) limitations.push('Descendência síncrona não observada completamente: ' + record.file);
}
if (JSON.stringify(expectedCwds.toSorted()) !== JSON.stringify(suites.map(/** Compara multiplicidade dos processos, não somente presença de um pacote. */
  function cwd(suite) { return suite.cwd; }).toSorted())) limitations.push('Processos Vitest divergem das folhas originais esperadas.');
for (const cwd of expectedCwds) {
  if (!suites.some(/** Confere pacotes esperados pela receita/script original, inclusive recursivos. */
    function same(suite) { return suite.cwd === cwd; })) limitations.push('Pacote unitário esperado sem execução Vitest: ' + cwd);
}
if (!suites.length || !processes.length || spawnError || exit.signal || exit.code === null) limitations.push('Passagem não possui fechamento confiável dos processos Vitest.');
limitations.push(...config.plan.limitations.map(/** Mantém limitações estáticas localizadas no relatório operacional. */
  function reason(value) { return JSON.stringify(value); }));
const complete = limitations.length === 0;
runtime.finish(complete, Object.keys(emitters), [...new Set(limitations)]);
const report = { protocol_version: 1, original_argv: argv, cwd: process.cwd(), original_exit: exit,
  spawn_error: spawnError, complete, limitations: [...new Set(limitations)],
  expected: { ...config.identity, emitters, census_complete: complete, declarations, points: config.plan.points },
  events: [...events.slice(0, 1), ...workerEvents, ...events.slice(1)], suites, processes, children,
   synchronous_children: synchronousChildren, workers, diagnostics, transforms, resource_modules: resourceModules, resource_effects: resourceEffects };
fs.writeFileSync(path.join(config.output_root, 'command-' + ordinal + '.json'), JSON.stringify(report));
process.exitCode = complete ? exit.code : 86;
