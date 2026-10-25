/** Controlled parser inputs, not observations from Docker or dataset evidence.
 * Runtime rows are explicit; neither the parser nor the AST determines their count.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { sha256, instrument } from '../src/instrumentation/transform.js';
import { openSnapshot } from '../src/project.js';
import { parseReport as jest } from '../src/frameworks/jest.js';
import { parseReport as vitest } from '../src/frameworks/vitest.js';

export function frameworkFixture(framework: string, version: string, failure = false): any {
  const base = new URL(`../../../../tests/fixtures/projects/${framework}-basic/`, import.meta.url);
  const name = failure ? 'assertion.case.ts' : 'value.test.ts';
  const source = readFileSync(new URL(name, base), 'utf8');
  const location = (marker: string, occurrence = 0) => {
    let offset = -1;
    for (let i = 0; i <= occurrence; i++) offset = source.indexOf(marker, offset + 1);
    if (offset < 0) throw Error(`Missing fixture marker: ${marker}`);
    const prefix = source.slice(0, offset);
    return {line: prefix.split('\n').length, column: offset - prefix.lastIndexOf('\n')};
  };
  const rows = failure ? [{title: 'assertion failure', status: 'failed', location: location('test(')}] : [
    ...[0, 1, 2, 3].map(() => ({title: 'same', status: 'passed', location: location('test.each')})),
    {title: 'skipped', status: 'skipped', location: location('test.skip')},
    {title: 'todo', status: 'todo', location: location('test.todo')},
    {title: 'expected failure', status: 'passed', location: location(framework === 'jest' ? 'test.failing' : 'test.fails'), failing: true},
    {title: 'same', status: 'passed', location: location("test('same'", 0)},
    {title: 'same', status: 'passed', location: location("test('same'", 1)},
    {title: 'conventional', status: 'passed', location: location("test('conventional'")},
  ];
  const entry = `/workspace/node_modules/jest-circus/${version === '29.7.0' ? 'runner.js' : 'build/runner.js'}`;
  const raw: any = {
    framework, version, compiler_version: '5.8.3',
    transform: framework === 'jest' ? {name: 'ts-jest', version: '29.4.5'} : {name: 'vite', version: '6.1.0'},
    source_hashes: {[name]: sha256(source)},
    configuration: framework === 'jest' ? {
      mode: 'cjs-ts-jest', argv: ['--runInBand'], global: {projects: [], randomize: false},
      projects: [{rootDir: '/workspace', extensionsToTreatAsEsm: [],
        transform: [['^.+\\.tsx?$', '/workspace/node_modules/ts-jest/dist/index.js', {tsconfig: {module: 'CommonJS'}}]],
        testMatch: ['**/*.test.ts'], testRunner: entry,
        runner: {namespace: 'jest-circus/runner', package_name: 'jest-circus', package_version: version,
          package_json_path: '/workspace/node_modules/jest-circus/package.json',
          resolved_path: entry, configured_path: entry, configured_realpath: entry}}],
    } : {mode: 'esm-vite', argv: ['run'], projectCount: 1, pool: 'forks',
      fileParallelism: true, maxWorkers: null, sequence: {}, includeTaskLocation: true},
  };
  if (framework === 'jest') raw.results = {testResults: [{testFilePath: '/workspace/' + name,
    testResults: rows.map(row => ({...row, ancestorTitles: failure ? [] : ['magnitude'], duration: 1}))}]};
  else raw.files = [{filepath: '/workspace/' + name, tasks: [{type: 'suite', name: 'magnitude',
    tasks: rows.map((row, i) => ({type: 'test', id: `controlled-${i}`, name: row.title,
      location: row.location, fails: 'failing' in row && row.failing === true,
      mode: row.status === 'skipped' ? 'skip' : row.status === 'todo' ? 'todo' : 'run',
      result: {state: ({passed: 'pass', failed: 'fail', skipped: 'skip', todo: 'todo'} as Record<string, string>)[row.status], duration: 1}}))}]}];
  return {raw, sources: {[name]: source}};
}

// Python tests use the same explicit controlled input and the real static parser.
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [framework, version, mode] = process.argv.slice(2);
  if (framework !== 'jest' && framework !== 'vitest') throw Error('Unknown fixture framework');
  const value = frameworkFixture(framework, version, mode === 'failure');
  if (mode === 'serial') Object.assign(value.raw.configuration, {fileParallelism: false, maxWorkers: 1});
  value.normalized = (framework === 'jest' ? jest : vitest)(value.raw, value.sources, sha256(JSON.stringify(value.raw)));
  if (mode === 'serial') {
    const root = fileURLToPath(new URL(`../../../../tests/fixtures/projects/${framework}-basic/`, import.meta.url));
    const programs = await openSnapshot(root, ['tsconfig.json'], root);
    value.manifest = instrument(programs, framework, version);
    value.sources = Object.fromEntries(programs.flatMap(p => [...p.texts]));
  }
  process.stdout.write(JSON.stringify(value));
}
