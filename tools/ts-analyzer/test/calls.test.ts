import assert from 'node:assert/strict';
import { test } from 'node:test';
import { resolve } from 'node:path';
import { mkdtemp, writeFile, mkdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { openSnapshot } from '../src/project.js';
import { discoverTargets } from '../src/targets.js';
import { discoverCalls } from '../src/calls.js';

test('resolves aliases, barrels, overloads and literal receivers without name matching', async () => {
  const projects = await openSnapshot(resolve('../../tests/fixtures/projects/static-symbols'), ['tsconfig.json'], resolve('../../tests/fixtures/projects'));
  const { targets } = discoverTargets(projects);
  const { test_calls, limitations } = discoverCalls(projects, targets, 'vitest');
  const direct = test_calls.filter(c => c.link_kind === 'direct');
  assert.equal(direct.length, 5);
  const names = direct.map(c => targets.find(t => t.id === c.symbol_target_id)!.qualified_name);
  assert.deepEqual(names.sort(), ['Counter.increment', 'overloaded', 'same', 'same', 'same'].sort());
  assert.equal(new Set(direct.filter(c => c.case_title === 'group first').map(c => c.case_declaration_id)).size, 1);
  assert.ok(direct.some(c => c.parameterized && c.case_title === 'group second %s'));
  assert.ok(test_calls.some(c => c.link_kind === 'unresolved' && c.reason === 'ambiguous_receiver'));
  assert.ok(!direct.some(c => c.case_title.includes('property') || c.case_title.includes('nested helper')));
  assert.ok(direct.every(c => c.path_resolution.length > 0 && c.runtime_confirmed === false));
  assert.ok(direct.filter(c => c.case_title === 'group first').every(c => c.path_resolution.includes('src/barrel.ts')));
  assert.ok(limitations.some(l => l.code === 'static_only'));
});
test('Jest globals, aliased imports and namespaces exclude local shadowing and named helpers', async () => {
  const root = await mkdtemp(resolve(tmpdir(), 'analyzer-calls-'));
  await mkdir(resolve(root, 'test'));
  await writeFile(resolve(root, 'tsconfig.json'), '{"include":["**/*.ts"]}');
  await writeFile(resolve(root, 'source.ts'), 'export function target() { return 1; }');
  await writeFile(resolve(root, 'test/a.test.ts'), `
import { target } from '../source';
import { test as check, beforeEach } from '@jest/globals';
import * as j from '@jest/globals';
test('global', () => target());
check('alias', () => { target(); });
j.it('namespace', () => { target(); });
test('indirect', () => { const helper = () => target(); helper(); });
test('registers hook', () => { beforeEach(() => target()); });
function shadow(it: any, test: any) { it('bad', () => target()); test('bad', () => target()); }
`);
  const programs = await openSnapshot(root, ['tsconfig.json'], root);
  const { targets } = discoverTargets(programs);
  const { test_calls } = discoverCalls(programs, targets, 'jest');
  assert.deepEqual(test_calls.filter(c => c.link_kind === 'direct').map(c => c.case_title).sort(), ['alias', 'global', 'namespace']);
});
test('class-valued object properties and reassigned methods are not exact method receivers', async () => {
  const root = await mkdtemp(resolve(tmpdir(), 'analyzer-receiver-'));
  await mkdir(resolve(root, 'test'));
  await writeFile(resolve(root, 'tsconfig.json'), '{"include":["**/*.ts"]}');
  await writeFile(resolve(root, 'source.ts'), `
export class Service { run() { return 1; } }
export class Holder { run = Service; }
`);
  await writeFile(resolve(root, 'test/a.test.ts'), `
import { Service, Holder } from '../source';
test('properties', () => { new Holder().run(); });
test('mutated', () => { const service = new Service(); service.run = () => 2; service.run(); });
test('mocked', () => { jest.spyOn(Service.prototype, 'run'); new Service().run(); });
`);
  const programs = await openSnapshot(root, ['tsconfig.json'], root);
  const { targets } = discoverTargets(programs);
  const { test_calls } = discoverCalls(programs, targets, 'jest');
  assert.ok(!test_calls.some(c => c.case_title === 'properties' && c.link_kind === 'direct'));
  assert.ok(!test_calls.some(c => c.case_title === 'mutated' && c.link_kind === 'direct'));
  assert.ok(test_calls.some(c => c.case_title === 'mocked' && c.mock_potential && !c.runtime_confirmed));
});
test('tagged each keeps one declaration identity without inferred execution counts', async () => {
  const root = await mkdtemp(resolve(tmpdir(), 'analyzer-each-'));
  await mkdir(resolve(root, 'test'));
  await writeFile(resolve(root, 'tsconfig.json'), '{"include":["**/*.ts"]}');
  await writeFile(resolve(root, 'source.ts'), 'export function target(value: number) { return value; }');
  await writeFile(resolve(root, 'test/a.test.ts'), "import { target } from '../source'; test.each`value\n${1}\n${2}`('value $value', ({value}) => target(value));");
  const programs = await openSnapshot(root, ['tsconfig.json'], root);
  const { test_calls } = discoverCalls(programs, discoverTargets(programs).targets, 'jest');
  const direct = test_calls.filter(c => c.link_kind === 'direct');
  assert.equal(direct.length, 1);
  assert.equal(direct[0].parameterized, true);
  assert.equal(direct[0].case_title, 'value $value');
  assert.ok(!('execution_count' in direct[0]));
});
test('static methods require concrete class receivers rather than polymorphic constructor types', async () => {
  const root = await mkdtemp(resolve(tmpdir(), 'analyzer-static-'));
  await mkdir(resolve(root, 'test'));
  await writeFile(resolve(root, 'tsconfig.json'), '{"include":["**/*.ts"]}');
  await writeFile(resolve(root, 'source.ts'), `
export class Base { static run() { return 1; } }
export class Derived extends Base { static run() { return 2; } }
`);
  await writeFile(resolve(root, 'barrel.ts'), "export { Base as Original, Derived } from './source';");
  await writeFile(resolve(root, 'test/a.test.ts'), `
import { Original as Alias, Derived } from '../barrel';
import * as classes from '../source';
const klass: typeof Alias = Derived;
test('polymorphic', () => klass.run());
test('original', () => Alias.run());
test('derived', () => Derived.run());
test('namespace', () => classes.Base.run());
`);
  const programs = await openSnapshot(root, ['tsconfig.json'], root);
  const { targets } = discoverTargets(programs);
  const { test_calls } = discoverCalls(programs, targets, 'jest');
  const polymorphic = test_calls.find(c => c.case_title === 'polymorphic')!;
  assert.equal(polymorphic.link_kind, 'unresolved');
  assert.equal(polymorphic.reason, 'ambiguous_receiver');
  assert.equal(polymorphic.symbol_target_id, null);
  for (const [title, name] of [['original', 'Base.run'], ['derived', 'Derived.run'], ['namespace', 'Base.run']]) {
    const call = test_calls.find(c => c.case_title === title)!;
    assert.equal(call.link_kind, 'direct');
    assert.equal(targets.find(t => t.id === call.symbol_target_id)!.qualified_name, name);
  }
});
test('method writes in production dependencies invalidate only the matching class symbols', async () => {
  const root = await mkdtemp(resolve(tmpdir(), 'analyzer-production-write-'));
  await mkdir(resolve(root, 'test'));
  await writeFile(resolve(root, 'tsconfig.json'), '{"include":["test/**/*.ts"]}');
  await writeFile(resolve(root, 'source.ts'), `
export class Service { run() { return 1; } }
export class Dynamic { run() { return 3; } }
export class Other { run() { return 4; } }
`);
  await writeFile(resolve(root, 'mutate.ts'), `
import { Service as Changed, Dynamic } from './source';
Changed.prototype.run = () => 2;
declare const key: string;
Dynamic.prototype[key] = () => 5;
`);
  await writeFile(resolve(root, 'test/a.test.ts'), `
import { Service as Alias, Dynamic, Other } from '../source';
import '../mutate';
test('production write', () => new Alias().run());
test('dynamic write', () => new Dynamic().run());
test('other class', () => new Other().run());
`);
  const programs = await openSnapshot(root, ['tsconfig.json'], root);
  // Imported files are context here, but their declarations are eligible when
  // also selected as roots by the source tsconfig, as in a multi-config request.
  await writeFile(resolve(root, 'source-config.json'), '{"include":["source.ts"]}');
  const sourcePrograms = await openSnapshot(root, ['source-config.json'], root);
  const { targets } = discoverTargets(sourcePrograms);
  const { test_calls } = discoverCalls(programs, targets, 'jest');
  for (const title of ['production write', 'dynamic write']) {
    const call = test_calls.find(c => c.case_title === title)!;
    assert.equal(call.link_kind, 'unresolved', title);
    assert.equal(call.reason, 'ambiguous_receiver');
    assert.equal(call.symbol_target_id, null);
  }
  const control = test_calls.find(c => c.case_title === 'other class')!;
  assert.equal(control.link_kind, 'direct');
  assert.equal(targets.find(t => t.id === control.symbol_target_id)!.qualified_name, 'Other.run');
});
for (const [name, mutation] of [
  ['object destructuring', '({ run: Service.prototype.run } = { run: () => 2 });'],
  ['array destructuring', '[Service.prototype.run] = [() => 2];'],
  ['delete', 'delete Service.prototype.run;'],
  ['nested default destination', '({ nested: { run: Service.prototype.run = Other.prototype.run } } = { nested: {} });'],
  ['for-of destination', 'for (Service.prototype.run of [() => 2]) {}'],
  ['for-of object destination', 'for ({ run: Service.prototype.run } of [{ run: () => 2 }]) {}'],
  ['for-of array destination', 'for ([Service.prototype.run] of [[() => 2]]) {}'],
  ['for-in destination', 'for (Service.prototype.run in { value: 2 }) {}'],
  ['for-in object destination', 'for ({ run: Service.prototype.run } in { value: 2 }) {}'],
  ['prefix increment', '++Service.prototype.run;'],
  ['prefix decrement', '--Service.prototype.run;'],
  ['postfix increment', 'Service.prototype.run++;'],
  ['postfix decrement', 'Service.prototype.run--;'],
]) {
  test(`${name} invalidates the method destination without invalidating homonymous reads`, async () => {
    const root = await mkdtemp(resolve(tmpdir(), 'analyzer-write-pattern-'));
    await mkdir(resolve(root, 'test'));
    await writeFile(resolve(root, 'tsconfig.json'), '{"include":["**/*.ts"]}');
    await writeFile(resolve(root, 'source.ts'), `
export class Service { run() { return 1; } }
export class Other { run() { return 3; } }
`);
    await writeFile(resolve(root, 'mutate.ts'), `import { Service, Other } from './source'; ${mutation}`);
    await writeFile(resolve(root, 'test/a.test.ts'), `
import { Service as Alias, Other } from '../source';
import '../mutate';
test('changed', () => new Alias().run());
test('control', () => new Other().run());
`);
    const programs = await openSnapshot(root, ['tsconfig.json'], root);
    const { targets } = discoverTargets(programs);
    const { test_calls } = discoverCalls(programs, targets, 'jest');
    const changed = test_calls.find(c => c.case_title === 'changed')!;
    assert.equal(changed.link_kind, 'unresolved');
    assert.equal(changed.reason, 'ambiguous_receiver');
    assert.equal(changed.symbol_target_id, null);
    const control = test_calls.find(c => c.case_title === 'control')!;
    assert.equal(control.link_kind, 'direct');
    assert.equal(targets.find(t => t.id === control.symbol_target_id)!.qualified_name, 'Other.run');
    assert.ok(test_calls.every(c => c.runtime_confirmed === false));
  });
}
test('local loop declarations and read-only unary operands do not invalidate method symbols', async () => {
  const root = await mkdtemp(resolve(tmpdir(), 'analyzer-loop-control-'));
  await mkdir(resolve(root, 'test'));
  await writeFile(resolve(root, 'tsconfig.json'), '{"include":["**/*.ts"]}');
  await writeFile(resolve(root, 'source.ts'), `
export class Service { run() { return 1; } }
export class Other { run() { return 3; } }
`);
  await writeFile(resolve(root, 'read.ts'), `
import { Service, Other } from './source';
for (const run of [Service.prototype.run]) {}
for (const { run } of [Other.prototype]) {}
for (const run in Service.prototype) {}
for (let run of [Other.prototype.run]) { run++; }
+Service.prototype.run;
-Other.prototype.run;
`);
  await writeFile(resolve(root, 'test/a.test.ts'), `
import { Service, Other } from '../source';
import '../read';
test('service', () => new Service().run());
test('other', () => new Other().run());
`);
  const programs = await openSnapshot(root, ['tsconfig.json'], root);
  const { targets } = discoverTargets(programs);
  const { test_calls } = discoverCalls(programs, targets, 'jest');
  assert.equal(test_calls.length, 2);
  assert.ok(test_calls.every(c => c.link_kind === 'direct' && !c.runtime_confirmed));
  assert.deepEqual(test_calls.map(c => targets.find(t => t.id === c.symbol_target_id)!.qualified_name).sort(), ['Other.run', 'Service.run']);
});
