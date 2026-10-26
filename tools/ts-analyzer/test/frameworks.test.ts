import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
import { frameworkFixture } from './framework-fixtures.js';

const root = '../../../../tests/fixtures/';
const fixture = (path: string) => readFileSync(new URL(root + path, import.meta.url), 'utf8');
const nodeRequire = createRequire(import.meta.url);

for (const version of ['3.2.4', '4.0.0']) {
  test(`Vitest ${version} reporter captures resolved selectors without changing T10 decisions`, async () => {
    const {parseReport} = await import('../src/frameworks/vitest.js');
    const {raw} = frameworkFixture('vitest', version);
    const source = fixture('projects/vitest-basic/value.test.ts');
    const sources = {'value.test.ts': source};
    const legacy = parseReport(raw, sources, 'a'.repeat(64));
    const reporterSource = readFileSync(new URL('../../src/frameworks/reporters/vitest.mjs', import.meta.url), 'utf8')
      .replace(/^import .+ from .+;$/gm, '')
      .replace('export default class Reporter', 'module.exports = class Reporter');
    const selectors = ['shard', 'changed', 'related', 'project', 'filters', 'cliExclude', 'standalone', 'mergeReports'];
    function sandbox(selection: Record<string, unknown>) {
      let emitted: any;
      const module = {exports: undefined as any};
      const req = (name: string) => ({
        './package.json': {type: 'module'}, 'vitest/package.json': {version},
        'typescript/package.json': {version: '5.8.3'}, 'vite/package.json': {version: '6.1.0'},
      }[name]);
      runInNewContext(reporterSource, {module, process: {argv: ['node', 'vitest', 'run']},
        createRequire: () => req, createHash,
        fs: {readFileSync: () => Buffer.from(source), mkdirSync: () => {},
          writeFileSync: (_path: string, content: string) => { emitted = JSON.parse(content); }},
      });
      const reporter = new module.exports();
      reporter.onInit({projects: [{}], config: {...raw.configuration, include: ['*.test.ts'], exclude: ['vendor/**'],
        includeTaskLocation: true, ...selection}});
      reporter.onTestRunEnd(raw.files.map((task: unknown) => ({task})), [], 'passed');
      return emitted;
    }
    const absent = sandbox({});
    for (const key of selectors) {
      assert.ok(Object.hasOwn(absent.configuration, key), key);
      assert.equal(absent.configuration[key], null, key);
    }
    const active = {shard: {index: 1, count: 2}, changed: 'HEAD', related: ['value.ts'], project: ['unit'],
      filters: ['value.test.ts'], cliExclude: ['other.test.ts'], standalone: true, mergeReports: './reports'};
    const observed = sandbox(active);
    for (const key of selectors) assert.deepEqual(observed.configuration[key], active[key as keyof typeof active], key);
    assert.deepEqual(observed.configuration.include, ['*.test.ts']);
    assert.deepEqual(observed.configuration.exclude, ['vendor/**']);
    for (const envelope of [absent, observed]) {
      const report = parseReport(envelope, sources, 'a'.repeat(64));
      assert.deepEqual(report.cases, legacy.cases);
      assert.deepEqual(report.support, legacy.support);
      assert.equal(report.completeness, 'not_evaluated');
      assert.equal(report.scope, 'unknown');
    }
  });

  test(`Vitest ${version} result support does not imply serial trace support`, async () => {
    const {parseReport} = await import('../src/frameworks/vitest.js');
    const {raw} = frameworkFixture('vitest', version);
    const sources = {'value.test.ts': fixture('projects/vitest-basic/value.test.ts')};
    const serial = {...raw, configuration: {...raw.configuration, fileParallelism: false, maxWorkers: 1}};
    assert.equal(parseReport(serial, sources, 'a'.repeat(64)).trace_support.status, 'tested');
    for (const changes of [
      {fileParallelism: true, maxWorkers: 2}, {fileParallelism: true, maxWorkers: 1},
      {fileParallelism: false, maxWorkers: 2}, {fileParallelism: null}, {maxWorkers: null},
      {fileParallelism: 'false'}, {maxWorkers: '1'}, {maxWorkers: true},
      {fileParallelism: undefined}, {maxWorkers: undefined},
    ]) {
      const report = parseReport({...serial, configuration: {...serial.configuration, ...changes}}, sources, 'a'.repeat(64));
      assert.equal(report.support.status, 'tested'); // T10 result parsing remains supported.
      assert.equal(report.cases.filter(c => c.status === 'passed' && !c.expected_failure && c.mapping === 'trusted').length, 7);
      assert.equal(report.trace_support.status, 'not_evaluated', JSON.stringify(changes));
    }
  });
}

