import assert from 'node:assert/strict';
import test from 'node:test';
import { Project } from 'ts-morph';
import { SnapshotProgram, openSnapshot } from '../src/project.js';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import { resolve } from 'node:path';

function program(files: Record<string, string>): SnapshotProgram[] {
  const project = new Project({useInMemoryFileSystem: true});
  for (const [path, text] of Object.entries(files)) project.createSourceFile('/snapshot/' + path, text);
  return [{project, root: '/snapshot', sourceFiles: project.getSourceFiles(), texts: new Map(Object.entries(files))}];
}

test('invocation evidence preserves values, this, errors and ignores argument helper entries', async () => {
  const { createRuntime } = await import('../src/instrumentation/runtime.js');
  const events: any[] = [];
  const rt = createRuntime(event => events.push(event));
  function target(this: {n: number}, n: number) { rt.entry('target', target); return this.n + n; }
  function helper() { target.call({n: 8}, 9); return 3; }
  const call = rt.method({n: 2, target}, 'target', 'site', 'target');
  const result = rt.runCase('a.test.ts', 10, '0', null, () => call(helper()));
  assert.equal(result, 5);
  assert.deepEqual(events.map(e => e.kind), ['test_entry', 'original_target_entry', 'direct_callsite', 'original_target_entry', 'case_end']);
  const entries = events.filter(e => e.kind === 'original_target_entry');
  assert.equal(entries[0].invocation_id, null);
  assert.equal(entries[1].invocation_id, events[2].invocation_id);
  assert.equal(entries[1].attributable, true);
  function throws() { rt.entry('throws', throws); throw Error('expected'); }
  assert.throws(() => rt.runCase('a.test.ts', 20, '0', null, () => rt.fn(throws, 'throw-site', 'throws')()), /expected/);
  assert.equal(events.at(-1).outcome, 'threw');
});

test('ALS isolates concurrent promises and closes cases without a global current-test ID', async () => {
  const { createRuntime } = await import('../src/instrumentation/runtime.js');
  const events: any[] = [];
  const rt = createRuntime(event => events.push(event));
  function target() { rt.entry('target', target); return 1; }
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const a = rt.runCase('a.test.ts', 1, 'a', null, async () => { await gate; rt.fn(target, 'a-site', 'target')(); });
  const b = rt.runCase('b.test.ts', 2, 'b', null, async () => { await Promise.resolve(); rt.fn(target, 'b-site', 'target')(); release(); });
  await Promise.all([a, b]);
  for (const e of events.filter(e => e.attributable)) assert.equal(e.runtime_id, e.callsite_id === 'a-site' ? 'a' : 'b');
  rt.entry('target', target);
  assert.equal(events.at(-1).attributable, false);
});

test('a mocked wrapper entering the original under the same call ID is not direct evidence', async () => {
  const { createRuntime } = await import('../src/instrumentation/runtime.js');
  const events: any[] = [];
  const rt = createRuntime(event => events.push(event));
  function original() { rt.entry('target', original); return 1; }
  const mock = () => original();
  rt.runCase('a.test.ts', 1, '0', null, () => rt.fn(mock, 'site', 'target')());
  assert.equal(events.find(e => e.kind === 'original_target_entry').attributable, false);
});

test('overlay uses original offsets, preserves directive prologues and emits truthful transpilation maps', async () => {
  const { instrument } = await import('../src/instrumentation/transform.js');
  const sources = {'value.ts': `export function target(n: number) { 'use strict'; return n + 1; }`,
    'a.test.ts': `import {target as alias} from './value';\ntest('alias', () => expect(alias(1)).toBe(2));`};
  const programs = program(sources);
  const result = instrument(programs, 'jest', '29.7.0');
  assert.equal(result.status, 'ready');
  assert.equal(result.callsites.length, 1);
  assert.equal(result.callsites[0].start_offset, sources['a.test.ts'].indexOf('alias(1)'));
  const target = result.files.find(f => f.path === 'value.ts')!;
  assert.match(target.source, /'use strict';[^}]*\.entry\(/);
  assert.ok(target.source.indexOf("'use strict'") < target.source.indexOf('.entry('));
  assert.equal(target.original_sha256.length, 64);
  assert.notEqual(target.original_sha256, target.overlay_sha256);
  assert.equal(JSON.parse(target.transpilation.source_map).sourcesContent[0], target.source);
  assert.equal(JSON.parse(target.transpilation.source_map).version, 3);
  assert.equal(programs[0].texts.get('value.ts'), sources['value.ts']);
  assert.equal(programs[0].project.getSourceFileOrThrow('/snapshot/value.ts').getFullText(), sources['value.ts']);
  const declaration = result.declarations[0];
  assert.equal(declaration.original_position, sources['a.test.ts'].indexOf('test('));
  assert.ok(declaration.overlay_start > declaration.original_position);
});

