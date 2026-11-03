import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { analyze } from '../analyze.mjs';

/** Converte fixtures textuais em entrada sem executar o código candidato. */
function run(files, issues = []) {
  return analyze({ files: Object.entries(files).map(/** Preserva caminho e conteúdo da fixture. */ ([path, content]) => ({ path, content })), issues });
}

const implementation = '/** Soma a entrada com uma unidade. */\nexport function increment(value: number) { return value + 1; }';

test('aliases, reexports e homônimos preservam identidade real', /** Verifica seleção incorreta por coincidência de nome. */ () => {
  const result = run({
    'src/a.ts': implementation,
    'src/barrel.ts': 'export { increment as renamed } from "./a";',
    'src/other.ts': implementation.replace('increment', 'unrelated'),
    'spec/a.spec.ts': `import { test as check } from 'vitest'; import { renamed as invoke } from '../src/barrel';
      check('real', () => { invoke(1); });
      function test(name: string, callback: () => void) {} test('fake', () => { invoke(2); });
      check('shadow', () => { const invoke = (x: number) => x; invoke(3); });`,
  });
  const target = result.instances.find(/** Localiza declaração original através do barrel. */ item => item.function.name === 'increment');
  assert.equal(target.tests.length, 1);
  assert.equal(target.tests[0].name, 'real');
  assert.equal(target.tests[0].association.resolved_target.path, 'src/a.ts');
  assert.equal(target.decision, 'approved');
  assert.equal(result.instances.find(/** Localiza homônimo sem associação. */ item => item.function.name === 'unrelated').tests.length, 0);
});

test('hooks, helpers e callbacks diferidos não provam chamada direta', /** Impede atribuição de execução indireta. */ () => {
  const result = run({ 'a.ts': implementation, 'a.test.ts': `import { test, beforeEach } from 'vitest'; import { increment } from './a';
    beforeEach(() => increment(0)); function helper() { increment(1); }
    test('indirect', () => { helper(); setTimeout(() => increment(2), 0); });
    test('returned callback', () => () => increment(3));
    test.skip.each([[1], [2]])('table', (value) => { increment(value); });` });
  assert.equal(result.instances[0].tests.length, 1);
  assert.equal(result.instances[0].tests[0].parameterized, true);
  assert.equal(result.instances[0].tests[0].skipped, true);
  assert.equal(result.instances[0].validation.executed_test_cases, null);
  assert.equal(result.instances[0].validation.suite_passed, null);
});

test('métodos exigem receptor concreto e rejeitam any ou parâmetro polimórfico', /** Evita associação de despacho não comprovado. */ () => {
  const result = run({ 'a.ts': `export class Counter {
    /** Soma a entrada com uma unidade. */ increment(x: number) { return x + 1; }
    /** Dobra a entrada numérica recebida. */ static double(x: number) { return x * 2; }
  }`, 'a.test.ts': `import { test } from 'node:test'; import { Counter } from './a';
    test('concrete', () => { const counter = new Counter(); counter.increment(2); Counter.double(3); });
    test('ambiguous', () => { const counter: any = new Counter(); counter.increment(3); });
    test('polymorphic', (counter: Counter) => { counter.increment(3); });` });
  assert.equal(result.instances.length, 2);
  assert.equal(result.instances[0].tests.length, 1);
  assert.equal(result.instances[0].tests[0].name, 'concrete');
  assert.equal(result.instances[1].tests.length, 1);
  assert.ok(result.issues.some(/** Exige registro explícito da ambiguidade. */ issue => issue.code === 'UNCERTAIN_TEST_EXECUTION'));
});

test('mocks e spies não provam execução original', /** Impede aprovação de chamadas interceptadas. */ () => {
  for (const mock of [`vi.mock('./a');`, `vi.spyOn(api, 'increment');`]) {
    const result = run({ 'a.ts': implementation, 'a.test.ts': `import { test, vi } from 'vitest'; import * as api from './a'; ${mock} test('mocked', () => api.increment(2));` });
    assert.equal(result.instances[0].tests.length, 0);
    assert.equal(result.instances[0].criteria.direct_test_association.status, 'not_evaluated');
  }
});