for (const version of ['29.7.0', '30.0.0']) {
  test(`Jest ${version} requires exact resolved circus runner evidence, including legacy raw reports`, async () => {
    const {parseReport} = await import('../src/frameworks/jest.js');
    const source = fixture('projects/jest-basic/value.test.ts');
    const {raw} = frameworkFixture('jest', version);
    const sources = {'value.test.ts': source};
    const legacy = structuredClone(raw);
    for (const project of legacy.configuration.projects) {
      delete project.testRunner;
      delete project.runner;
    }
    const untrusted = (value: unknown) => {
      const report = parseReport(value, sources, 'a'.repeat(64));
      assert.equal(report.support.status, 'not_evaluated');
      assert.ok(report.cases.every(c => c.mapping === 'not_evaluated'));
      assert.equal(report.cases.filter(c => c.status === 'passed' && !c.expected_failure && c.mapping === 'trusted').length, 0);
    };
    untrusted(legacy);

    const circus = '/workspace/node_modules/jest-circus/' + (version === '29.7.0' ? 'runner.js' : 'build/runner.js');
    const packageJson = '/workspace/node_modules/jest-circus/package.json';
    for (const testRunner of [circus, '/workspace/custom/jest-circus/build/runner.js']) {
      const oldRaw = structuredClone(legacy);
      oldRaw.configuration.projects[0].testRunner = testRunner;
      untrusted(oldRaw); // A path/name alone is not resolved package evidence.
    }
    const reporterSource = readFileSync(new URL('../../src/frameworks/reporters/jest.cjs', import.meta.url), 'utf8');
    function sandbox(configuredPath: string) {
      let emitted: any;
      const req = Object.assign((name: string) => ({
        'jest/package.json': {version}, 'typescript/package.json': {version: '5.8.3'},
        'ts-jest/package.json': {version: '29.4.5'}, 'jest-circus/package.json': {name: 'jest-circus', version},
      }[name]), {resolve: (name: string) => {
        if (name === 'jest-circus/runner') return circus;
        if (name === 'jest-circus/package.json') return packageJson;
        throw Error('unexpected resolution');
      }});
      const module = {exports: undefined as any};
      runInNewContext(reporterSource, {module, process: {argv: ['node', 'jest', '--runInBand']},
        require: (name: string) => name === 'node:fs' ? {
          realpathSync: (path: string) => path,
          readFileSync: () => Buffer.from(source), mkdirSync: () => {},
          writeFileSync: (_path: string, content: string) => { emitted = JSON.parse(content); },
        } : name === 'node:module' ? {createRequire: () => req} : nodeRequire(name),
      });
      const project = {...structuredClone(raw.configuration.projects[0]), testRunner: configuredPath};
      new module.exports(raw.configuration.global).onRunComplete(new Set([{config: project}]), raw.results);
      return emitted;
    }
    const observed = sandbox(circus);
    const project = observed.configuration.projects[0];
    assert.equal(project.testRunner, circus);
    assert.equal(project.runner.namespace, 'jest-circus/runner');
    assert.equal(project.runner.package_name, 'jest-circus');
    assert.equal(project.runner.package_version, version);
    assert.equal(project.runner.package_json_path, packageJson);
    assert.equal(project.runner.resolved_path, circus);
    assert.equal(parseReport(observed, sources, 'a'.repeat(64)).support.status, 'tested');
    const custom = sandbox('/workspace/custom/jest-circus/build/runner.js');
    assert.equal(custom.configuration.mode, 'unimplemented');
    custom.configuration.mode = 'cjs-ts-jest'; // Parser must independently enforce the gate.
    untrusted(custom);
    const wrongConfigPath = structuredClone(observed);
    wrongConfigPath.configuration.projects[0].testRunner = '/workspace/custom/runner.js';
    untrusted(wrongConfigPath);
    const wrongEntry = structuredClone(observed);
    const alternate = '/workspace/node_modules/jest-circus/' + (version === '29.7.0' ? 'build/runner.js' : 'runner.js');
    wrongEntry.configuration.projects[0].testRunner = alternate;
    Object.assign(wrongEntry.configuration.projects[0].runner, {
      configured_path: alternate, configured_realpath: alternate, resolved_path: alternate,
    });
    untrusted(wrongEntry);
    for (const changes of [
      {namespace: 'custom/jest-circus/runner'}, {package_name: 'custom-jest-circus'},
      {package_version: '99.0.0'}, {resolved_path: '/workspace/custom/jest-circus/build/runner.js'},
      {package_json_path: '/workspace/custom/package.json'},
    ]) {
      const forged = structuredClone(observed);
      Object.assign(forged.configuration.projects[0].runner, changes);
      untrusted(forged);
    }
  });
}

