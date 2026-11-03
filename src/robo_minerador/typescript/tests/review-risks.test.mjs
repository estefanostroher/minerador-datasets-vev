import test from 'node:test';
import assert from 'node:assert/strict';
import { analyze } from '../analyze.mjs';

const implementation = '/** Incrementa. */\nexport function increment(x: number) { return x + 1; }';

/** Monta snapshots de regressão sem executar código dos candidatos. */
function run(files) {
  return analyze({ files: Object.entries(files).map(/** Preserva o texto de cada arquivo virtual. */ ([path, content]) => ({ path, content })), issues: [] });
}

/** Localiza a unidade original para conferir decisões e associações. */
function unit(result, name = 'increment', owner = null) {
  return result.instances.find(/** Seleciona a identidade lexical solicitada. */ item => item.function.name === name && item.function.class_name === owner);
}

test('ignorado ou opção incerta preserva associação sem aprovar por essa evidência', /** Impede seleção apoiada exclusivamente em testes ignorados ou indeterminados. */ () => {
  const bodies = [
    `import {test} from 'vitest'; test.skip('ignored', () => increment(1));`,
    `import {test, describe} from 'vitest'; describe.skip('suite', () => { describe('nested', () => { test('ignored', () => increment(1)); }); });`,
    `import {test} from 'node:test'; test('ignored', {'skip':true}, () => increment(1));`,
    `import {test} from 'node:test'; test('uncertain', {...options}, () => increment(1));`,
    `import {test} from 'node:test'; test('uncertain', options, () => increment(1));`,
    `import {test} from 'node:test'; test('uncertain', {skip}, () => increment(1));`,
  ];
  for (const body of bodies) {
    const result = run({ 'a.ts': implementation, 'a.test.ts': `import {increment} from './a'; ${body}` });
    const candidate = unit(result);
    assert.equal(candidate.tests.length, 1, body);
    assert.equal(candidate.criteria.direct_test_association.status, 'not_evaluated', body);
    assert.equal(candidate.decision, 'not_evaluated', body);
    assert.equal(candidate.validation.executed_test_cases, null);
    if (body.includes('ignored')) assert.equal(candidate.tests[0].skipped, true, body);
    else assert.equal(Object.hasOwn(candidate.tests[0], 'skipped'), false, body);
  }
  const active = run({ 'a.ts': implementation, 'a.test.ts': `import {test} from 'vitest'; import {increment} from './a'; test.skip('ignored', () => increment(1)); test('active', () => increment(2));` });
  assert.equal(unit(active).criteria.direct_test_association.status, 'approved');
  assert.equal(unit(active).tests.length, 2);
});

test('registro em helper nunca chamado ou callback diferido não comprova registro de teste', /** Impede associação aprovada em corpos que podem nunca registrar testes. */ () => {
  for (const body of [
    `function neverCalled(){test('hidden',()=>increment(1));}`,
    `setTimeout(()=>test('deferred',()=>increment(1)),0);`,
    `describe('suite',()=>{function neverCalled(){test('hidden',()=>increment(1));}});`,
  ]) {
    const result = run({ 'a.ts': implementation, 'a.test.ts': `import {test,describe} from 'vitest'; import {increment} from './a'; ${body}` });
    assert.equal(unit(result).criteria.direct_test_association.status, 'not_evaluated', body);
    assert.notEqual(unit(result).decision, 'approved', body);
  }
  const suite = run({ 'a.ts': implementation, 'a.test.ts': `import {test,describe} from 'vitest'; import {increment} from './a'; describe('suite',()=>test('registered',()=>increment(1)));` });
  assert.equal(unit(suite).decision, 'approved');
});

test('specifier de framework remapeado para módulo local não comprova API real', /** Impede confiar no nome vitest quando o compilador resolve outro módulo. */ () => {
  for (const imported of [`import {test} from 'vitest';`, `import {test} from './testing';`]) {
    const result = run({ 'tsconfig.json': '{"compilerOptions":{"baseUrl":".","paths":{"vitest":["fake.ts"]}}}', 'fake.ts': 'export function test(name, callback) {}', 'testing.ts': `export {test} from 'vitest';`, 'a.ts': implementation, 'a.test.ts': `${imported} import {increment} from './a'; test('fake',()=>increment(1));` });
    assert.equal(unit(result).tests.length, 0);
    assert.notEqual(unit(result).decision, 'approved');
  }
});

