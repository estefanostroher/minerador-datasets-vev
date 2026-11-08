/** Executa exclusivamente fixture confiável e descartável dentro de Docker Linux. */
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import ts from 'typescript';
import { sourceHash } from '../validation-transform.mjs';
import { createValidationPlugin } from '../validation-vitest.mjs';

const version = process.argv[2];
const viteVersion = process.argv[3] ?? '8.3.6';
if (!['4.1.10', '4.1.11', '5.0.5'].includes(version) || process.cwd() !== '/fixture') {
  throw new Error('Fixture requer versão explícita e diretório Docker exclusivo.');
}
const install = spawnSync('npm', ['install', '--legacy-peer-deps', '--ignore-scripts', '--no-audit', '--no-fund',
  'vitest@' + version, 'vite@' + viteVersion], { encoding: 'utf8' });
process.stdout.write(install.stdout);
process.stderr.write(install.stderr);
if (install.status !== 0) throw new Error('Instalação da fixture indisponível; não comprova suporte.');
const tools = { node: process.versions.node, vitest: JSON.parse(fs.readFileSync('node_modules/vitest/package.json')).version,
  vite: JSON.parse(fs.readFileSync('node_modules/vite/package.json')).version, typescript: ts.version };
assert.equal(tools.vitest, version);
assert.equal(tools.vite, viteVersion);

const source = `import {test,expect,beforeEach,vi} from 'vitest';
/** Soma e registra entrada. */
export function compute(value: number) { if(value<0) throw value; return value+1; } test('same line',/** Preserva coluna original após inserção no corpo. */ function sameLine(){expect(compute(1)).toBe(2);});
/** Preserva identidade Promise do original. */
export async function asynchronous(value: number) { return value+1; }
class Counter {
  /** Inicializa estado da fixture. */
  constructor(){ this.value=3; }
  /** Soma usando this original. */
  add(value: number){ return this.value+value; }
}
/** Helper indireto, sem ponto direto. */
function helper(){ return compute(7); }
beforeEach(/** Hook sem crédito direto. */ function before(){ compute(100); });
test('duas', /** Executa duas chamadas em um caso. */ function twice(){ expect(compute(1)).toBe(2); expect(compute(2)).toBe(3); });
test.each([1,2,3,4])('igual', /** Mantém títulos iguais. */ function parameterized(value){ expect(compute(value)).toBe(value+1); });
test('metodo', /** Mantém getter antes dos argumentos. */ function method(){
  const effects=[]; const counter=new Counter();
  const receiver={get add(){ effects.push('get'); return counter.add; },value:3};
  function argument(){effects.push('arg'); return 2;}
  expect(receiver.add(argument())).toBe(5); expect(effects).toEqual(['get','arg']);
});
test('async throw', /** Mantém throw e await. */ async function mixed(){
  expect(await asynchronous(3)).toBe(4); expect.assertions(2);
  try {compute(-2);}catch(error){expect(error).toBe(-2);}
});
test('mock forwarding', /** Mock encaminha ao original sem provar chamada ao callable original. */ function mock(){
  const api={compute:vi.fn(/** Encaminha sem crédito. */ function forward(value){return compute(value);})};
  expect(api.compute(2)).toBe(3);
});
test('indireta', /** Helper não conta como chamada direta. */ function indirect(){expect(helper()).toBe(8);});
test.concurrent.each([1,2])('concorrente', /** Intercala identidades por ALS. */ async function concurrent(value){
  await Promise.resolve(); expect(compute(value)).toBe(value+1);
});
let attempt=0;
test('retry',{retry:1},/** Preserva primeira falha. */ function retry(){compute(1);if(++attempt===1)throw Error('primeira');});
test.skip('skip',/** Não executa ignorado. */ function ignored(){compute(8);});
test.todo('todo');
test.fails('expected',/** Fracasso esperado não vira positivo. */ function expected(){compute(2);throw Error('esperada');});
let late;
test('late',/** Agenda helper diferido sem associação direta. */ function scheduleLate(){
  late=new Promise(/** Agenda chamada posterior ao corpo do teste. */ function pending(resolve){
    setTimeout(/** Chamada diferida não pode virar evidência do caso seguinte. */ function deferred(){compute(90);resolve();},10);
  });
});
test('after late',/** Aguarda a tarefa anterior sem roubar sua atribuição. */ async function afterLate(){await late;expect(compute(4)).toBe(5);});
test('repeat',{repeats:1},/** Repeat mantém o mesmo caso reconhecido pelo runner. */ function repeat(){expect(compute(3)).toBe(4);});
`;
fs.writeFileSync('fixture.test.ts', source);
fs.mkdirSync('events');
const ast = ts.createSourceFile('fixture.test.ts', source, ts.ScriptTarget.Latest, true);
const targets = [], declarations = [], points = [];