test('documentação anexada, externa, ausente e ambígua preserva proveniência', /** Evita aprovar tags vazias ou associar documentação por similaridade. */ () => {
  const bare = 'export function increment(x: number) { return x + 1; }';
  const attached = run({ 'a.ts': '/** @returns {number} Retorna a entrada incrementada. */\n' + bare });
  assert.equal(attached.instances[0].documentation[0].original_text, '/** @returns {number} Retorna a entrada incrementada. */');
  const external = run({ 'a.ts': bare, 'README.md': '[increment](a.ts#increment) Soma uma unidade à entrada.' });
  assert.equal(external.instances[0].documentation[0].path, 'README.md');
  const missing = run({ 'a.ts': '/** @returns {number} */\n' + bare, 'README.md': 'increment é uma função muito importante.' });
  assert.equal(missing.instances[0].criteria.associated_documentation.status, 'rejected');
  const ambiguous = run({ 'a.ts': bare + '\nexport function other(x: number) { return x * 2; }', 'README.md': '[Fonte](a.ts) Implementa operações sobre entradas.' });
  assert.equal(ambiguous.instances[0].criteria.associated_documentation.status, 'not_evaluated');
  const incomplete = run({ 'a.ts': bare }, [{ code: 'SNAPSHOT_LIMIT', message: 'Snapshot incompleto.' }]);
  assert.equal(incomplete.instances[0].criteria.associated_documentation.status, 'not_evaluated');
});

test('trivialidade comprovada não usa contagem de linhas', /** Evita rejeitar lógica curta e aprovar wrappers longos. */ () => {
  const result = run({ 'a.ts': `export function constantReturn(x: number) { return 42; }
    export function delegate(x: number) { return unknownCall(x); }
    export function logic(x: number) { return unknownCall(x + 1); }
    export function uncertain(x: number) { const result = unknownCall(x); return result; }` });
  const statuses = Object.fromEntries(result.instances.map(/** Extrai decisão H-F06 por símbolo. */ item => [item.function.name, item.criteria.nontrivial_implementation.status]));
  assert.deepEqual(statuses, { constantReturn: 'rejected', delegate: 'rejected', logic: 'approved', uncertain: 'not_evaluated' });
});

test('FS virtual resolve paths JSONC e não lê imports locais do disco', /** Protege integridade do snapshot e resolução por configuração. */ () => {
  const result = run({ 'packages/p/tsconfig.json': '{ // alias local\n "compilerOptions": {"baseUrl": ".", "paths": {"@app/*": ["src/*"]}}}', 'packages/p/src/a.ts': implementation,
    'packages/p/a.test.ts': `import { test } from 'vitest'; import { increment } from '@app/a'; import absent from './missing'; test('alias', () => increment(1));` });
  assert.equal(result.instances[0].tests.length, 1);
  assert.ok(result.local_dependencies.some(/** Confere alvo virtual do alias. */ dependency => dependency.specifier === '@app/a' && dependency.resolved_path === 'packages/p/src/a.ts'));
  assert.ok(result.unresolved_references.some(/** Mantém referência ausente como pendência. */ reference => reference.specifier === './missing'));
  assert.equal(result.instances[0].criteria.preserved_local_context.status, 'not_evaluated');
  assert.throws(/** Verifica rejeição de travessia de diretórios. */ () => run({ '../a.ts': implementation }));
});