test('construtor próprio ou herdado que retorna outro objeto invalida receptor original', /** Impede associar método de instância substituída durante construção. */ () => {
  for (const declaration of [
    `export class Counter { constructor(){return new Other()} /** Incrementa. */ increment(x:number){return x+1} }`,
    `class Base {constructor(){return new Other()}} export class Counter extends Base { /** Incrementa. */ increment(x:number){return x+1} }`,
  ]) {
    const result = run({ 'a.ts': `class Other { increment(x:number){return x*2} } ${declaration}`, 'a.test.ts': `import {test} from 'vitest'; import {Counter} from './a'; test('replacement',()=>{const receiver=new Counter(); receiver.increment(1);});` });
    assert.equal(unit(result, 'increment', 'Counter').tests.length, 0);
    assert.equal(unit(result, 'increment', 'Counter').criteria.direct_test_association.status, 'not_evaluated');
  }
});

test('aritmética literal constante rejeita e independência desconhecida fica pendente', /** Impede aprovar trivialidade apenas pela existência de nó binário. */ () => {
  const result = run({ 'a.ts': `export function arithmetic(){return 1+2} export function nested(){return -(2*3)+4} export function unknown(){return externalValue+2} export function dependent(x:number){return x+2}` });
  assert.equal(unit(result, 'arithmetic').criteria.nontrivial_implementation.status, 'rejected');
  assert.equal(unit(result, 'nested').criteria.nontrivial_implementation.status, 'rejected');
  assert.equal(unit(result, 'unknown').criteria.nontrivial_implementation.status, 'not_evaluated');
  assert.equal(unit(result, 'dependent').criteria.nontrivial_implementation.status, 'approved');
});

test('tags de metadados não documentam e prosa curta não exige contagem de palavras', /** Evita falsa documentação e rejeição por tamanho da descrição. */ () => {
  for (const comment of ['/** @name increment */', '/** @public */', '/** @returns {number} */', '// @ts-ignore', '/// <reference path="./present.d.ts" />']) {
    const result = run({ 'a.ts': comment + '\n' + implementation.split('\n')[1], 'present.d.ts': 'declare const value: number;' });
    assert.equal(unit(result).documentation.length, 0, comment);
  }
  for (const comment of ['/** Incrementa. */', '/** @returns {number} Incrementa. */']) {
    const result = run({ 'a.ts': comment + '\n' + implementation.split('\n')[1] });
    assert.equal(unit(result).criteria.associated_documentation.status, 'approved', comment);
  }
});

test('descrição na seção abaixo de link Markdown preserva trecho sem absorver seção vizinha', /** Impede ausência falsa ou associação de documentação de outro símbolo. */ () => {
  const result = run({ 'a.ts': implementation.split('\n')[1], 'README.md': '## [increment](a.ts#increment)\n\nSoma uma unidade ao argumento recebido.\n\n## unrelated\nDescrição de outra unidade.' });
  const candidate = unit(result);
  assert.equal(candidate.criteria.associated_documentation.status, 'approved');
  assert.equal(candidate.documentation[0].start_line, 1);
  assert.ok(candidate.documentation[0].original_text.includes('Soma uma unidade'));
  assert.ok(!candidate.documentation[0].original_text.includes('unrelated'));
});