test('unsupported optional/spread/await/super/yield and concurrency modes leave every original byte intact', async () => {
  const { instrument } = await import('../src/instrumentation/transform.js');
  for (const invocation of ['target?.(1)', 'target(...[1])', 'target(await Promise.resolve(1))']) {
    const files = {'value.ts': 'export function target(n: number) { return n; }',
      'a.test.ts': `import {target} from './value'; test('case', async () => { ${invocation}; });`};
    const result = instrument(program(files), 'vitest', '4.0.0');
    assert.equal(result.status, 'not_evaluated', invocation);
    assert.deepEqual(result.files, []);
    assert.ok(result.limitations.length);
  }
  const files = {'value.ts': 'export function target() { return 1; }',
    'a.test.ts': `import {target} from './value'; test.concurrent('case', async () => target());`};
  assert.equal(instrument(program(files), 'vitest', '4.0.0').status, 'not_evaluated');
  assert.equal(instrument(program(files), 'vitest', '4.0.1').status, 'not_evaluated');
  for (const extra of [
    {'value.ts': 'export function* target(n: number) { yield n; }',
      'a.test.ts': `import {target} from './value'; test('generator', () => target(1).next());`},
    {'value.ts': 'export function target(n: number) { return n; }',
      'a.test.ts': `import {target} from './value'; test('yield', function* () { target(yield 1); });`},
  ]) {
    const result = instrument(program(extra), 'jest', '29.7.0');
    assert.equal(result.status, 'not_evaluated');
    assert.deepEqual(result.files, []);
  }
});

test('method probes evaluate receiver, getter and arguments once in their original order', async () => {
  const { createRuntime } = await import('../src/instrumentation/runtime.js');
  const observed: string[] = [];
  const rt = createRuntime(() => {});
  const receiver = () => { observed.push('receiver'); return {get method() { observed.push('getter'); return function(this: unknown, n: number) { observed.push('call'); return n; }; }}; };
  const argument = () => { observed.push('argument'); return 9; };
  assert.equal(rt.method(receiver(), 'method', 'site', 'id')(argument()), 9);
  assert.deepEqual(observed, ['receiver', 'getter', 'argument', 'call']);
});

test('overlay report locations restore original case identity through hashes and coordinates, never titles', async () => {
  const { restoreReport } = await import('../src/instrumentation/reports.js');
  const { instrument, sha256 } = await import('../src/instrumentation/transform.js');
  const sources = {'value.ts': 'export function target() { return 1; }',
    'a.test.ts': `import {target} from './value';\ntest('same', () => target());\ntest('same', () => target());`};
  const overlay = instrument(program(sources), 'jest', '29.7.0');
  const file = overlay.files.find(f => f.path === 'a.test.ts')!;
  const location = (offset: number) => { const prefix = file.source.slice(0, offset); return {line: prefix.split('\n').length, column: offset - prefix.lastIndexOf('\n')}; };
  const raw = {framework: 'jest', version: '29.7.0', source_hashes: {'a.test.ts': file.overlay_sha256},
    results: {testResults: [{testFilePath: '/workspace/a.test.ts', testResults: overlay.declarations.map(d => ({title: 'same', location: location(d.overlay_start)}))}]}};
  const restored = restoreReport(raw, sources, overlay);
  assert.equal(restored.source_hashes['a.test.ts'], sha256(sources['a.test.ts']));
  assert.deepEqual(restored.results.testResults[0].testResults.map((c: any) => c.location.line), [2, 3]);
  assert.equal(raw.source_hashes['a.test.ts'], file.overlay_sha256);
  assert.throws(() => restoreReport({...raw, source_hashes: {'a.test.ts': 'a'.repeat(64)}}, sources, overlay), /hash/);
  assert.throws(() => restoreReport(raw, {...sources, 'a.test.ts': '\n' + sources['a.test.ts']}, overlay), /hash/);
});

test('circus registration stacks support bare each/callback frames as well as named frames', () => {
  const source = readFileSync(new URL('../../src/instrumentation/supplements/adapter.cjs', import.meta.url), 'utf8');
  const exports: any = {};
  runInNewContext(source, {exports, require: (name: string) => name === 'node:fs' ? {} : {declarations: []}});
  const stack = 'Error:\n    at eachBind (/workspace/node_modules/jest-each/build/bind.js:39:22)\n    at /workspace/value.test.ts:6:28\n    at Object.<anonymous> (/workspace/value.test.ts:5:1)';
  assert.deepEqual(JSON.parse(JSON.stringify(exports.stackLocations('value.test.ts', stack))), [{line: 6, column: 28}, {line: 5, column: 1}]);
});