/** Percorre somente corpo imediato do teste, excluindo hooks/helpers/diferidos. */
function callsIn(node, declaration, callback) {
  if (ts.isFunctionLike(node) && node !== callback) return;
  if (ts.isCallExpression(node)) {
    const expression = node.expression.getText(ast);
    const targetId = { compute: 'compute', asynchronous: 'asynchronous', 'receiver.add': 'add', 'api.compute': 'compute' }[expression];
    if (targetId) points.push({ id: 'point-' + node.getStart(ast), instance_id: targetId,
      declaration_id: declaration.id, start: node.getStart(ast), end: node.end });
  }
  ts.forEachChild(node, /** Mantém declaração enquanto percorre expressões imediatas. */
    function child(value) { callsIn(value, declaration, callback); });
}

/** Seleciona declarações e offsets da fixture; nenhuma fonte minerada é carregada. */
function visit(node) {
  if ((ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node)) && ['compute', 'asynchronous', 'add'].includes(node.name?.text)) {
    targets.push({ id: node.name.text, start: node.getStart(ast), end: node.end });
  }
  if (ts.isCallExpression(node) && node.expression.getText(ast).startsWith('test')) {
    const callback = node.arguments.find(/** Identifica o callback registrado pela fixture. */
      function isCallback(value) { return ts.isFunctionExpression(value) || ts.isArrowFunction(value); });
    const title = node.arguments[0];
    if ((callback || node.expression.getText(ast) === 'test.todo') && title && ts.isStringLiteral(title)) {
      const position = ast.getLineAndCharacterOfPosition(node.getStart(ast));
      const registrationOffset = ts.isCallExpression(node.expression) ? node.expression.end
        : ts.isPropertyAccessExpression(node.expression) ? node.expression.name.getStart(ast) : node.expression.getStart(ast);
      const registration = ast.getLineAndCharacterOfPosition(registrationOffset);
      const last = ast.getLineAndCharacterOfPosition(ts.isCallExpression(node.expression) ? node.expression.end - 1 : registrationOffset);
      const declaration = { id: 'declaration-' + node.getStart(ast), file: 'fixture.test.ts', start: node.getStart(ast), end: node.end,
        start_line: position.line + 1, start_column: position.character + 1,
        registration_line: registration.line + 1, registration_column: registration.character + 1,
        registration_kind: ts.isCallExpression(node.expression) ? 'application' : 'callee',
        application_last_line: last.line + 1, application_last_column: last.character + 1,
        modifiers: node.expression.getText(ast).includes('.fails') ? ['fails'] : node.expression.getText(ast).includes('.skip') ? ['skip'] : node.expression.getText(ast).includes('.todo') ? ['todo'] : [] };
      declarations.push(declaration);
      if (callback) callsIn(callback, declaration, callback);
    }
  }
  ts.forEachChild(node, visit);
}
visit(ast);
const plans = [{ path: 'fixture.test.ts', sha256: sourceHash(source), targets, points }];
fs.writeFileSync('plan.json', JSON.stringify({ tools, declarations, plans }));
const runner = `import {TestRunner} from '/fixture/node_modules/vitest/dist/index.js';
import {createValidationRunner} from '/instrument/validation-runner.mjs';
import fs from 'node:fs';
const plan=JSON.parse(fs.readFileSync('/fixture/plan.json','utf8'));
/** Persiste evento integral em stream próprio por emissor. */
function writeEvent(event){fs.appendFileSync('/fixture/events/'+event.emitter_id+'.jsonl',JSON.stringify(event)+'\\n');}
/** Mantém caminho original relativo ao workspace controlado. */
function normalizeFile(file){return file.replace('/fixture/','');}
export default createValidationRunner(TestRunner,{identity:{session_id:'fixture',repository_id:1,commit_sha:'a'.repeat(40),instrument_id:'fixture-v1'},
tools:plan.tools,declarations:plan.declarations,writeEvent,normalizeFile});`;
fs.writeFileSync('runner.mjs', runner);
const { startVitest } = await import(pathToFileURL(path.resolve('node_modules/vitest/dist/node.js')).href);
// Controle sem transform exclusivamente da fixture, nunca segunda passagem científica.
const baseline = await startVitest('test', [], { run: true });
await baseline.close();
assert.equal(process.exitCode ?? 0, 0);
const limitations = [];
/** Retém limitações de transformação sem convertê-las em contagem zero. */
function reportLimitation(value) { limitations.push(value); }
const context = await startVitest('test', [], { run: true, includeTaskLocation: true, runner: '/fixture/runner.mjs' },
  { plugins: [createValidationPlugin(plans, '/fixture', reportLimitation)] });