test('referência triple slash ausente torna contexto pendente sem virar descrição', /** Protege completude do contexto local referenciado pelo compilador. */ () => {
  const result = run({ 'a.ts': `/// <reference path='./missing.d.ts' />\n${implementation}`, 'a.test.ts': `import {test} from 'vitest'; import {increment} from './a'; test('active',()=>increment(1));` });
  assert.equal(unit(result).criteria.preserved_local_context.status, 'not_evaluated');
  assert.notEqual(unit(result).decision, 'approved');
  assert.ok(result.unresolved_references.some(/** Confere preservação da referência local ausente. */ reference => reference.from === 'a.ts' && reference.specifier === './missing.d.ts'));
  assert.ok(unit(result).documentation.every(/** Exclui diretivas técnicas da documentação comportamental. */ doc => !doc.original_text.includes('<reference')));
  const present = run({ 'a.ts': `/// <reference path='./present.d.ts' />\n${implementation}`, 'present.d.ts': 'declare const value: number;' });
  assert.ok(present.local_dependencies.some(/** Confere resolução virtual da referência existente. */ reference => reference.resolved_path === 'present.d.ts'));
});

test('métodos concretos de classes expressão são unidades sem incluir a expressão como função', /** Evita desaparecer implementações válidas em classes anônimas. */ () => {
  const result = run({ 'a.ts': 'export const Counter=class{/** Incrementa. */increment(x:number){return x+1}}; export let Mutable=class{/** Duplica. */double(x:number){return x*2}};', 'a.test.ts': `import {test} from 'vitest'; import {Counter} from './a'; test('class expression',()=>{const receiver=new Counter();receiver.increment(1);});` });
  assert.equal(result.instances.length, 2);
  assert.equal(unit(result, 'increment', 'Counter').function.kind, 'class_method');
  assert.equal(unit(result, 'increment', 'Counter').tests.length, 1);
  assert.equal(unit(result, 'double').function.class_name, null);
});

test('throws e exception descritivos documentam comportamento sem aceitar tags vazias', /** Evita perder descrições de falha e aceitar apenas metadados de exceção. */ () => {
  for (const tag of ['throws', 'exception']) {
    const comment = `/** @${tag} {Error} Rejeita entradas negativas. */`;
    const result = run({ 'a.ts': `${comment}\n${implementation.split('\n')[1]}` });
    assert.equal(unit(result).criteria.associated_documentation.status, 'approved');
    assert.equal(unit(result).documentation[0].original_text, comment);
    const metadata = run({ 'a.ts': `/** @${tag} {Error} */\n${implementation.split('\n')[1]}` });
    assert.equal(unit(metadata).documentation.length, 0);
  }
});

test('prosa no cabeçalho vinculado conta sem transformar o rótulo de identidade em descrição', /** Protege descrição inline e preserva integralmente o cabeçalho original. */ () => {
  const heading = '## [increment](a.ts#increment) Soma uma unidade ao argumento.';
  const result = run({ 'a.ts': implementation.split('\n')[1], 'README.md': heading });
  assert.equal(unit(result).criteria.associated_documentation.status, 'approved');
  assert.equal(unit(result).documentation[0].original_text, heading);
  assert.equal(unit(result).documentation[0].start_line, 1);
  assert.equal(unit(result).documentation[0].end_line, 1);
  const identityOnly = run({ 'a.ts': implementation.split('\n')[1], 'README.md': '## [Documentação de increment](a.ts#increment)' });
  assert.equal(unit(identityOnly).documentation.length, 0);
});

test('seção filha vinculada a outro símbolo não documenta o pai e vínculo ambíguo fica pendente', /** Impede aprovar increment usando exclusivamente a descrição de double. */ () => {
  const source = `${implementation.split('\n')[1]}\nexport function double(x:number){return x*2}`;
  const markdown = '## [increment](a.ts#increment)\n\n### [double](a.ts#double)\n\nDobra o argumento recebido.';
  const result = run({ 'a.ts': source, 'README.md': markdown });
  assert.notEqual(unit(result).criteria.associated_documentation.status, 'approved');
  assert.equal(unit(result).documentation.length, 0);
  assert.equal(unit(result, 'double').criteria.associated_documentation.status, 'approved');
  const withOwnProse = run({ 'a.ts': source, 'README.md': markdown.replace('\n\n###', '\n\nIncrementa.\n\n###') });
  assert.equal(unit(withOwnProse).criteria.associated_documentation.status, 'approved');
  assert.ok(!unit(withOwnProse).documentation[0].original_text.includes('Dobra'));
  const ambiguous = run({ 'a.ts': source, 'README.md': markdown.replace('a.ts#double', 'a.ts#unknown') });
  assert.equal(unit(ambiguous).criteria.associated_documentation.status, 'not_evaluated');
  assert.equal(unit(ambiguous).documentation.length, 0);
  const sourceLinked = run({ 'a.ts': `/** @see README.md#increment */\n${source}`, 'README.md': markdown.replace('## [increment](a.ts#increment)', '## increment') });
  assert.notEqual(unit(sourceLinked).criteria.associated_documentation.status, 'approved');
  assert.equal(unit(sourceLinked).documentation.length, 0);
});