for (const [framework, version] of [['jest', '29.7.0'], ['jest', '30.0.0'], ['vitest', '3.2.4'], ['vitest', '4.0.0']]) {
  test(`normalizes controlled ${framework} ${version} runtime occurrences and exclusions`, async () => {
    const adapter = await import(`../src/frameworks/${framework}.js`);
    assert.equal(typeof adapter.parseReport, 'function');
    const source = fixture(`projects/${framework}-basic/value.test.ts`);
    const {raw} = frameworkFixture(framework, version);
    const emitted: unknown[] = [];
    const report = adapter.parseReport(raw, {'value.test.ts': source}, 'a'.repeat(64), (item: unknown) => emitted.push(item));
    assert.deepEqual(emitted, report.cases);
    assert.equal(report.schema_version, '1.0');
    assert.equal(report.support.status, 'tested', JSON.stringify(raw.configuration));
    assert.equal(report.cases.length, 10);
    assert.equal(report.cases.filter((c: any) => c.status === 'passed' && !c.expected_failure && c.mapping === 'trusted').length, 7);
    assert.equal(report.cases.filter((c: any) => c.expected_failure).length, 1);
    assert.equal(report.cases.filter((c: any) => ['pending', 'skipped', 'todo'].includes(c.status)).length, 2);
    assert.ok(report.cases.every((c: any) => c.mapping === 'trusted'));
    const rows = report.cases.slice(0, 4);
    assert.deepEqual(rows.map((c: any) => c.parameter_index), [0, 1, 2, 3]);
    assert.equal(new Set(report.cases.map((c: any) => c.identity)).size, 10);
    assert.equal(rows[0].declaration_position, source.indexOf('test.each'));
    assert.equal(report.compiler_version, '5.8.3');
    assert.equal(report.completeness, 'not_evaluated');
    const unmapped = adapter.parseReport(raw, {}, 'a'.repeat(64));
    assert.ok(unmapped.cases.every((c: any) => c.mapping === 'not_evaluated' && c.declaration_position === null));
    assert.throws(() => adapter.parseReport({...raw, version: '99.0.0'}, {}, 'a'.repeat(64)), /version/);
    const exotic = adapter.parseReport({...raw, configuration: {...raw.configuration, mode: 'exotic'}}, {'value.test.ts': source}, 'a'.repeat(64));
    assert.equal(exotic.support.status, 'not_evaluated');
    assert.ok(exotic.cases.every((c: any) => c.mapping === 'not_evaluated'));
    assert.equal(exotic.cases.filter((c: any) => c.expected_failure).length, 1);
    if (framework === 'vitest') {
      const workspace = adapter.parseReport({...raw, configuration: {...raw.configuration, projectCount: 2}}, {'value.test.ts': source}, 'a'.repeat(64));
      assert.equal(workspace.support.status, 'not_evaluated');
      const concurrentConfig = adapter.parseReport({...raw, configuration: {...raw.configuration, sequence: {concurrent: true}}}, {'value.test.ts': source}, 'a'.repeat(64));
      assert.equal(concurrentConfig.support.status, 'not_evaluated');
    }
    // Runtime rows, not static table rows, determine the number of occurrences.
    const reduced = structuredClone(raw);
    if (framework === 'jest') reduced.results.testResults[0].testResults.splice(0, 1);
    else reduced.files[0].tasks[0].tasks.splice(0, 1);
    assert.equal(adapter.parseReport(reduced, {'value.test.ts': source}, 'a'.repeat(64)).cases.length, 9);
    const failed = adapter.parseReport(frameworkFixture(framework, version, true).raw,
      {'assertion.case.ts': fixture(`projects/${framework}-basic/assertion.case.ts`)}, 'a'.repeat(64));
    assert.equal(failed.cases.length, 1);
    assert.equal(failed.cases[0].status, 'failed');
    assert.equal(failed.cases[0].expected_failure, false);
  });
}

