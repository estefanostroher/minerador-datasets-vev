import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { analyzeResources } from '../validation-resources.mjs';
import { analyzeResourceClosure } from '../validation-resource-closure.mjs';

const models = JSON.parse(fs.readFileSync(new URL('../../validation_resource_models.json', import.meta.url)));
function payload(texts, roots = ['test.ts']) {
  return { files: Object.entries(texts).map(([path, content]) => ({ path, content })),
    inventory_paths: Object.keys(texts), roots, configuration_paths: [], loaded_paths: roots, models, vitest_bound: true };
}

test('remapeia js/mjs/cjs para fonte original sem escolher ambiguidades ou escapar da raiz', () => {
  for (const [extension, source] of [['js', 'ts'], ['js', 'tsx'], ['mjs', 'mts'], ['cjs', 'cts']]) {
    const input = payload({ 'test.ts': `import {value} from './value.${extension}'; const result=value;`,
      [`value.${source}`]: 'export const value=1;' });
    assert.equal(analyzeResources(input).complete, true);
    assert.equal(analyzeResourceClosure(input).complete, true);
  }
  for (const texts of [
    { 'test.ts': "import './value.js';", 'value.ts': '', 'value.tsx': '' },
    { 'test.ts': "import '../outside.js';", '../outside.ts': '' },
    { 'test.ts': "import './value.mjs';", 'value.ts': '' },
  ]) assert.equal(analyzeResourceClosure(payload(texts)).complete, false);
});

test('apaga tipos sem fechar imports exclusivos de tipos nem apagar inicializadores executáveis', () => {
  const input = payload({ 'test.ts': "import type {Missing} from 'opaque'; import {type Shape, value as alias} from './value.js'; export type T=Missing; export interface I {x: T}; const n=alias as number;",
    'value.ts': 'export type Shape={x:number}; export const value=1;' });
  assert.equal(analyzeResources(input).complete, true);
  const closure = analyzeResourceClosure(input);
  assert.equal(closure.complete, true);
  assert.equal(closure.edges.some(edge => edge.specifier === 'opaque'), false);
  // O binding inline é apagado, mas import {type X} preserva carregamento no modo verbatim.
  assert.equal(analyzeResourceClosure(payload({ 'test.ts': "import {type Shape} from 'opaque';" })).complete, false);
  for (const source of ["const value=eval('1') as number;", 'class C { static field=eval("1"); }',
    "import * as unknown from './value.js'; unknown.run();"]) {
    assert.equal(analyzeResources(payload({ 'test.ts': source, 'value.ts': 'export const value=1;' })).complete, false);
  }
});

test('exports/imports declarativos não são efeitos, mas resolução usada sem modelo continua aberta', () => {
  const texts = { 'test.ts': "import {value} from './value.js';", 'value.ts': 'export const value=1;',
    'package.json': JSON.stringify({ type: 'module', exports: { '.': { development: './value.ts', default: './dist.js' } }, imports: { '#value': './value.ts' } }) };
  assert.equal(analyzeResourceClosure(payload(texts)).complete, true);
  for (const specifier of ['#value', 'self-package', '@alias/value']) {
    assert.equal(analyzeResourceClosure(payload({ ...texts, 'test.ts': `import '${specifier}';` })).complete, false);
  }
  assert.equal(analyzeResources(payload({ 'package.json': '{"scripts":{"pretest":"node effect.js"}}' }, ['package.json'])).complete, false);
});

test('assertions Node específicas aceitam só dados e preservam operações opacas nos argumentos', () => {
  const good = "import assert from 'node:assert/strict'; import {equal as same} from 'node:assert/strict'; assert.equal(1,1); assert.deepEqual([1],[1]); same('a','a'); assert.ok(true);";
  assert.equal(analyzeResources(payload({ 'test.ts': good })).complete, true);
  for (const source of ["import assert from 'node:assert/strict'; assert.throws(()=>eval('1'));",
    "import assert from 'node:assert/strict'; assert.equal(eval('1'),1);",
    "import assert from 'node:assert/strict'; assert.custom(1);"]) {
    assert.equal(analyzeResources(payload({ 'test.ts': source })).complete, false);
  }
});

test('filesystem assíncrono só permite temporário de proveniência fechada', () => {
  const prefix = "import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises'; import {tmpdir} from 'node:os'; import {join} from 'node:path';";
  const source = prefix + "const dir=await mkdtemp(join(tmpdir(),'fixture-')); await writeFile(join(dir,'data'),'ok'); await readFile(join(dir,'data'),'utf8'); await rm(dir,{recursive:true});";
  assert.equal(analyzeResources(payload({ 'test.ts': source })).complete, true);
  for (const operation of ["await readFile('/workspace/config.ts','utf8');", "await readFile(unknown);", "await mkdtemp('/tmp/opaque-');"]) {
    assert.equal(analyzeResources(payload({ 'test.ts': prefix + operation })).complete, false);
  }
});