test('referência documental exige caminho integral tanto com fragmento quanto com dois-pontos', /** Impede atribuir documentação de other/a.ts à unidade homônima em a.ts. */ () => {
  for (const separator of ['#', '::']) {
    const source = implementation.split('\n')[1];
    const reference = `other/a.ts${separator}increment Soma uma unidade ao argumento.`;
    const result = run({ 'a.ts': source, 'other/a.ts': source, 'README.md': reference });
    const root = result.instances.find(/** Seleciona a unidade cujo caminho é apenas sufixo da referência. */ item => item.function.path === 'a.ts');
    const nested = result.instances.find(/** Seleciona a unidade explicitamente documentada. */ item => item.function.path === 'other/a.ts');
    assert.equal(root.documentation.length, 0, separator);
    assert.equal(root.criteria.associated_documentation.status, 'rejected', separator);
    assert.equal(nested.criteria.associated_documentation.status, 'approved', separator);
    assert.equal(nested.documentation[0].original_text, reference);
    const direct = run({ 'a.ts': source, 'README.md': `Descrição: \`a.ts${separator}increment\` Soma uma unidade ao argumento.` });
    assert.equal(unit(direct).criteria.associated_documentation.status, 'approved', separator);
  }
});

test('pacotes independentes não resolvem raízes sob a configuração de outro pacote', /** Impede aliases ficticiamente ambíguos e mantém dependências transitivas acessíveis. */ () => {
  const config = '{"compilerOptions":{"baseUrl":".","paths":{"@app/*":["src/*"]}}}';
  const result = run({
    'tsconfig.json': config,
    'src/a.ts': implementation,
    'a.test.ts': `import {test} from 'vitest'; import {increment} from '@app/a'; test('root',()=>increment(1));`,
    'packages/p/tsconfig.json': config,
    'packages/p/src/a.ts': implementation,
    'packages/p/a.test.ts': `import {test} from 'vitest'; import {increment} from '@app/a'; test('package',()=>increment(1));`,
  });
  assert.deepEqual(result.issues, []);
  const aliases = result.local_dependencies.filter(/** Isola as resoluções que devem depender da configuração proprietária. */ dependency => dependency.specifier === '@app/a');
  assert.deepEqual(aliases, [
    { from: 'a.test.ts', specifier: '@app/a', resolved_path: 'src/a.ts' },
    { from: 'packages/p/a.test.ts', specifier: '@app/a', resolved_path: 'packages/p/src/a.ts' },
  ]);
  for (const candidate of result.instances) {
    assert.equal(candidate.decision, 'approved');
    assert.equal(candidate.tests.length, 1);
    assert.equal(candidate.tests[0].path, candidate.function.path === 'src/a.ts' ? 'a.test.ts' : 'packages/p/a.test.ts');
    assert.equal(candidate.tests[0].association.resolved_target.path, candidate.function.path);
    assert.equal(candidate.validation.executed_test_cases, null);
  }
  const transitive = run({
    'tsconfig.json': config,
    'packages/p/tsconfig.json': config,
    'packages/p/src/a.ts': implementation,
    'bridge.ts': `export {increment} from './packages/p/src/a';`,
    'a.test.ts': `import {test} from 'vitest'; import {increment} from './bridge'; test('cross-project',()=>increment(1));`,
  });
  assert.equal(unit(transitive).tests.length, 1);
  assert.equal(unit(transitive).tests[0].association.resolved_target.path, 'packages/p/src/a.ts');
  assert.equal(unit(transitive).decision, 'approved');
});