await context.close();
assert.equal(process.exitCode ?? 0, 0);
assert.deepEqual(limitations, []);
const events = fs.readdirSync('events').flatMap(/** Reúne streams completos após o framework fechar. */
  function read(file) { return fs.readFileSync('events/' + file, 'utf8').trim().split('\n').map(
    /** Decodifica cada evento sem confundir argumentos adicionais de map. */
    function parse(line) { return JSON.parse(line); }); });
const started = events.filter(/** Seleciona casos reconhecidos pelo runner. */
  function cases(event) { return event.event === 'case_started'; });
const finished = events.filter(/** Seleciona resultados sem apagar tentativas. */
  function cases(event) { return event.event === 'case_finished'; });
const direct = events.filter(/** Seleciona os pontos realmente atingidos. */
  function calls(event) { return event.event === 'direct_call'; });
const entered = events.filter(/** Seleciona entradas no callable original comprovado. */
  function calls(event) { return event.event === 'target_entered'; });
assert.equal(started.length, 19, JSON.stringify({ declarations, finished: events.filter(
  /** Mostra lacunas concretas caso o mapa de posições não corresponda. */
  function closed(event) { return event.event === 'run_finished'; }) }));
assert.equal(finished.length, 19);
const repeated = started.filter(/** Título duplicado não é identidade. */ function same(event) { return event.case.title === 'igual'; });
assert.equal(new Set(repeated.map(/** Recupera IDs do runner. */ function id(event) { return event.case_id; })).size, 4);
for (const item of started) {
  const calls = direct.filter(/** Seleciona chamadas do caso exato. */ function same(event) { return event.case_id === item.case_id; });
  const entries = entered.filter(/** Seleciona originais do caso exato. */ function same(event) { return event.case_id === item.case_id; });
  if (item.case.title === 'duas') assert.equal(entries.length, 2);
  if (item.case.title === 'mock forwarding') { assert.equal(calls.length, 1); assert.equal(entries.length, 0); }
  if (['indireta', 'skip', 'todo', 'late'].includes(item.case.title)) assert.equal(calls.length, 0);
  if (item.case.title === 'after late') assert.equal(entries.length, 1);
  if (item.case.title === 'repeat') {
    assert.equal(entries.length, 2);
    const result = finished.find(/** Recupera repetições dentro do único caso. */
      function same(event) { return event.case_id === item.case_id; });
    assert.deepEqual(result.attempts.map(/** Verifica identidade por repetição, sem caso extra. */
      function repeat(attempt) { return attempt.repeat; }), [0, 1]);
  }
  if (item.case.title === 'retry') {
    const result = finished.find(/** Recupera resultado final e histórico. */ function same(event) { return event.case_id === item.case_id; });
    assert.equal(result.status, 'passed');
    assert.deepEqual(result.attempts.map(/** Preserva a primeira falha apesar de retry verde. */
      function status(attempt) { return attempt.status; }), ['failed', 'passed']);
  }
}
assert.ok(events.filter(/** Inspeciona fechamento de todos os emissores observados. */
  function closed(event) { return event.event === 'run_finished'; }).every(/** Exige localização completa da fixture. */
  function complete(event) { return event.complete; }));
assert.equal(fs.readFileSync('fixture.test.ts', 'utf8'), source);
console.log(JSON.stringify({ fixture: 'validation-vitest', tools, cases: started.length, direct_calls: direct.length,
  original_entries: entered.length, process_census_proven: false, original_script_bootstrap_proven: false }));
