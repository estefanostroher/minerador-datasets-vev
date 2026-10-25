import assert from 'node:assert/strict';
import { test } from 'node:test';
import { resolve } from 'node:path';
import { readFile, mkdtemp, mkdir, writeFile, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { openSnapshot } from '../src/project.js';
import { discoverTargets } from '../src/targets.js';
import { discoverCalls } from '../src/calls.js';

export const fixture = resolve('../../tests/fixtures/projects/static-symbols');
export async function program() {
  return openSnapshot(fixture, ['tsconfig.json'], resolve('../../tests/fixtures/projects'));
}
test('extracts implementations, stable identities and original UTF16 intervals', async () => {
  const projects = await program();
  const targets = discoverTargets(projects).targets;
  assert.equal(targets.length, 4);
  const target = targets.find(t => t.path === 'src/first.ts' && t.qualified_name === 'same')!;
  const text = await readFile(resolve(fixture, target.path), 'utf8');
  assert.equal(target.start_offset, text.indexOf('export function same'));
  assert.equal(target.code, text.slice(target.start_offset, target.end_offset));
  assert.equal(target.signature, 'export function same(value: number): number ');
  assert.equal(target.start_line, 3);
  assert.equal(target.start_column, 0);
  assert.match(target.id, /^sha256:[a-f0-9]{64}$/);
  assert.equal(discoverTargets(projects).targets.find(t => t.code === target.code)!.id, target.id);
  assert.ok(targets.some(t => t.qualified_name === 'Counter.increment'));
  assert.ok(!targets.some(t => t.path.startsWith('test/')));
});
test('preserves BOM, CRLF and astral offsets without formatting', async () => {
  const root = await mkdtemp(resolve(tmpdir(), 'analyzer-'));
  const text = '\ufeff// 😀\r\n/** doc */\r\nexport function f() {\r\n return 1;\r\n}\r\n';
  await writeFile(resolve(root, 'tsconfig.json'), '{"include":["*.ts"]}');
  await writeFile(resolve(root, 'a.ts'), text);
  const result = discoverTargets(await openSnapshot(root, ['tsconfig.json'], root));
  assert.equal(result.targets[0].start_offset, text.indexOf('export'));
  assert.equal(result.targets[0].code, text.slice(text.indexOf('export'), text.lastIndexOf('}') + 1));
});
test('rejects escapes, external extends and symlinks before program creation', async () => {
  await assert.rejects(openSnapshot(fixture, ['../tsconfig.json'], resolve('../../tests/fixtures/projects')));
  await assert.rejects(openSnapshot(fixture, ['C:/secret.json'], resolve('../../tests/fixtures/projects')));
  const root = await mkdtemp(resolve(tmpdir(), 'analyzer-'));
  await writeFile(resolve(root, 'tsconfig.json'), '{"extends":"../outside.json"}');
  await assert.rejects(openSnapshot(root, ['tsconfig.json'], root), /config/);
  await mkdir(resolve(root, 'nested'));
  await symlink(resolve(root, 'nested'), resolve(root, 'inside'), 'junction');
  await writeFile(resolve(root, 'tsconfig.json'), '{"include":["**/*.ts"]}');
  await writeFile(resolve(root, 'nested/a.ts'), 'export function a() {}');
  assert.ok((await openSnapshot(root, ['tsconfig.json'], root)).length);
  await symlink(tmpdir(), resolve(root, 'outside'), 'junction');
  await assert.rejects(openSnapshot(root, ['tsconfig.json'], root), /snapshot/);
});
test('honors multiple tsconfig options and excludes while retaining dependencies as context', async () => {
  const root = await mkdtemp(resolve(tmpdir(), 'analyzer-'));
  await mkdir(resolve(root, 'src'));
  await mkdir(resolve(root, 'test'));
  await writeFile(resolve(root, 'base.json'), '{"compilerOptions":{"baseUrl":".","paths":{"@first":["src/one.ts"]}}}');
  await writeFile(resolve(root, 'tsconfig.json'), '{"extends":"./base.json","include":["src/one.ts","test/*.ts"],"exclude":["src/excluded.ts"]}');
  await writeFile(resolve(root, 'second.json'), '{"include":["src/two.ts"]}');
  await writeFile(resolve(root, 'src/one.ts'), 'export function one() { return 1; }');
  await writeFile(resolve(root, 'src/two.ts'), 'export function two() { return 2; }');
  await writeFile(resolve(root, 'src/excluded.ts'), 'export function excluded() { return 3; }');
  await writeFile(resolve(root, 'test/a.test.ts'), "import { one } from '@first'; test('one', () => one());");
  const programs = await openSnapshot(root, ['tsconfig.json', 'second.json'], root);
  assert.equal(programs[0].project.getCompilerOptions().baseUrl, '/snapshot');
  assert.deepEqual(discoverTargets(programs).targets.map(t => t.qualified_name).sort(), ['one', 'two']);
  await assert.rejects(openSnapshot(root, ['tsconfig.json'], root, { maxFiles: 1, maxBytes: 10000 }), /limit/);
  await assert.rejects(openSnapshot(root, ['tsconfig.json'], root, { maxFiles: 100, maxBytes: 1 }), /limit/);
});
test('allowJs keeps JavaScript as explicit context and emits only original TypeScript implementations', async () => {
  const root = await mkdtemp(resolve(tmpdir(), 'analyzer-js-context-'));
  await mkdir(resolve(root, 'test'));
  await writeFile(resolve(root, 'tsconfig.json'), '{"compilerOptions":{"allowJs":true,"jsx":"preserve"},"include":["**/*"]}');
  const tsPaths = ['typed.ts', 'component.tsx', 'esm.mts', 'common.cts'];
  const jsPaths = ['source.js', 'component-js.jsx', 'module.mjs', 'legacy.cjs', 'disguised.ts.js'];
  for (const path of [...tsPaths, ...jsPaths]) {
    await writeFile(resolve(root, path), 'export function target() { return 1; }');
  }
  for (const path of ['types.d.ts', 'esm-types.d.mts', 'common-types.d.cts']) {
    await writeFile(resolve(root, path), 'export declare function declaration(): number;');
  }
  await writeFile(resolve(root, 'test/a.test.ts'), `
import { target as tsAlias } from '../typed';
import { target as jsAlias } from '../source.js';
test('TS control', () => tsAlias());
test('JS context', () => jsAlias());
`);
  const programs = await openSnapshot(root, ['tsconfig.json'], root);
  assert.ok(programs[0].project.getSourceFile('/snapshot/source.js'));
  const { targets, limitations } = discoverTargets(programs);
  assert.deepEqual(targets.map(t => t.path).sort(), tsPaths.sort());
  for (const path of jsPaths) {
    assert.ok(limitations.some(l => l.code === 'javascript_context_only' && l.path === path));
  }
  const { test_calls } = discoverCalls(programs, targets, 'jest');
  assert.equal(test_calls.find(c => c.case_title === 'TS control')!.link_kind, 'direct');
  assert.equal(test_calls.find(c => c.case_title === 'JS context')!.link_kind, 'unresolved');
});
test('validates only the requested TypeScript config graph, leaving unrelated JSON as context', async () => {
  const root = await mkdtemp(resolve(tmpdir(), 'analyzer-config-graph-'));
  await mkdir(resolve(root, 'configs'));
  await mkdir(resolve(root, 'referenced'));
  await writeFile(resolve(root, 'source.ts'), 'export function target() { return 1; }');
  await writeFile(resolve(root, 'tsconfig.json'), '{"extends":"./configs/base.json","include":["source.ts"],"references":[{"path":"./referenced"}]}');
  await writeFile(resolve(root, 'configs/base.json'), '{"compilerOptions":{"target":"ES2022"}}');
  await writeFile(resolve(root, 'referenced/tsconfig.json'), '{"extends":"../configs/base.json","files":[]}');
  await writeFile(resolve(root, '.eslintrc.json'), '{"extends":"eslint:recommended"}');
  await writeFile(resolve(root, 'data.json'), '{"extends":42,"references":[null,"not-a-project"]}');
  await writeFile(resolve(root, 'unused-tsconfig.json'), '{"extends":"../outside.json"}');
  const programs = await openSnapshot(root, ['tsconfig.json'], root);
  assert.deepEqual(discoverTargets(programs).targets.map(t => t.qualified_name), ['target']);
  await writeFile(resolve(root, 'configs/base.json'), '{"extends":"../../outside.json"}');
  await assert.rejects(openSnapshot(root, ['tsconfig.json'], root), /unsafe_config_reference/);
  await writeFile(resolve(root, 'configs/base.json'), '{}');
  await writeFile(resolve(root, 'referenced/tsconfig.json'), '{"references":[{"path":"../../outside"}]}');
  await assert.rejects(openSnapshot(root, ['tsconfig.json'], root), /unsafe_config_reference/);
  await writeFile(resolve(root, 'referenced/tsconfig.json'), '{"extends":"@unsupported/tsconfig"}');
  await assert.rejects(openSnapshot(root, ['tsconfig.json'], root), /unsafe_config_reference/);
});
test('detects cycles across reachable extends and references before opening a compiler program', async () => {
  const root = await mkdtemp(resolve(tmpdir(), 'analyzer-config-cycle-'));
  await writeFile(resolve(root, 'tsconfig.json'), '{"extends":"./base.json","files":[]}');
  await writeFile(resolve(root, 'base.json'), '{"references":[{"path":"./tsconfig.json"}]}');
  await assert.rejects(openSnapshot(root, ['tsconfig.json'], root), /config_cycle/);
  await writeFile(resolve(root, 'base.json'), '{"references":[{"path":"."}]}');
  await assert.rejects(openSnapshot(root, ['tsconfig.json'], root), /config_cycle/);
});