test('campos de instância não comprovam chamada imediata, mas nomes computados e campos estáticos distinguem execução', /** Impede atribuir chamadas em instâncias nunca criadas ao callback de teste. */ () => {
  for (const body of [
    'class Never { field=increment(1); }',
    'const Never=class { ["field"]=increment(1); };',
    'class Never { static Nested=class { field=increment(1); }; }',
  ]) {
    const result = run({ 'a.ts': implementation, 'a.test.ts': `import {test} from 'vitest'; import {increment} from './a'; test('no instance',()=>{${body}});` });
    assert.equal(unit(result).tests.length, 0, body);
    assert.equal(unit(result).criteria.direct_test_association.status, 'not_evaluated', body);
    assert.notEqual(unit(result).decision, 'approved', body);
  }
  for (const body of [
    'class Immediate { [increment(1)]=0; }',
    'class Immediate { [increment(1)]() {} }',
    'class Immediate { static field=increment(1); }',
    'class Immediate { static { increment(1); } }',
  ]) {
    const result = run({ 'a.ts': implementation, 'a.test.ts': `import {test} from 'vitest'; import {increment} from './a'; test('definition',()=>{${body}});` });
    assert.equal(unit(result).tests.length, 1, body);
    assert.equal(unit(result).criteria.direct_test_association.status, 'approved', body);
    assert.equal(unit(result).validation.executed_test_cases, null);
  }
});

test('inicializadores de parâmetros e desestruturação impedem rejeição trivial não comprovada', /** Evita ignorar lógica executável antes da chamada delegada no corpo. */ () => {
  for (const parameters of [
    'x:number,y=x+1',
    'x:number,{y=x+1}={}',
    'x:number,{nested:{y=x+1}}',
    'x:number,[y=x+1]',
    'x:number,{[String(x+1)]:y}',
    'x:number,y=unknownCall(x)',
  ]) {
    const result = run({ 'a.ts': `/** Incrementa por argumento padrão. */ export function increment(${parameters}){return identity(y)} function identity(y:number){return y}`, 'a.test.ts': `import {test} from 'vitest'; import {increment} from './a'; test('default',()=>increment(1));` });
    assert.equal(unit(result).criteria.nontrivial_implementation.status, 'not_evaluated', parameters);
    assert.notEqual(unit(result).decision, 'rejected', parameters);
    assert.equal(unit(result).tests.length, 1, parameters);
  }
  const plain = run({ 'a.ts': 'export function increment(x:number){return identity(x)} function identity(x:number){return x}' });
  assert.equal(unit(plain).criteria.nontrivial_implementation.status, 'rejected');
});

test('extensão markdown documenta tanto por referência externa quanto por link na origem', /** Impede ausência falsa de documentação textual já recuperada pelo coordenador Python. */ () => {
  const externalText = '[increment](a.ts#increment) Soma uma unidade ao argumento.';
  const external = run({ 'a.ts': implementation.split('\n')[1], 'README.markdown': externalText });
  assert.equal(unit(external).criteria.associated_documentation.status, 'approved');
  assert.equal(unit(external).documentation[0].path, 'README.markdown');
  assert.equal(unit(external).documentation[0].original_text, externalText);
  for (const comment of ['/** @see README.markdown */', '/** @see README.markdown#increment */', '/** @link README.markdown#increment */']) {
    const linked = run({ 'a.ts': `${comment}\n${implementation.split('\n')[1]}`, 'README.markdown': '## increment\nSoma uma unidade ao argumento.' });
    assert.equal(unit(linked).criteria.associated_documentation.status, 'approved', comment);
    assert.equal(unit(linked).documentation[0].path, 'README.markdown', comment);
    assert.equal(unit(linked).documentation[0].association.kind, 'source_link', comment);
  }
});