test('runtime location mapping does not invent positions for missing/ambiguous declarations', async () => {
  const {parseReport} = await import('../src/frameworks/vitest.js');
  const {raw} = frameworkFixture('vitest', '4.0.0');
  const source = fixture('projects/vitest-basic/value.test.ts');
  const changed = source.replace('Astral prefix', 'Changed bytes');
  assert.ok(parseReport(raw, {'value.test.ts': changed}, 'a'.repeat(64)).cases.every(c => c.mapping === 'not_evaluated'));
  const altered = structuredClone(raw);
  delete altered.files[0].tasks[0].tasks[0].location;
  assert.equal(parseReport(altered, {'value.test.ts': source}, 'a'.repeat(64)).cases[0].mapping, 'not_evaluated');
  const concurrent = source.replace('test.each', 'test.concurrent.each');
  const concurrentRaw = {...raw, source_hashes: {'value.test.ts': createHash('sha256').update(concurrent).digest('hex')}};
  assert.equal(parseReport(concurrentRaw, {'value.test.ts': concurrent}, 'a'.repeat(64)).cases[0].mapping, 'not_evaluated');
  // Changed locations are not a source map. Prefix affects line numbers; do not repair by title matching.
  assert.equal(parseReport(raw, {'value.test.ts': '\n' + source}, 'a'.repeat(64)).cases[0].mapping, 'not_evaluated');
  assert.throws(() => parseReport({...raw, files: [{...raw.files[0], filepath: '/etc/passwd'}]}, {}, 'a'.repeat(64)), /workspace/);
  assert.throws(() => parseReport({...raw, files: [{...raw.files[0], filepath: '/workspace/../a'}]}, {}, 'a'.repeat(64)), /unsafe/);
  const inheritedConcurrency = source.replace("describe('magnitude'", "describe.concurrent('magnitude'");
  const inheritedRaw = {...raw, source_hashes: {'value.test.ts': createHash('sha256').update(inheritedConcurrency).digest('hex')}};
  assert.ok(parseReport(inheritedRaw, {'value.test.ts': inheritedConcurrency}, 'a'.repeat(64)).cases.every(c => c.mapping === 'not_evaluated'));
});

test('Jest case IDs do not depend on cross-file completion order', async () => {
  const {parseReport} = await import('../src/frameworks/jest.js');
  const {raw} = frameworkFixture('jest', '30.0.0');
  const source = fixture('projects/jest-basic/value.test.ts');
  const first = parseReport(raw, {'value.test.ts': source}, 'a'.repeat(64));
  const other = {...structuredClone(raw.results.testResults[0]), testFilePath: '/workspace/other.test.ts'};
  raw.results.testResults.unshift(other);
  const reordered = parseReport(raw, {'value.test.ts': source, 'other.test.ts': source}, 'a'.repeat(64));
  assert.deepEqual(reordered.cases.filter(c => c.file === 'value.test.ts').map(c => c.identity), first.cases.map(c => c.identity));
});
