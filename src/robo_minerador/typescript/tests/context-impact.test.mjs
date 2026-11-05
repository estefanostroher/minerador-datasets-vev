import test from 'node:test';
import assert from 'node:assert/strict';
import { analyze } from '../analyze.mjs';

const implementation = '/** Incrementa a entrada. */\nexport function increment(x:number) { return x + 1; }';
const localTest = "import{test}from'vitest';import{increment}from'./a';test('local',()=>increment(1));";
const absent = "import missing from './absent';export const unrelated=missing;";

/** Analisa apenas textos virtuais e retorna a unidade alvo e o diagnóstico integral. */
function run(extra = {}, issues = []) {
  const texts = { 'src/a.ts': implementation, 'src/a.test.ts': localTest, ...extra };
  const result = analyze({ files: Object.entries(texts).map(/** Monta entrada sem acesso ao disco candidato. */ ([path, content]) => ({ path, content })), issues });
  return { result, unit: result.instances.find(/** Seleciona a função original por caminho. */ item => item.function.path === 'src/a.ts') };
}

test('import ausente independente permanece auditável sem bloquear evidência positiva', /** Reproduz o bloqueio global original. */ () => {
  const { result, unit } = run({ 'other/b.ts': absent });
  assert.equal(unit.decision, 'approved');
  assert.ok(result.unresolved_references.some(/** Confere preservação da referência ausente. */ ref => ref.from === 'other/b.ts'));
  assert.ok(result.issues.some(/** Confere preservação do diagnóstico. */ issue => issue.path === 'other/b.ts'));
});

test('dependências diretas e transitivas de implementação e teste bloqueiam contexto', /** Impede promoção quando o import ausente alcança uma raiz real. */ () => {
  for (const root of ['src/a.ts', 'src/a.test.ts']) {
    for (const prefix of [absent, "import './bridge';"]) {
      const { unit } = run({ [root]: prefix + '\n' + (root.endsWith('.test.ts') ? localTest : implementation), 'src/bridge.ts': absent });
      assert.equal(unit.criteria.preserved_local_context.status, 'not_evaluated');
      assert.equal(unit.criteria.associated_documentation.status, 'approved');
    }
  }
});

test('ciclos e reexports preservados terminam e permitem contexto completo', /** Percorre o fechamento sem recursão ilimitada. */ () => {
  const { unit } = run({ 'src/a.ts': "import './bridge';\n" + implementation, 'src/bridge.ts': "export * from './cycle';", 'src/cycle.ts': "export * from './bridge';" });
  assert.equal(unit.decision, 'approved');
});

test('triple slash, ambient, fonte inválida e configuração extends mantêm incerteza', /** Não confunde distância lexical com independência semântica. */ () => {
  for (const extra of [
    { 'src/a.ts': '/// <reference path="./missing.d.ts" />\n' + implementation },
    { 'other/globals.d.ts': '/// <reference path="./missing.d.ts" />\ndeclare const shared: number;' },
    { 'other/b.ts': "import './absent'; declare global { interface Window { changed: number } } export {};" },
    { 'other/b.ts': "import './absent'; export function broken( {" },
    { 'tsconfig.json': '{"extends":"./missing.json"}' },
    { 'tsconfig.json': '{"extends":"./base.json"}', 'base.json': '{"extends":"./missing.json"}' },
    { 'src/a.ts': '/// <reference types="missing" />\n' + implementation },
    { 'tsconfig.json': '{"compilerOptions":{"types":["missing"]}}' },
    { 'src/a.ts': "import '#internal';\n" + implementation, 'package.json': '{"imports":{"#internal":"./missing.ts"}}' },
    { 'src/a.ts': "import 'local-package/missing';\n" + implementation, 'package.json': '{"name":"local-package"}' },
    { 'tsconfig.json': '{"references":[{"path":"./missing"}]}' },
  ]) assert.equal(run(extra).unit.criteria.preserved_local_context.status, 'not_evaluated', JSON.stringify(extra));
});

test('extends preservado publica a configuração transitiva sem criar pendência artificial', /** Confere que a raiz efetiva carrega o grafo das configurações realmente lidas. */ () => {
  const { result, unit } = run({ 'tsconfig.json': '{"extends":"./base.json"}', 'base.json': '{"extends":"./ancestor.json"}', 'ancestor.json': '{"compilerOptions":{"strict":true}}' });
  assert.equal(unit.decision, 'approved');
  for (const target of ['base.json', 'ancestor.json']) assert.ok(result.local_dependencies.some(/** Exige a preservação de cada configuração lida na cadeia efetiva. */ dependency => dependency.from === 'tsconfig.json' && dependency.resolved_path === target));
});