test('protocol CLI emite somente um JSON e importação é inerte', /** Protege o consumidor Python contra saídas ou execução inesperadas. */ () => {
  const cli = new URL('../analyze.mjs', import.meta.url);
  const result = spawnSync(process.execPath, [fileURLToPath(cli)], { input: JSON.stringify({ files: [{ path: 'a.ts', content: implementation }], issues: [] }), encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stderr, '');
  assert.equal(result.stdout.trim().split('\n').length, 1);
  assert.equal(JSON.parse(result.stdout).analyzer.name, 'typescript_compiler_api');
  const imported = spawnSync(process.execPath, ['--input-type=module', '-e', `await import(${JSON.stringify(cli.href)})`], { encoding: 'utf8' });
  assert.equal(imported.status, 0);
  assert.equal(imported.stdout, '');
});

test('APIs npm externas não tornam pendente uma associação local comprovada', /** Evita falsa ausência ou pendência causada por expectativas externas. */ () => {
  const result = run({ 'a.ts': implementation, 'a.test.ts': `import { test, expect } from 'vitest'; import { increment } from './a'; test('expect', () => { expect(increment(1)).toEqual(2); });` });
  assert.equal(result.instances[0].decision, 'approved');
  assert.deepEqual(result.issues, []);
});

test('manifests comprovam globais Jest e Mocha sem aceitar funções locais', /** Protege reconhecimento de testes sem imports explícitos. */ () => {
  for (const framework of ['jest', 'mocha']) {
    const result = run({ 'package.json': JSON.stringify({ devDependencies: { [framework]: '1.0.0' } }), 'a.ts': implementation, 'a.test.ts': `import { increment } from './a'; it.only('global', () => increment(1)); function test(name, fn) {} test('fake', () => increment(2));` });
    assert.equal(result.instances[0].tests.length, 1);
    assert.equal(result.instances[0].tests[0].framework, framework);
    assert.deepEqual(result.instances[0].tests[0].modifiers, ['only']);
  }
});

test('reexport de API de teste e alias const de função mantêm alvos', /** Impede perder identidade através de aliases locais comprovados. */ () => {
  const result = run({ 'a.ts': implementation, 'testing.ts': `export { test as check } from './testing-base';`, 'testing-base.ts': `export { test } from 'vitest';`, 'a.test.ts': `import { check } from './testing'; import { increment } from './a'; const first = increment; const invoke = first; check('alias', () => invoke(1));` });
  assert.equal(result.instances[0].tests.length, 1);
  assert.equal(result.instances[0].tests[0].association.resolved_target.name, 'increment');
});

test('mock de barrel e substituição de função invalidam evidência de chamada', /** Evita associação original por meio de reexports ou atribuições interceptadas. */ () => {
  for (const interception of [`vi.mock('./barrel');`, `api.increment = vi.fn();`]) {
    const result = run({ 'a.ts': implementation, 'barrel.ts': `export { increment } from './a';`, 'a.test.ts': `import { test, vi } from 'vitest'; import * as api from './barrel'; ${interception} test('intercepted', () => api.increment(1));` });
    assert.equal(result.instances[0].tests.length, 0);
    assert.equal(result.instances[0].criteria.direct_test_association.status, 'not_evaluated');
  }
});

test('documentação de métodos homônimos e links sem descrição permanecem conservadores', /** Impede associação textual ambígua ou baseada só em rótulo. */ () => {
  const result = run({ 'a.ts': 'export class A { run(x: number) { return x + 1; } } export class B { run(x: number) { return x * 2; } }', 'README.md': '[Método](a.ts#run) Transforma o argumento recebido.' });
  assert.ok(result.instances.every(/** Exige pendência em ambas as classes homônimas. */ item => item.criteria.associated_documentation.status === 'not_evaluated'));
  const labelOnly = run({ 'a.ts': implementation.replace('/** Soma a entrada com uma unidade. */', ''), 'README.md': '[Documentação da função](a.ts#increment)' });
  assert.equal(labelOnly.instances[0].documentation.length, 0);
});

test('unidades preservam implementação, assinatura, TSX e ordem sem incluir expressões', /** Protege seleção das unidades e integridade do texto original. */ () => {
  const source = '/** Renderiza o valor recebido. */\nexport function view(x: number) { return <b>{x + 1}</b>; }';
  const result = run({ 'z.tsx': source, 'a.ts': `export const arrow = (x: number) => x + 1; export class A { constructor() {} get value() { return 1; } method(x: number) { return x + 1; } }`, 'ambient.d.ts': 'declare function ambient(x: number): number;', 'tests/helper.ts': implementation });
  assert.deepEqual(result.instances.map(/** Verifica conjunto e ordem de unidades válidas. */ item => item.function.name), ['method', 'view']);
  assert.equal(result.instances[1].function.reference_code, 'export function view(x: number) { return <b>{x + 1}</b>; }');
  assert.equal(result.instances[1].function.signature, 'export function view(x: number)');
  assert.equal(result.instances[1].function.start_line, 2);
  assert.ok(result.instances[1].context_paths.includes('tests/helper.ts'));
});

test('exportação local sobrescrita e default inexistente não são APIs de teste', /** Impede associação a exports homônimos de frameworks. */ () => {
  const result = run({ 'a.ts': implementation, 'testing.ts': `export * from 'vitest'; export function test(name, callback) { callback(); }`, 'a.test.ts': `import { test } from './testing'; import fake from 'vitest'; import { increment } from './a'; test('local', () => increment(1)); fake('default', () => increment(2));` });
  assert.equal(result.instances.find(/** Localiza a função cuja execução não foi comprovada. */ item => item.function.name === 'increment').tests.length, 0);
});

test('documentação de overload e link da origem preserva o trecho explicitamente ligado', /** Evita ausência falsa e mistura de seções externas. */ () => {
  const overload = run({ 'a.ts': `/** Incrementa a entrada numérica recebida. */
    export function increment(x: number): number;
    export function increment(x: number) { return x + 1; }` });
  assert.equal(overload.instances.length, 1);
  assert.equal(overload.instances[0].documentation.length, 1);
  const linked = run({ 'src/a.ts': `/** @see ../docs/api.md#increment */\nexport function increment(x: number) { return x + 1; }`, 'docs/api.md': '# API\n## increment\nSoma uma unidade ao argumento recebido.\n## other\nNão pertence à documentação selecionada.' });
  assert.equal(linked.instances[0].documentation.length, 1);
  assert.equal(linked.instances[0].documentation[0].start_line, 2);
  assert.equal(linked.instances[0].documentation[0].end_line, 3);
  assert.ok(!linked.instances[0].documentation[0].original_text.includes('other'));
  const missing = run({ 'a.ts': `/** @see absent.md */\nexport function increment(x: number) { return x + 1; }` });
  assert.equal(missing.instances[0].criteria.associated_documentation.status, 'not_evaluated');
});

test('declaração default anônima e chamadas parametrizadas mantêm identidade sem executar casos', /** Evita perder unidades exportadas e inventar contagem de execução. */ () => {
  const result = run({ 'a.ts': '/** Incrementa a entrada numérica recebida. */\nexport default function(x: number) { return x + 1; }', 'a.test.ts': `import { test } from '@jest/globals'; import invoke from './a'; test.each\`value | result\n\${1} | \${2}\`('tagged', ({value}) => { invoke(value); });` });
  assert.equal(result.instances[0].function.name, 'default');
  assert.equal(result.instances[0].tests.length, 1);
  assert.equal(result.instances[0].tests[0].parameterized, true);
  assert.equal(result.instances[0].validation.executed_test_cases, null);
});

test('interface, callback referenciado e API estendida mantêm associação pendente', /** Evita reprovar por ausência quando o despacho não foi comprovado. */ () => {
  for (const body of [
    `interface Operation { increment(x: number): number; } test('interface', (receiver: Operation) => receiver.increment(1));`,
    `function body() { increment(1); } test('named', body);`,
    `const custom = test.extend({}); custom('extended', () => increment(1));`,
  ]) {
    const result = run({ 'a.ts': implementation, 'a.test.ts': `import { test } from 'vitest'; import { increment } from './a'; ${body}` });
    assert.equal(result.instances[0].tests.length, 0);
    assert.equal(result.instances[0].criteria.direct_test_association.status, 'not_evaluated');
  }
});

test('fragmento documental homônimo por prefixo não associa outra função', /** Impede associação externa por correspondência parcial do nome. */ () => {
  const result = run({ 'a.ts': implementation.replace('/** Soma a entrada com uma unidade. */', ''), 'README.md': '[Outra função](a.ts#incrementOther) Soma uma unidade à entrada.' });
  assert.equal(result.instances[0].documentation.length, 0);
});

test('ordem de entrada não altera evidências e opções dinâmicas não inventam skip', /** Protege determinismo e metadados sem executar expressões do candidato. */ () => {
  const files = { 'a.ts': implementation, 'z.test.ts': `import { test } from 'node:test'; import { increment } from './a'; const flag = unknownFlag(); test('dynamic', {skip: flag}, () => increment(1));`, 'a.test.ts': `import { test } from 'mocha'; import { increment } from './a'; test('first', () => increment(2));` };
  const result = run(files);
  assert.deepEqual(result, run(Object.fromEntries(Object.entries(files).reverse())));
  assert.deepEqual(result.instances[0].tests.map(/** Confere ordem lexical dos arquivos de teste. */ item => item.path), ['a.test.ts', 'z.test.ts']);
  assert.equal(Object.hasOwn(result.instances[0].tests[1], 'skipped'), false);
  assert.equal(result.instances[0].validation.executed_test_cases, null);
});

test('fonte inválida não fundamenta rejeição científica por trivialidade', /** Evita concluir H-F06 a partir de árvore sintática incompleta. */ () => {
  const result = run({ 'a.ts': '/** Retorna uma constante inteira. */\nexport function broken() { return 42;' });
  assert.equal(result.instances[0].decision, 'not_evaluated');
  assert.equal(result.instances[0].criteria.nontrivial_implementation.status, 'not_evaluated');
});

test('globais Vitest sem configuração comprovada não viram ausência científica', /** Preserva pendência de configurações executáveis que não foram avaliadas. */ () => {
  const result = run({ 'package.json': '{"devDependencies":{"vitest":"1.0.0"}}', 'a.ts': implementation, 'a.test.ts': `import { increment } from './a'; test('global', () => increment(1));`, 'vitest.config.ts': 'export default {test:{globals:true}};' });
  assert.equal(result.instances[0].tests.length, 0);
  assert.equal(result.instances[0].criteria.direct_test_association.status, 'not_evaluated');
});
