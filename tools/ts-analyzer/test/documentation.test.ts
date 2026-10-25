import assert from 'node:assert/strict';
import { test } from 'node:test';
import { Project } from 'ts-morph';
import { discoverTargets } from '../src/targets.js';
import { discoverDocumentation } from '../src/documentation.js';

function analyze(code: string, markdown = '', name = 'f') {
  const project = new Project({ useInMemoryFileSystem: true });
  const file = project.createSourceFile('/snapshot/src/a.ts', code);
  const programs = [{ project, root: '/unused', sourceFiles: [file], texts: new Map([['src/a.ts', code], ['docs/api.md', markdown]]) }];
  const targets = discoverTargets(programs).targets;
  return discoverDocumentation(programs, targets).find(d => targets.find(t => t.id === d.symbol_target_id)?.qualified_name === name)!;
}
test('preserves behavioral JSDoc, association, UTF16 offsets and indicative excerpts', () => {
  const code = '// 😀\r\n/** Returns the absolute input.\r\n * @param x input number\r\n * @throws Error for invalid input\r\n */\r\nexport function f(x:number){return Math.abs(x);}';
  const result = analyze(code);
  assert.equal(result.status, 'approved');
  const source = result.sources[0];
  assert.equal(source.text, code.slice(source.start_offset, source.end_offset));
  assert.equal(source.association, 'adjacent_jsdoc');
  assert.equal(source.path, 'src/a.ts');
  assert.ok(source.markers.some(m => m.kind === 'errors' && m.text.includes('@throws')));
});
test('absence, signature-only and semantically generic text are distinct', () => {
  assert.equal(analyze('function f(){}').reason, 'documentation_absent');
  assert.equal(analyze('/** @param {number} x */ function f(x:number){}').status, 'rejected');
  const generic = analyze('/** Helper function */ function f(){}');
  assert.equal(generic.status, 'not_evaluated');
  assert.equal(generic.reason, 'documentation_behavior_unresolved');
  assert.equal(generic.sources[0].text, '/** Helper function */');
  for (const text of ['Returns {number}.', 'Returns Promise<number>.', 'Returns Foo.', 'Returns a number.', 'f(x: number): number']) {
    assert.notEqual(analyze('/** ' + text + ' */ function f(){}').status, 'approved');
  }
});
test('Markdown requires one explicit path AND qualified symbol binding per section', () => {
  const code = 'function f(x:number){return x+1;}';
  const markdown = '# API\r\n\r\n## [f](../src/a.ts#f)\r\nReturns the incremented input. 😀\r\n\r\n## Other\r\nUnrelated text.';
  const result = analyze(code, markdown);
  assert.equal(result.status, 'approved');
  const source = result.sources[0];
  assert.equal(source.association, 'markdown_path_symbol');
  assert.equal(source.text, markdown.slice(source.start_offset, source.end_offset));
  assert.ok(!source.text.includes('Unrelated'));
  for (const link of ['[f](../src/a.ts)', 'f', '[f](../../outside.ts#f)', '[f](https://example.com/src/a.ts#f)', '[f](../src/b.ts#f)', '[f](../src/a.ts#g)']) {
    assert.equal(analyze(code, '## ' + link + '\nReturns the incremented input.').reason, 'documentation_absent');
  }
});
for (const [verb, article] of [
  ['Returns', 'a'], ['Returns', 'an'], ['Returns', 'the'],
  ['Retorna', 'um'], ['Retorna', 'uma'], ['Retorna', 'o'], ['Retorna', 'a'],
]) test(`type-only descriptions with article ${verb} ${article} remain unresolved`, () => {
  for (const type of [
    'Promise<number>', 'Array<string>', 'ReadonlyArray<number>', 'Map<string, number>',
    'Promise<Array<string | null>>', 'number[]', '[number, string]',
    'string | undefined', 'Foo & Bar', '{ value: number }', '(number | null)',
    '() => number', 'Record<string, unknown>', 'Readonly<{ value: number }>',
  ]) {
    const doc = `/** ${verb} ${article} ${type}. */`;
    const code = `// 😀\r\n${doc}\r\nfunction f():${type}{throw Error();}`;
    const result = analyze(code);
    assert.equal(result.status, 'not_evaluated', doc);
    assert.equal(result.reason, 'documentation_behavior_unresolved', doc);
    const source = result.sources[0];
    assert.equal(source.text, doc);
    assert.equal(source.text, code.slice(source.start_offset, source.end_offset));
    assert.deepEqual(result.evidence, [`src/a.ts:${source.start_offset}:${source.end_offset}`]);
    assert.equal(source.association, 'adjacent_jsdoc');
  }
});
test('type wrappers and trailing punctuation do not establish behavior', () => {
  for (const description of [
    'Returns a `Promise<number>`.', 'Returns the (Promise<number>)!',
    'Returns an {Array<string>};', 'Returns a Promise<number>...',
    'Retorna uma Promise<number>.', 'Returns a Promise<number>.\n',
  ]) {
    assert.equal(analyze(`/** ${description} */ function f(){}`).status, 'not_evaluated', description);
  }
  const result = analyze('/** Returns a Promise<number>. */ function f():Promise<number>{return Promise.resolve(1)}');
  assert.equal(result.status, 'not_evaluated');
});
test('articles and type mentions retain genuine behavioral descriptions', () => {
  for (const description of [
    'Returns a normalized value for the input.',
    'Returns an array sorted by ascending input values.',
    'Returns the input incremented by one.',
    'Returns a Promise<number> resolving to the sum of the inputs.',
    'Retorna um valor normalizado para a entrada.',
    'Retorna uma lista ordenada pelos valores de entrada.',
  ]) {
    const doc = `/** ${description} */`;
    const result = analyze(`${doc} function f(){}`);
    assert.equal(result.status, 'approved', description);
    assert.equal(result.sources[0].text, doc);
  }
});
test('methods and overload-attached JSDoc retain explicit original association', () => {
  assert.equal(analyze('class C {\n/** Returns the incremented input. */\nm(x:number){return x+1;} }', '', 'C.m').status, 'approved');
  const overloaded = analyze('/** Returns the incremented input. */ function f(x:number):number;\nfunction f(x:number){return x+1;}');
  assert.equal(overloaded.status, 'approved');
  assert.equal(overloaded.sources[0].association, 'overload_jsdoc');
});
test('code-only Markdown is not behavioral prose and parent sections cannot lend another symbol prose', () => {
  const code = 'function f(){}';
  const codeOnly = analyze(code, '## [f](../src/a.ts#f)\n```ts\n// Returns the incremented input.\n```');
  assert.notEqual(codeOnly.status, 'approved');
  const nested = analyze(code, '## [f](../src/a.ts#f)\nHelper function\n### [g](../src/a.ts#g)\nReturns the incremented input.');
  assert.notEqual(nested.status, 'approved');
});