test('omissões e issues desconhecidas impedem concluir ausência e contexto', /** Protege testes, documentos, configurações ancestrais e globais omitidos. */ () => {
  for (const path of ['other/source.ts', 'other/guide.md', 'other/check.test.ts', 'tsconfig.json', 'other/fixture.png', undefined]) {
    const issue = { code: 'OMITTED_CONTEXT', message: 'Arquivo indisponível.', ...(path ? { path } : {}) };
    const { unit } = run({ 'src/a.ts': implementation.split('\n')[1], 'src/a.test.ts': 'export {};' }, [issue]);
    assert.equal(unit.criteria.associated_documentation.status, 'not_evaluated');
    assert.equal(unit.criteria.direct_test_association.status, 'not_evaluated');
    assert.equal(unit.criteria.preserved_local_context.status, 'not_evaluated');
  }
});

test('recursos e imports dinâmicos relacionados continuam pendentes', /** Não descarta fixtures nem acessos de alcance desconhecido. */ () => {
  for (const access of ["import './fixture.png';", "import('other/' + name);", "require(name);", "readFileSync(name);", "new URL('./fixture.png', import.meta.url);"]) {
    assert.equal(run({ 'src/a.test.ts': access + localTest }).unit.criteria.preserved_local_context.status, 'not_evaluated', access);
  }
});

test('incerteza em teste independente localiza issue; mock relacionado continua bloqueando', /** Separa registro independente de alteração do alvo. */ () => {
  const independent = "import {test} from 'vitest'; export function other(x:number){return x+2;} test('uncertain', callback);";
  const { result, unit } = run({ 'other/b.test.ts': independent });
  assert.equal(unit.decision, 'approved');
  assert.ok(result.issues.some(/** Exige localização do registro incerto comprovado. */ issue => issue.code === 'UNCERTAIN_TEST_EXECUTION' && issue.path === 'other/b.test.ts'));
  const mocked = run({ 'src/a.test.ts': "import {vi} from 'vitest'; vi.mock('./a');" + localTest }).unit;
  assert.equal(mocked.criteria.direct_test_association.status, 'not_evaluated');
  assert.equal(mocked.criteria.preserved_local_context.status, 'not_evaluated');
});

test('mocks sem import e recursos de alcance desconhecido fora do grafo não provam independência', /** Conserva efeitos cujo alvo não pode ser delimitado pela cadeia de imports. */ () => {
  for (const content of [
    "import {vi} from 'vitest'; vi.mock('../src/a');",
    "import {vi} from 'vitest'; vi.mock(dynamicTarget);",
    "import fs from 'node:fs'; export const data = fs.readFileSync(dynamicPath);",
    'export const loaded = import(dynamicPath);',
    'export const changed = globalThis.unknown = replacement;',
  ]) {
    const { unit, result } = run({ 'other/unknown.ts': content });
    assert.equal(unit.criteria.preserved_local_context.status, 'not_evaluated', content);
    assert.ok(result.issues.some(/** Exige diagnóstico auditável para o alcance desconhecido. */ issue => issue.code === 'UNPROVEN_RESOURCE_SCOPE'));
  }
});

test('registro incerto relacionado não apaga chamada comprovada, mas impede contexto completo', /** Distingue existência de associação e completude das condições usadas. */ () => {
  const { unit } = run({ 'other/related.test.ts': "import {test} from 'vitest'; import {increment} from '../src/a'; test('uncertain',callback);" });
  assert.equal(unit.criteria.direct_test_association.status, 'approved');
  assert.equal(unit.criteria.preserved_local_context.status, 'not_evaluated');
});

test('configurações de teste potencialmente efetivas não são módulos independentes', /** Impede aprovação diante de configuração direta ou transitivamente incompleta. */ () => {
  for (const path of ['vitest.config.ts', 'vite.config.mts', 'vitest.workspace.ts', 'jest.config.ts', 'jest.config.mjs', '.mocharc.cjs']) {
    for (const content of ["import './missing'; export default {};", "import './setup'; export default {};"]) {
      const { unit, result } = run({ [path]: content, 'setup.ts': "import './missing'; export {};" });
      assert.equal(unit.criteria.preserved_local_context.status, 'not_evaluated', path + content);
      assert.equal(unit.criteria.direct_test_association.status, 'approved');
      assert.equal(unit.criteria.associated_documentation.status, 'approved');
      assert.ok(result.unresolved_references.some(/** Mantém a dependência ausente da configuração ou de seu setup. */ reference => reference.specifier === './missing'));
    }
  }
});