test('Vitest wraps the runner getFn/setFn store, not a fictitious task.fn property', () => {
  const source = readFileSync(new URL('../../src/instrumentation/supplements/vitest-setup.mjs', import.meta.url), 'utf8');
  const js = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
  let hook!: (context: any) => void;
  const root = {filepath: '/workspace/a.test.ts', tasks: [] as any[]};
  const task = {type: 'test', id: 'runtime-collected-42', file: root, location: {line: 2, column: 1}};
  root.tasks.push(task);
  const functions = new WeakMap<object, Function>([[task, () => 7]]);
  let wrapped = false;
  const declaration = {original_position: 10, parameterized: false};
  const adapter = {locate: () => declaration, collect: () => {},
    wrap: (_runtime: unknown, fn: Function, _file: string, _declaration: unknown, id: string) => () => {
      assert.equal(id, task.id); wrapped = true; return fn();
    }};
  runInNewContext(js, {exports: {}, WeakSet, Map, require: (name: string) => {
    if (name.includes('/vitest/')) return {beforeEach: (callback: typeof hook) => { hook = callback; }};
    if (name.includes('/runner/')) return {getFn: (task: object) => functions.get(task), setFn: (task: object, fn: Function) => functions.set(task, fn)};
    if (name === './runtime.cjs') return {default: {runtime: {}}};
    return {default: adapter};
  }});
  hook({task});
  assert.equal(functions.get(task)!(), 7);
  assert.equal(wrapped, true);
});

test('promises retaining a closed case context cannot attribute a later invocation', async () => {
  const { createRuntime } = await import('../src/instrumentation/runtime.js');
  const events: any[] = [];
  const rt = createRuntime(event => events.push(event));
  function original() { rt.entry('target', original); }
  let pending!: Promise<void>;
  rt.runCase('a.test.ts', 1, '0', null, () => {
    pending = Promise.resolve().then(() => { rt.fn(original, 'late', 'target')(); });
  });
  await pending;
  const entry = events.find(e => e.kind === 'original_target_entry');
  assert.equal(entry.runtime_id, '0');
  assert.equal(entry.attributable, false);
  assert.ok(events.findIndex(e => e.kind === 'case_end') < events.findIndex(e => e.kind === 'direct_callsite'));
});

test('class evaluation cannot replace a method before its original identity is captured', async () => {
  const { instrument } = await import('../src/instrumentation/transform.js');
  for (const source of [
    `@decorate export class C { method() { return 1; } }`,
    `export class C { method() { return 1; } static { Object.defineProperty(this.prototype, 'method', {value: wrap(this.prototype.method)}); } }`,
    `export class C { method() { return 1; } static replaced = wrap(this.prototype); }`,
  ]) {
    const result = instrument(program({'value.ts': source,
      'a.test.ts': `import {C} from './value'; test('case', () => { const c = new C(); c.method(); });`}), 'jest', '29.7.0');
    assert.equal(result.status, 'not_evaluated', source);
    assert.deepEqual(result.files, []);
  }
});

for (const [framework, version] of [['jest', '29.7.0'], ['jest', '30.0.0'], ['vitest', '3.2.4'], ['vitest', '4.0.0']] as const) {
  test(`project overlay replays original bytes and source maps for ${framework} ${version}`, async () => {
    const { instrument } = await import('../src/instrumentation/transform.js');
    const root = resolve('../../tests/fixtures/projects/' + framework + '-basic');
    const programs = await openSnapshot(root, ['tsconfig.json'], root);
    const current = instrument(programs, framework, version);
    assert.equal(current.status, 'ready');
    assert.deepEqual(current.files.map(file => file.path).sort(),
      ['dependency.ts', 'service.ts', 'test/direct.case.ts', 'value.test.ts', 'value.ts']);
    assert.ok(current.callsites.length >= 18);
    for (const file of current.files) {
      const original = readFileSync(resolve(root, file.path), 'utf8');
      let replayed = '', cursor = 0;
      for (const edit of file.edits) {
        assert.ok(edit.start >= cursor && edit.end >= edit.start);
        replayed += original.slice(cursor, edit.start);
        assert.equal(replayed.length, edit.overlay_start);
        replayed += edit.text;
        assert.equal(replayed.length, edit.overlay_end);
        cursor = edit.end;
      }
      replayed += original.slice(cursor);
      assert.equal(replayed, file.source);
      assert.equal(JSON.parse(file.transpilation.source_map).sourcesContent[0], replayed);
    }
    assert.deepEqual(instrument(programs, framework, version), current);
  });
}