test('reexports fecham transitivos e não escondem colisões, tipos ou código ausente', () => {
  const texts = { 'test.ts': "import {alias} from './barrel.js'; const n=alias;",
    'barrel.ts': "export {value as alias, type Shape} from './value.js'; export type {Missing} from 'opaque';",
    'value.ts': 'export type Shape=number; export const value=1;' };
  assert.equal(analyzeResourceClosure(payload(texts)).complete, true);
  assert.equal(analyzeResources(payload(texts)).complete, true);
  const missing = payload({ 'test.ts': "import './missing.js';" });
  missing.inventory_paths.push('missing.ts');
  assert.equal(analyzeResources(missing).complete, false);
  const collision = { 'test.ts': "import './barrel.js';", 'barrel.ts': "export * from './a.js'; export * from './b.js';",
    'a.ts': 'export const value=1;', 'b.ts': 'export const value=2;' };
  assert.equal(analyzeResourceClosure(payload(collision)).complete, false);
});

test('shorthand só transporta dados; métodos sobre parâmetros opacos continuam desconhecidos', () => {
  assert.equal(analyzeResources(payload({ 'test.ts': 'const value=1; export const record={value};' })).complete, true);
  for (const source of ['const method=()=>1; const record={method};',
    'export const compute=(input: number[])=>input.map(x=>x+1);',
    'export const compute=(input: unknown)=>new Date(input as string);']) {
    assert.equal(analyzeResources(payload({ 'test.ts': source })).complete, false);
  }
});

test('assert.match só admite regexp literal e dados, sem executar callbacks ou métodos candidatos', () => {
  assert.equal(analyzeResources(payload({ 'test.ts': "import assert from 'node:assert/strict'; assert.match('fixture', /fix/); assert.doesNotMatch('fixture', /other/);" })).complete, true);
  for (const expression of ["assert.match('x',{test:()=>true});", "assert.match(eval('x'), /x/);", "assert.match('x', unknown);"]) {
    assert.equal(analyzeResources(payload({ 'test.ts': "import assert from 'node:assert/strict';" + expression })).complete, false);
  }
});

test('diagnóstico aponta a operação desconhecida, não apenas seu receptor', () => {
  const result = analyzeResources(payload({ 'test.ts': '\nexport const compute=(input: number[])=>input.map(x=>x+1);' }));
  const detail = result.files[0].limitation_details[0];
  assert.equal(detail.start_line, 2);
  assert.equal(detail.expression, 'input.map');
});

test('leitura JSON original tem aresta e bytes fechados, sem liberar código/config opacos', () => {
  const source = "import {readFileSync} from 'node:fs'; const vectors=JSON.parse(readFileSync(new URL('./fixtures/vectors.json',import.meta.url),'utf8'));";
  const input = payload({ 'test.ts': source, 'fixtures/vectors.json': '{"values":[1,2]}' });
  const result = analyzeResourceClosure(input);
  assert.equal(result.complete, true);
  assert.ok(result.edges.some(edge => edge.kind === 'snapshot_data' && edge.path === 'fixtures/vectors.json'));
  assert.ok(result.closure_paths.includes('fixtures/vectors.json'));
  for (const [name, text] of [['fixtures/vectors.json', 'invalid'], ['fixtures/code.ts', 'export const value=1;']]) {
    const bad = payload({ 'test.ts': source.replace('./fixtures/vectors.json', './' + name), [name]: text });
    assert.equal(analyzeResourceClosure(bad).complete, false);
  }
  assert.equal(analyzeResourceClosure(payload({ 'test.ts': source })).complete, false);
  assert.equal(analyzeResources(payload({ 'test.ts': "import {writeFileSync} from 'node:fs'; writeFileSync(new URL('./data.json',import.meta.url),'{}');", 'data.json': '{}' })).complete, false);
  assert.equal(analyzeResources(payload({ 'test.ts': source.replace('./fixtures/vectors.json', './%2e%2e/outside.json'), '%2e%2e/outside.json': '{}' })).complete, false);
});

test('configuração NodeNext/Bundler e aliases não usados permanecem no fechamento', () => {
  const texts = { 'test.ts': "import './value.js';", 'value.ts': 'export const value=1;',
    'tsconfig.json': '{"extends":"./tsconfig.base.json","compilerOptions":{"paths":{"@alias/*":["./src/*"]}}}',
    'tsconfig.base.json': '{"compilerOptions":{"moduleResolution":"NodeNext","customConditions":["development"]}}' };
  const result = analyzeResourceClosure(payload(texts));
  assert.equal(result.complete, true);
  assert.ok(result.closure_paths.includes('tsconfig.base.json'));
  assert.equal(analyzeResourceClosure(payload({ ...texts, 'test.ts': "import '@alias/value';" })).complete, false);
  assert.equal(analyzeResourceClosure(payload({ ...texts, 'tsconfig.base.json': '{"compilerOptions":{"moduleSuffixes":[".custom",""]}}' })).complete, false);
});