test('configurações de teste declarativas com setup ou recursos não comprovados mantêm pendência', /** Conserva alcance desconhecido sem executar ou interpretar parcialmente cada formato. */ () => {
  for (const [path, content] of [
    ['vitest.config.json', '{"test":{"setupFiles":["./missing.ts"]}}'],
    ['jest.config.json', '{"setupFilesAfterEnv":["<rootDir>/missing.ts"]}'],
    ['.mocharc.json', '{"require":["./missing.ts"]}'],
    ['.mocharc.yaml', 'require:\n  - ./missing.ts'],
    ['test/mocha.opts', '--require ./missing.ts'],
    ['package.json', '{"jest":{"setupFiles":["./missing.ts"]}}'],
    ['package.json', '{"mocha":{"require":["./missing.ts"]}}'],
  ]) {
    const { unit, result } = run({ [path]: content });
    assert.equal(unit.decision, 'not_evaluated', path + content);
    assert.ok(result.issues.some(/** Exige diagnóstico auditável de configuração cujo efeito não foi comprovado. */ issue => issue.code === 'UNPROVEN_RESOURCE_SCOPE' && issue.path === path));
  }
});

test('ambiente Node e carregamento explícito de env não comprovam contexto completo', /** Protege leitura de ambiente global e aliases importados sem executar o candidato. */ () => {
  for (const content of [
    localTest.replace('increment(1)', "{process.loadEnvFile('./missing.env');increment(Number(process.env.INPUT));}"),
    "import {loadEnvFile as load} from 'node:process'; " + localTest.replace('increment(1)', "{load('./missing.env');increment(1);}"),
  ]) {
    const { unit, result } = run({ 'src/a.test.ts': content });
    assert.equal(unit.criteria.preserved_local_context.status, 'not_evaluated');
    assert.equal(unit.criteria.direct_test_association.status, 'approved');
    assert.ok(result.issues.some(/** Exige diagnóstico do acesso a ambiente não preservado. */ issue => issue.code === 'UNPROVEN_RESOURCE_SCOPE' && issue.path === 'src/a.test.ts'));
  }
});

test('carregadores CommonJS e aliases não comprovam independência de módulo ausente', /** Conserva carregamento indireto sem tentar resolver ou executar o carregador. */ () => {
  for (const loader of [
    "module.require('../other/bridge');",
    "const load = require; load('../other/bridge');",
    "const {require: load} = module; load('../other/bridge');",
    "const load = module.require.bind(module); load('../other/bridge');",
    "import {createRequire} from 'node:module'; const load = createRequire(import.meta.url); load('../other/bridge');",
    "import {createRequire as makeLoader} from 'module'; const load = makeLoader(import.meta.url); load('../other/bridge');",
    "import * as loaders from 'node:module'; const load = loaders.createRequire(import.meta.url); load('../other/bridge');",
  ]) {
    const { unit, result } = run({ 'src/a.test.ts': loader + localTest, 'other/bridge.ts': "import './missing'; export {};" });
    assert.equal(unit.decision, 'not_evaluated', loader);
    assert.equal(unit.criteria.preserved_local_context.status, 'not_evaluated', loader);
    assert.equal(unit.criteria.direct_test_association.status, 'approved', loader);
    assert.equal(unit.criteria.associated_documentation.status, 'approved', loader);
    assert.ok(result.issues.some(/** Mantém auditável a origem do carregamento de alcance desconhecido. */ issue => issue.code === 'UNPROVEN_RESOURCE_SCOPE' && issue.path === 'src/a.test.ts'), loader);
    assert.ok(result.unresolved_references.some(/** Preserva a referência ausente sem inventar aresta do carregador. */ reference => reference.from === 'other/bridge.ts' && reference.specifier === './missing'), loader);
  }
});

test('referência a carregador não afirma execução nem completa seu alcance', /** Mantém pendência conservadora mesmo com alias importado ainda não chamado. */ () => {
  for (const loader of ["import {createRequire as unusedLoader} from 'node:module';", 'const unusedLoader = require;']) {
    const { unit } = run({ 'src/a.test.ts': loader + localTest });
    assert.equal(unit.criteria.preserved_local_context.status, 'not_evaluated', loader);
    assert.equal(unit.validation.status, 'not_evaluated');
    assert.equal(unit.validation.executed_test_cases, null);
  }
});
