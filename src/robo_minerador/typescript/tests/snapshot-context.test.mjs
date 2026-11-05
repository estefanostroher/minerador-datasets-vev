import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { analyze } from '../analyze.mjs';
import { createProject } from '../virtual-project.mjs';

/** Produz protocolo textual verificável para exercitar somente análise estática. */
function snapshotInput(extra = {}, complete = true) {
  const texts = {
    'src/a.ts': '/** Incrementa a entrada. */\nexport function increment(x:number){return x+1;}',
    'src/a.test.ts': "import{test}from'vitest';import{increment}from'./a';test('direto',()=>increment(1));",
    'vitest.config.ts': "import{defineConfig}from'vitest/config';export default defineConfig({test:{environment:'node'}});",
    'package.json': '{"devDependencies":{"vitest":"1"}}',
    ...extra,
  };
  const files = Object.entries(texts).map(/** Preserva hash dos bytes textuais enviados. */ ([filePath, content]) => ({ path: filePath, content, sha256: createHash('sha256').update(content).digest('hex') }));
  const inventory = files.map(/** Liga cada texto a um objeto Git do inventário. */ file => {
    const raw = Buffer.from(file.content);
    return { path: file.path, mode: '100644', type: 'blob', size: raw.length, oid: createHash('sha1').update(`blob ${raw.length}\0`).update(raw).digest('hex') };
  });
  inventory.push({ path: 'fixtures/binary.bin', mode: '100644', type: 'blob', size: 2, oid: createHash('sha1').update('blob 2\0').update(Buffer.from([0, 255])).digest('hex') });
  return { files, issues: [], inventory, analysis_complete: complete, snapshot: {
    format: 'git-bare-v1', status: 'complete', repository_id: 42, commit_sha: 'a'.repeat(40),
    cache_key: '42/' + 'a'.repeat(40), tree_oid: 'b'.repeat(40), manifest_sha256: 'c'.repeat(64),
    file_count: inventory.length, total_blob_bytes: inventory.reduce(/** Soma apenas bytes versionados. */ (sum, entry) => sum + entry.size, 0),
  } };
}

/** Recupera a unidade científica sem depender de outras declarações do contexto. */
function unit(result) { return result.instances.find(/** Localiza a origem exata da unidade. */ item => item.function.path === 'src/a.ts'); }

test('V3 configurações e setups JS preservados mantêm mocks com allowJs falso ou verdadeiro', /** Cruza extensões, reexportações e imports transitivos sem executar contexto candidato. */ () => {
  for (const configExtension of ['ts', 'js']) for (const setupExtension of ['ts', 'js']) for (const allowJs of [false, true]) for (const route of ['direct', 'imported_config', 'imported_setup']) for (const mocked of [false, true]) {
    const configPath = 'vitest.config.' + configExtension, setupPath = 'setup.' + setupExtension;
    const extra = {
      'vitest.config.ts': 'export {};',
      [configPath]: route === 'imported_config' ? "import configuration from './configuration.js';export default configuration;" : `export default {test:{setupFiles:['./${setupPath}']}};`,
      'configuration.js': `export default {test:{setupFiles:['./${setupPath}']}};`,
      [setupPath]: route === 'imported_setup' ? "import './mock-helper.js';" : mocked ? "import{vi}from'vitest';vi.mock('./src/a');" : 'export {};',
      'mock-helper.js': mocked ? "import{vi}from'vitest';vi.mock('./src/a');" : 'export {};',
      'tools/irrelevant.js': "import{vi}from'vitest';vi.mock('../src/a');",
    };
    extra['tsconfig.json'] = JSON.stringify({ files: ['src/a.ts', 'src/a.test.ts', ...Object.keys(extra)], compilerOptions: { allowJs } });
    const input = snapshotInput(extra), result = analyze(input);
    const label = JSON.stringify({ configExtension, setupExtension, allowJs, route, mocked });
    assert.equal(unit(result).decision, mocked ? 'not_evaluated' : 'approved', label);
    assert.equal(unit(result).criteria.direct_test_association.status, mocked ? 'not_evaluated' : 'approved', label);
    assert.equal(unit(result).validation.status, 'not_evaluated');
    if (!mocked) assert.equal(result.issues.some(/** Não oculta falha de contexto nos controles positivos. */ issue => issue.code === 'UNANALYZED_TEXT'), false, label);
  }
});

test('V3 presets JS importados mantêm automock e controles positivos sob ambas opções allowJs', /** Segue preset declarativo até módulo JS importado sem alterar a configuração original. */ () => {
  for (const configExtension of ['ts', 'js']) for (const presetExtension of ['ts', 'js']) for (const allowJs of [false, true]) for (const automock of [false, true]) {
    const configPath = 'jest.config.' + configExtension, presetPath = 'preset.' + presetExtension;
    const extra = {
      [configPath]: `export default {preset:'./${presetPath}'};`,
      [presetPath]: "import configuration from './preset-value.js';export default configuration;",
      'preset-value.js': `export default {automock:${automock}};`,
      'src/a.test.ts': "import{test}from'@jest/globals';import{increment}from'./a';test('direto',()=>increment(1));",
    };
    extra['tsconfig.json'] = JSON.stringify({ files: ['src/a.ts', 'vitest.config.ts', ...Object.keys(extra)], compilerOptions: { allowJs } });
    assert.equal(unit(analyze(snapshotInput(extra))).decision, automock ? 'not_evaluated' : 'approved', JSON.stringify({ configExtension, presetExtension, allowJs, automock }));
  }
});

test('V3 análise de contexto não habilita JS no programa científico nem apaga sintaxe inválida necessária', /** Verifica separação dos programas e localização do diagnóstico de setup excluído. */ () => {
  const input = snapshotInput({
    'tsconfig.json': '{"files":["src/a.ts","src/a.test.ts","vitest.config.ts","vitest.config.js","setup.js"],"compilerOptions":{"allowJs":false}}',
    'vitest.config.js': "export default {test:{setupFiles:['./setup.js']}};",
    'setup.js': 'export const broken = ;',
  });
  const project = createProject(input);
  assert.equal(project.programFor('setup.js').getCompilerOptions().allowJs, false);
  assert.equal(project.programFor('setup.js').getSourceFile('/candidate/setup.js'), undefined);
  const result = analyze(input);
  assert.equal(unit(result).decision, 'not_evaluated');
  assert.match(unit(result).criteria.preserved_local_context.reason, /INVALID_SOURCE.*setup\.js/);
});

test('V3 AST auxiliar de contexto não promove teste JS excluído a testemunha científica', /** Contrasta a retenção estática de JS com sua admissão explícita no programa original. */ () => {
  for (const allowJs of [false, true]) {
    const result = analyze(snapshotInput({
      'src/a.test.ts': 'export {};',
      'src/only.test.js': "import{test}from'vitest';import{increment}from'./a';test('somente JS',()=>increment(1));",
      'tsconfig.json': JSON.stringify({ files: ['src/a.ts', 'src/a.test.ts', 'src/only.test.js', 'vitest.config.ts'], compilerOptions: { allowJs } }),
    }));
    assert.equal(unit(result).decision === 'approved', allowJs);
    assert.equal(unit(result).tests.length, allowJs ? 1 : 0);
  }
});

test('V3 stubGlobal distingue APIs de registro de recursos e respeita identidade do mock', /** Contrasta fetch, test, alias importado e vi sombreado no mesmo setup relacionado. */ () => {
  for (const [setup, expected] of [
    ['export {};', 'approved'],
    ["import{vi}from'vitest';vi.stubGlobal('fetch',()=>{});", 'approved'],
    ["import{vi}from'vitest';vi.stubGlobal('test',()=>{});", 'not_evaluated'],
    ["import{vi as mockApi}from'vitest';const copied=mockApi;copied.stubGlobal('test',()=>{});", 'not_evaluated'],
    ["const vi={stubGlobal(name,callback){}};vi.stubGlobal('test',()=>{});", 'approved'],
  ]) {
    const result = analyze(snapshotInput({
      'vitest.config.ts': "export default {test:{globals:true,setupFiles:['./setup.ts']}};",
      'setup.ts': setup,
      'src/a.test.ts': "import{increment}from'./a';test('direto',()=>increment(1));",
    }));
    assert.equal(unit(result).decision, expected, setup);
    assert.equal(unit(result).criteria.direct_test_association.status, expected, setup);
  }
});

test('V3 JS listado com allowJs falso não causa crash nem suprime diagnóstico de fonte TS', /** Confere exclusão legítima de AST JavaScript e pendência TS necessária como controle negativo. */ () => {
  for (const allowJs of [false, true]) for (const missing of [false, true]) {
    const result = analyze(snapshotInput({
      'tsconfig.json': JSON.stringify({ files: ['src/a.ts', 'tools/helper.js'], compilerOptions: { allowJs } }),
      'tools/helper.js': 'export const helper=1;',
      'src/a.ts': (missing ? "import './missing';" : '') + '/** Incrementa a entrada. */export function increment(x:number){return x+1;}',
    }));
    assert.equal(unit(result).decision, missing ? 'not_evaluated' : 'approved');
    assert.equal(result.issues.some(/** Mantém diagnóstico local necessário independente da disponibilidade do helper JS. */ issue => issue.code === 'UNRESOLVED_LOCAL_IMPORT' && issue.path === 'src/a.ts'), missing);
    assert.equal(unit(result).validation.status, 'not_evaluated');
  }
});

test('V3 alternativas usam defaults próprios e herdam somente extends declarado', /** Contrasta ausência, override e herança legítima de moduleSuffixes. */ () => {
  for (const [alternative, expected] of [
    [{ include: ['src/**/*.test.ts'] }, 'not_evaluated'],
    [{ include: ['src/**/*.test.ts'], compilerOptions: { moduleSuffixes: [''] } }, 'not_evaluated'],
    [{ extends: './tsconfig.json', include: ['src/**/*.test.ts'] }, 'approved'],
  ]) {
    const result = analyze(snapshotInput({
      'tsconfig.json': JSON.stringify({ compilerOptions: { moduleSuffixes: ['.native', ''] } }),
      'tsconfig.test.json': JSON.stringify(alternative),
      'src/a.native.ts': '/** Incrementa nativamente. */export function increment(x:number){return x+2;}',
    }));
    const native = result.instances.find(/** Localiza implementação escolhida pela primária. */ item => item.function.path === 'src/a.native.ts');
    assert.equal(native.decision, expected);
    assert.equal(result.issues.some(/** Exige diagnóstico somente para identidade divergente. */ issue => issue.code === 'AMBIGUOUS_MODULE_RESOLUTION'), expected !== 'approved');
  }
});

test('V3 exports não descartam runtime JS concorrente nem confundem declaração com implementação', /** Contrasta TS concreto, declaração ambiente e alvos únicos. */ () => {
  for (const [types, runtime, expected] of [
    ['./src/index.ts', './dist/index.js', 'not_evaluated'],
    ['./src/index.ts', './src/other.ts', 'not_evaluated'],
    ['./dist/index.d.ts', './dist/index.js', 'approved'],
    ['./src/index.ts', './missing.js', 'approved'],
  ]) {
    const result = analyze(snapshotInput({
      'src/a.test.ts': "import{test}from'vitest';import{increment}from'@local/math';test('direto',()=>increment(1));",
      'packages/math/package.json': JSON.stringify({ name: '@local/math', exports: { types, import: runtime } }),
      'packages/math/src/index.ts': "export{increment}from'../../../src/a';",
      'packages/math/src/other.ts': 'export function increment(x:number){return x+3;}',
      'packages/math/dist/index.d.ts': 'export declare function increment(x:number):number;',
      'packages/math/dist/index.js': types.endsWith('.d.ts') ? "export{increment}from'../../../src/a';" : 'export function increment(x){return x+4;}',
    }));
    assert.equal(unit(result).decision, expected);
    assert.equal(result.issues.some(/** Conserva ambiguidade entre implementações concretas. */ issue => issue.code === 'AMBIGUOUS_MODULE_RESOLUTION'), expected !== 'approved');
  }
});

test('V3 aliases mappers automock e globais respeitam configuração relacionada', /** Move a mesma política entre raiz relacionada e diretório irmão isolado. */ () => {
  for (const [name, configuration, source] of [
    ['vitest.config.ts', "export default {resolve:{alias:{'./a':'./replacement.ts'}}};", "import{test}from'vitest';"],
    ['vitest.config.ts', "export default {resolve:{alias:{vitest:'./replacement.ts'}}};", "import{test}from'vitest';"],
    ['jest.config.json', JSON.stringify({ moduleNameMapper: { '^\\./a$': './replacement.ts' } }), "import{test}from'@jest/globals';"],
    ['jest.config.json', '{"automock":true}', "import{test}from'@jest/globals';"],
    ['vitest.config.ts', "export default {test:{globals:true,setupFiles:['./setup.ts']}};", ''],
  ]) {
    for (const [prefix, expected] of [['', 'not_evaluated'], ['other/', 'approved']]) {
      const result = analyze(snapshotInput({
        'vitest.config.ts': 'export default {test:{globals:true}};',
        [prefix + name]: configuration,
        [prefix + 'replacement.ts']: 'export function increment(x:number){return x+9;}',
        [prefix + 'setup.ts']: 'globalThis.test = (name, callback) => {};',
        'src/a.test.ts': source + "import{increment}from'./a';test('direto',()=>increment(1));",
      }));
      assert.equal(unit(result).decision, expected, prefix + name + configuration);
    }
  }
});

test('V3 testemunha independente resiste a mocks de alvo e API em testes irmãos', /** Exercita duas testemunhas da mesma unidade sem agregar efeitos entre seus escopos. */ () => {
  for (const setup of ["import{vi}from'vitest';vi.mock('../src/a');", "import{vi}from'vitest';vi.mock('vitest');", 'globalThis.test = (name, callback) => {};']) {
    const result = analyze(snapshotInput({
      'vitest.config.ts': 'export default {test:{globals:true}};',
      'other/vitest.config.ts': "export default {test:{globals:true,setupFiles:['./setup.ts']}};",
      'other/setup.ts': setup,
      'other/a.test.ts': "import{test}from'vitest';import{increment}from'../src/a';test('irmão',()=>increment(1));",
    }));
    assert.equal(unit(result).decision, 'approved', setup);
  }
});

test('V3 globais de configuração irmã não habilitam registro e escopo próximo prevalece', /** Não usa globals verdadeiro fora do contexto nem ignora falso explícito próximo. */ () => {
  const testSource = "import{increment}from'./a';test('direto',()=>increment(1));";
  for (const extra of [
    { 'vitest.config.ts': 'export default {};', 'other/vitest.config.ts': 'export default {test:{globals:true}};' },
    { 'vitest.config.ts': 'export default {test:{globals:true}};', 'src/vitest.config.ts': 'export default {test:{globals:false}};' },
  ]) assert.equal(unit(analyze(snapshotInput({ ...extra, 'src/a.test.ts': testSource }))).decision, 'not_evaluated');
});

test('V3 aprova evidência local com config, ambiente, FS, pacotes e binário sem avaliar execução', /** Confere fronteira entre preservação estática e requisitos dinâmicos. */ () => {
  const result = analyze(snapshotInput({ 'src/a.test.ts': "import{test}from'vitest';import{increment}from'./a';import{readFileSync}from'node:fs';test('direto',()=>{readFileSync(process.env.INPUT);increment(1);});const load=require;import(dynamicPath);" }));
  assert.equal(unit(result).decision, 'approved');
  assert.deepEqual(unit(result).validation, { status: 'not_evaluated', executed_test_cases: null, suite_passed: null, external_services: 'not_evaluated' });
  for (const type of ['test_configuration', 'environment', 'external_dependency', 'external_resource', 'dynamic_loading']) assert.ok(result.execution_requirements.some(/** Exige categoria informativa observada. */ item => item.type === type && item.status === 'not_evaluated'));
  assert.deepEqual(result.issues, []);
});

test('V3 recusa snapshot incompleto, inventário adulterado e bytes divergentes', /** Exercita integridade do protocolo antes do parser. */ () => {
  for (const damage of ['status', 'inventory', 'text']) {
    const input = snapshotInput();
    if (damage === 'status') input.snapshot.status = 'incomplete';
    if (damage === 'inventory') input.inventory.pop();
    if (damage === 'text') input.files[0].content += ' ';
    assert.throws(/** Impede analisar entrada inconsistente. */ () => analyze(input));
  }
});

test('V3 busca truncada não rejeita ausência documental ou de testes', /** Conserva pendências de busca sem inventar ausência científica. */ () => {
  const result = analyze(snapshotInput({ 'src/a.ts': 'export function increment(x:number){return x+1;}', 'src/a.test.ts': 'export {};' }, false));
  assert.equal(unit(result).criteria.associated_documentation.status, 'not_evaluated');
  assert.equal(unit(result).criteria.direct_test_association.status, 'not_evaluated');
  assert.equal(unit(result).decision, 'not_evaluated');
});

test('V3 import local necessário ausente e alvo incerto continuam pendentes', /** Não confunde dependência de identidade com ambiente de execução. */ () => {
  for (const text of [
    "import{test}from'vitest';import{increment}from'./missing';test('direto',()=>increment(1));",
    "import{test}from'vitest';import{increment}from'./a';const target:any=increment;test('incerto',()=>target(1));",
  ]) assert.notEqual(unit(analyze(snapshotInput({ 'src/a.test.ts': text }))).decision, 'approved');
});

test('V3 setup declarativo preservado com mock não aprova chamada ao original', /** Detecta mock por specifier mesmo quando setup não importa o alvo. */ () => {
  const result = analyze(snapshotInput({
    'vitest.config.ts': "export default {test:{setupFiles:['./setupfilename.ts']}};",
    'setupfilename.ts': "import{vi}from'vitest';vi.mock('./src/a',()=>({increment:()=>0}));",
  }));
  assert.equal(unit(result).decision, 'not_evaluated');
  assert.equal(unit(result).criteria.direct_test_association.status, 'not_evaluated');
  assert.ok(result.local_dependencies.some(/** Confere aresta declarativa usada no replay. */ item => item.from === 'vitest.config.ts' && item.resolved_path === 'setupfilename.ts'));
});

test('V3 globais declarativos resolvem API sem aceitar shadowing local', /** Verifica origem da API em vez da mera grafia de test. */ () => {
  const extra = { 'vitest.config.ts': 'export default {test:{globals:true}};', 'src/a.test.ts': "import{increment}from'./a';test('direto',()=>increment(1));" };
  assert.equal(unit(analyze(snapshotInput(extra))).decision, 'approved');
  extra['src/a.test.ts'] = "import{increment}from'./a';const test=(name,fn)=>{};test('falso',()=>increment(1));";
  assert.notEqual(unit(analyze(snapshotInput(extra))).decision, 'approved');
});

test('V3 alias e reexport mantêm homônimos separados e não selecionam arrows', /** Preserva identidade lexical e seleção científica existentes. */ () => {
  const result = analyze(snapshotInput({
    'src/index.ts': "export{increment as calculate}from'./a';",
    'src/other.ts': '/** Outro incremento. */export function increment(x:number){return x+2;} export const arrow=(x)=>x+1;',
    'src/a.test.ts': "import{test}from'vitest';import{calculate}from'./index';test('alias',()=>calculate(1));",
  }));
  assert.equal(unit(result).decision, 'approved');
  assert.equal(result.instances.find(/** Localiza homônimo sem evidência. */ item => item.function.path === 'src/other.ts').criteria.direct_test_association.status, 'rejected');
  assert.ok(!result.instances.some(/** Exclui expressão arrow da unidade de análise. */ item => item.function.name === 'arrow'));
});

test('V3 mantém H-F06 e dependências de tipos externas não geram bloqueio global', /** Separa ambiente de compilação de trivialidade comprovada. */ () => {
  const result = analyze(snapshotInput({ 'tsconfig.json': '{"extends":"@external/tsconfig","compilerOptions":{"types":["node"]}}' }));
  assert.equal(unit(result).decision, 'approved');
  const trivial = analyze(snapshotInput({ 'src/a.ts': '/** Retorna constante. */export function increment(x:number){return 1+2;}' }));
  assert.equal(unit(trivial).criteria.nontrivial_implementation.status, 'rejected');
});

test('V3 recurso binário importado existe sem virar fonte TypeScript', /** Confere presença pelo inventário sem decodificar bytes do recurso. */ () => {
  const input = snapshotInput({ 'src/a.test.ts': "import{test}from'vitest';import{increment}from'./a';import fixture from '../fixtures/binary.bin';test('direto',()=>increment(1));" });
  const result = analyze(input);
  assert.equal(unit(result).decision, 'approved');
  assert.ok(result.local_dependencies.some(/** Confere aresta para recurso binário preservado. */ item => item.resolved_path === 'fixtures/binary.bin'));
});

test('V3 preset e alias declarativos não aprovam associação ao alvo substituído', /** Preserva proteção de substituição em configurações resolvíveis. */ () => {
  for (const config of [
    { 'vitest.config.ts': "export default {resolve:{alias:{'./a':'./replacement.ts'}}};" },
    { 'vitest.config.ts': "const alias={'./a':'./replacement.ts'};const base={resolve:{alias}};export default {...base};" },
    { 'vitest.config.ts': "export{default}from'./base.ts';", 'base.ts': "export default {resolve:{alias:{'./a':'./replacement.ts'}}};" },
    { 'jest.config.json': '{"preset":"./preset.json"}', 'preset.json': JSON.stringify({ moduleNameMapper: { '^\\./a$': './replacement.ts' } }) },
  ]) {
    const result = analyze(snapshotInput(config));
    assert.equal(unit(result).criteria.direct_test_association.status, 'not_evaluated');
    assert.notEqual(unit(result).decision, 'approved');
  }
});

test('V3 setup truncado afeta contexto e omissão documental independente só afeta busca', /** Localiza limites sem aprovar por omissão de setup conhecido. */ () => {
  const input = snapshotInput({ 'vitest.config.ts': "export default {test:{setupFiles:['./setup.ts']}};", 'setup.ts': "import{vi}from'vitest';vi.mock('./src/a');" }, false);
  input.files = input.files.filter(/** Simula limite antes da fonte do setup. */ file => file.path !== 'setup.ts');
  input.issues = [{ code: 'ANALYSIS_TEXT_LIMIT', path: 'setup.ts', message: 'Limite textual.' }];
  assert.equal(unit(analyze(input)).criteria.preserved_local_context.status, 'not_evaluated');
  const independent = snapshotInput({ 'docs/other.md': 'Texto externo sem vínculo.' }, false);
  independent.files = independent.files.filter(/** Omite somente documento que não é evidência positiva. */ file => file.path !== 'docs/other.md');
  independent.issues = [{ code: 'ANALYSIS_TEXT_LIMIT', path: 'docs/other.md', message: 'Limite textual.' }];
  assert.equal(unit(analyze(independent)).decision, 'approved');
});

test('V3 substituição comprovada da API no setup invalida registro sem bloquear mera presença global', /** Não aceita framework mockado ou global test substituído como registro confiável. */ () => {
  for (const setup of ["import{vi}from'vitest';vi.mock('vitest');", 'globalThis.test = (name, callback) => {};']) {
    const result = analyze(snapshotInput({
      'vitest.config.ts': "export default {test:{globals:true,setupFiles:['./setup.ts']}};",
      'setup.ts': setup,
      'src/a.test.ts': "import{increment}from'./a';test('direto',()=>increment(1));",
    }));
    assert.equal(unit(result).criteria.direct_test_association.status, 'not_evaluated');
    assert.notEqual(unit(result).decision, 'approved');
  }
});

test('V3 API de registro reexportada e mockada não comprova teste real', /** Mantém proveniência da API até o módulo local substituído pelo setup. */ () => {
  const result = analyze(snapshotInput({
    'src/test-api.ts': "export{test}from'vitest';",
    'src/a.test.ts': "import{test}from'./test-api';import{increment}from'./a';test('direto',()=>increment(1));",
    'vitest.config.ts': "export default {test:{setupFiles:['./setup.ts']}};",
    'setup.ts': "import{vi}from'vitest';vi.mock('./src/test-api',()=>({test:()=>{}}));",
  }));
  assert.equal(unit(result).criteria.direct_test_association.status, 'not_evaluated');
});

test('V3 spreads de setup e chaves computadas conservam arestas para texto omitido', /** Não perde configuração resolvível quando somente o setup excede o orçamento. */ () => {
  for (const config of [
    "export default {test:{setupFiles:['./setup.ts']}};",
    "const setups=['./setup.ts'];export default {test:{setupFiles:[...setups]}};",
    "const name='setupFiles';const setups=['./setup.ts'];export default {['test']:{[name]:[...setups]}};",
    "const setups=[...setups,'./setup.ts'];export default {test:{setupFiles:[...setups]}};",
  ]) {
    const input = snapshotInput({ 'vitest.config.ts': config, 'setup.ts': "import{vi}from'vitest';vi.mock('./src/a');" + ' '.repeat(2000) }, false);
    input.files = input.files.filter(/** Simula projeção limitada mantendo inventário integral. */ file => file.path !== 'setup.ts');
    input.issues = [{ code: 'ANALYSIS_TEXT_LIMIT', path: 'setup.ts', message: 'Orçamento de 1000 bytes.' }];
    const result = analyze(input);
    assert.equal(unit(result).criteria.preserved_local_context.status, 'not_evaluated', config);
    assert.ok(result.local_dependencies.some(/** Exige vínculo declarativo mesmo sem AST do setup. */ dependency => dependency.from === 'vitest.config.ts' && dependency.resolved_path === 'setup.ts'), config);
  }
});

test('V3 alias em array expandido e chave literal computada continua substituindo alvo', /** Avalia apenas spreads e nomes de propriedades estaticamente resolvíveis. */ () => {
  for (const config of [
    "const aliases=[{find:'./a',replacement:'./fake.ts'}];export default {resolve:{alias:[...aliases]}};",
    "export default {resolve:{['alias']:{['./a']:'./fake.ts'}}};",
    "const key='alias';export default {resolve:{[key]:{'./a':'./fake.ts'}}};",
  ]) {
    const result = analyze(snapshotInput({ 'vitest.config.ts': config }));
    assert.equal(unit(result).criteria.direct_test_association.status, 'not_evaluated', config);
  }
});

test('V3 export nomeado como default resolve identidade local e cadeias externas', /** Segue símbolos exportados sem importar ou executar os módulos candidatos. */ () => {
  for (const extra of [
    { 'vitest.config.ts': "const config={resolve:{alias:{'./a':'./fake.ts'}}};export{config as default};" },
    { 'vitest.config.ts': "export{config as default}from'./base';", 'base.ts': "export const config={resolve:{alias:{'./a':'./fake.ts'}}};" },
    { 'vitest.config.ts': "export{selected as default}from'./bridge';", 'bridge.ts': "export{config as selected}from'./base';", 'base.ts': "export const config={resolve:{alias:{'./a':'./fake.ts'}}};" },
  ]) {
    assert.equal(unit(analyze(snapshotInput(extra))).criteria.direct_test_association.status, 'not_evaluated', JSON.stringify(extra));
  }
});

test('V3 alias de framework externo invalida registro mesmo com resolução npm nula', /** Substituição conhecida de API não é mera dependência externa não instalada. */ () => {
  for (const extra of [
    { 'vitest.config.ts': "export default {resolve:{alias:{vitest:'./fake.ts'}}};" },
    { 'vitest.config.ts': "const aliases=[{find:'vitest',replacement:'./fake.ts'}];export default {resolve:{alias:[...aliases]}};" },
    { 'jest.config.json': JSON.stringify({ moduleNameMapper: { '^@jest/globals$': './fake.ts' } }), 'src/a.test.ts': "import{test}from'@jest/globals';import{increment}from'./a';test('direto',()=>increment(1));" },
    { 'jest.config.json': JSON.stringify({ moduleNameMapper: { '^@jest/globals$': './fake.ts' } }), 'package.json': '{"devDependencies":{"jest":"1"}}', 'src/a.test.ts': "import{increment}from'./a';test('direto',()=>increment(1));" },
  ]) {
    const result = analyze(snapshotInput({ ...extra, 'fake.ts': 'export const test=(name,callback)=>{};' }));
    assert.equal(unit(result).criteria.direct_test_association.status, 'not_evaluated', JSON.stringify(extra));
    assert.notEqual(unit(result).decision, 'approved');
  }
  const independent = analyze(snapshotInput({ 'vitest.config.ts': "const aliases=[{find:'unrelated',replacement:'./fake.ts'}];export default {resolve:{['alias']:[...aliases]}};" }));
  assert.equal(unit(independent).decision, 'approved');
});

test('V3 spies sobre registro global ou namespace de framework invalidam associação sem tipos npm', /** Não depende de declarações ambientes para reconhecer substituição de API conhecida. */ () => {
  for (const [setup, testSource] of [
    ["import{vi}from'vitest';vi.spyOn(globalThis,'test').mockImplementation(()=>{});", "import{increment}from'./a';test('direto',()=>increment(1));"],
    ["import{vi}from'vitest';import*as api from'vitest';vi.spyOn(api,'test').mockImplementation(()=>{});", "import{test}from'vitest';import{increment}from'./a';test('direto',()=>increment(1));"],
    ["import{vi}from'vitest';vi.stubGlobal('test',()=>{});", "import{increment}from'./a';test('direto',()=>increment(1));"],
    ["import{vi}from'vitest';const root=globalThis;vi.spyOn(root,'test').mockImplementation(()=>{});", "import{increment}from'./a';test('direto',()=>increment(1));"],
    ["import{vi}from'vitest';import*as api from'vitest';const copied=api;vi.spyOn(copied,'test').mockImplementation(()=>{});", "import{test}from'vitest';import{increment}from'./a';test('direto',()=>increment(1));"],
  ]) {
    const result = analyze(snapshotInput({
      'vitest.config.ts': "export default {test:{globals:true,setupFiles:['./setup.ts']}};",
      'setup.ts': setup, 'src/a.test.ts': testSource,
    }));
    assert.equal(unit(result).criteria.direct_test_association.status, 'not_evaluated', setup);
    assert.notEqual(unit(result).decision, 'approved');
  }
});

test('V3 spies em objetos locais ou recursos sem relação com registro não bloqueiam teste', /** Evita promover a mera presença de spy ou global a bloqueio de contexto. */ () => {
  for (const setup of [
    "import{vi}from'vitest';const api={test:()=>{}};vi.spyOn(api,'test').mockImplementation(()=>{});",
    "import{vi}from'vitest';const globalThis={test:()=>{}};vi.spyOn(globalThis,'test').mockImplementation(()=>{});",
    "import{vi}from'vitest';vi.spyOn(globalThis,'unrelatedResource').mockImplementation(()=>{});",
  ]) {
    const result = analyze(snapshotInput({
      'vitest.config.ts': "export default {test:{globals:true,setupFiles:['./setup.ts']}};",
      'setup.ts': setup, 'src/a.test.ts': "import{increment}from'./a';test('direto',()=>increment(1));",
    }));
    assert.equal(unit(result).decision, 'approved', setup);
  }
});

test('V3 automock Jest declarativo impede comprovar implementação original sem inventar unmock', /** Mantém falso e ausência como controles positivos e não aplica política Jest ao Vitest. */ () => {
  for (const [config, expected] of [[{ automock: true }, 'not_evaluated'], [{ automock: false }, 'approved'], [{}, 'approved']]) {
    const result = analyze(snapshotInput({
      'jest.config.json': JSON.stringify(config),
      'src/a.test.ts': "import{test}from'@jest/globals';import{increment}from'./a';test('direto',()=>increment(1));",
    }));
    assert.equal(unit(result).criteria.direct_test_association.status, expected);
    assert.equal(unit(result).decision, expected);
  }
  const independent = analyze(snapshotInput({ 'jest.config.json': '{"automock":true}' }));
  assert.equal(unit(independent).decision, 'approved');
  for (const [extra, expected] of [
    [{ 'package.json': '{"jest":{"automock":true}}' }, 'not_evaluated'],
    [{ 'jest.config.json': '{"preset":"./preset.json"}', 'preset.json': '{"automock":true}' }, 'not_evaluated'],
    [{ 'jest.config.json': '{"preset":"./jest.preset.json","automock":false}', 'jest.preset.json': '{"automock":true}' }, 'approved'],
  ]) {
    const result = analyze(snapshotInput({ ...extra, 'src/a.test.ts': "import{test}from'@jest/globals';import{increment}from'./a';test('direto',()=>increment(1));" }));
    assert.equal(unit(result).decision, expected);
  }
});

test('V3 configurações irmãs e inválidas fora do escopo não invalidam identidade selecionada', /** Reproduz os bloqueios do piloto sem descartar diagnósticos do repositório. */ () => {
  const result = analyze(snapshotInput({
    'tsconfig.json': '{"compilerOptions":{"moduleResolution":"bundler"}}',
    'tsconfig.web.json': '{"include":["web/**/*.ts"]}',
    'unrelated/tsconfig.json': '{ broken json',
    'unrelated/tsconfig.build.json': '{}',
  }));
  assert.equal(unit(result).decision, 'approved');
  assert.ok(result.issues.some(/** Confere permanência do diagnóstico não efetivo. */ issue => issue.code === 'INVALID_TSCONFIG' && issue.path === 'unrelated/tsconfig.json'));
  assert.ok(result.issues.some(/** Coexistência continua auditável, sem veto universal. */ issue => issue.code === 'MULTIPLE_TSCONFIGS'));
});

test('V3 include exclude extends e project references localizam opções e divergências necessárias', /** Mantém pendência quando duas configurações aplicáveis resolvem a chamada para identidades diferentes. */ () => {
  const texts = {
    'src/a.test.ts': "import{test}from'vitest';import{increment}from'@app/a';test('direto',()=>increment(1));",
    'tsconfig.json': '{"extends":"./base.json","exclude":["web"],"references":[{"path":"./web"}]}',
    'base.json': '{"compilerOptions":{"baseUrl":".","paths":{"@app/*":["src/*"]}}}',
    'tsconfig.test.json': '{"include":["other/**/*.ts"],"compilerOptions":{"baseUrl":".","paths":{"@app/*":["other/*"]}}}',
    'web/tsconfig.json': '{"include":["*.ts"],"compilerOptions":{"baseUrl":".","paths":{"@app/*":["./*"]}}}',
    'web/a.ts': '/** Outro alvo. */export function increment(x:number){return x+2;}',
    'other/a.ts': '/** Outro alvo. */export function increment(x:number){return x+3;}',
  };
  assert.equal(unit(analyze(snapshotInput(texts))).decision, 'approved');
  const unselected = analyze(snapshotInput({ ...texts, 'tsconfig.unselected.json': '{ broken json', 'tsconfig.tooling.json': '{"compilerOptions":{"paths":{"@app/*":["other/*"]}}}' }));
  assert.equal(unit(unselected).decision, 'approved');
  texts['tsconfig.test.json'] = '{"include":["src/**/*.test.ts"],"compilerOptions":{"baseUrl":".","paths":{"@app/*":["other/*"]}}}';
  const conflicting = analyze(snapshotInput(texts));
  assert.equal(unit(conflicting).criteria.preserved_local_context.status, 'not_evaluated');
  assert.match(unit(conflicting).criteria.preserved_local_context.reason, /AMBIGUOUS_MODULE_RESOLUTION.*src\/a.test.ts/);
  texts['tsconfig.json'] = '{ broken json';
  const invalid = analyze(snapshotInput(texts));
  assert.match(unit(invalid).criteria.preserved_local_context.reason, /INVALID_TSCONFIG.*tsconfig.json/);
});

test('V3 métodos auxiliares incertos e outro callback não apagam testemunha direta confiável', /** Reproduz map e Map/get sem exigir tipos externos para todas as chamadas do teste. */ () => {
  const result = analyze(snapshotInput({
    'src/a.test.ts': "import{test}from'vitest';import{increment}from'./a';test('positivo',()=>{increment(1);const replay:any={};replay!.days.map(x=>x);const s:any={};s.meta.get('x');});test('outro',callback);",
    'other/uncertain.test.ts': "import{test}from'vitest';import{increment}from'../src/a';test('outro',()=>{const receiver:any=increment;receiver(1);});",
  }));
  assert.equal(unit(result).decision, 'approved');
  assert.ok(result.issues.some(/** Conserva diagnóstico de chamada/registro não usado como prova. */ issue => issue.code === 'UNCERTAIN_TEST_EXECUTION'));
  for (const testSource of [
    "import{test}from'vitest';import{increment}from'./a';test('incerto',()=>{const target:any=increment;target(1);});",
    "import{test}from'vitest';import{increment}from'./missing';test('ausente',()=>increment(1));",
  ]) assert.equal(unit(analyze(snapshotInput({ 'src/a.test.ts': testSource }))).criteria.direct_test_association.status, 'not_evaluated');
});

test('V3 documento comprovado resiste a menção incidental mas seção ambígua não vira prova', /** Distingue proveniência positiva de mera presença de texto em um array documental. */ () => {
  const positive = analyze(snapshotInput({ 'README.md': 'O arquivo `src/a.ts` integra o projeto.' }));
  assert.equal(unit(positive).decision, 'approved');
  assert.ok(positive.issues.some(/** Não oculta a ambiguidade incidental. */ issue => issue.code === 'AMBIGUOUS_DOCUMENTATION'));
  const absent = analyze(snapshotInput({ 'src/a.ts': 'export function increment(x:number){return x+1;}', 'README.md': 'O arquivo `src/a.ts` integra o projeto.' }));
  assert.equal(unit(absent).criteria.associated_documentation.status, 'not_evaluated');
  assert.match(unit(absent).criteria.preserved_local_context.reason, /AMBIGUOUS_DOCUMENTATION.*README.md/);
  const ambiguous = analyze(snapshotInput({
    'src/a.ts': 'export function increment(x:number){return x+1;}\nexport function other(x:number){return x+2;}',
    'README.md': '# src/a.ts#increment\nDescrição vinculada.\n## [Vínculo incerto](src/a.ts)\nProsa sem titularidade única.',
  }));
  assert.equal(unit(ambiguous).documentation.length, 0);
  assert.equal(unit(ambiguous).criteria.associated_documentation.status, 'not_evaluated');
});

test('V3 exports workspace resolvem strings subpaths e condições somente para fontes preservadas', /** Não usa heurística de nome de diretório nem presume execução de build. */ () => {
  const texts = {
    'src/a.test.ts': "import{test}from'vitest';import{increment}from'@local/math';import{extra}from'@local/math/extra';test('direto',()=>increment(1));",
    'packages/math/package.json': '{"name":"@local/math","exports":{".":{"source":"./src/index.ts","types":"./dist/index.d.ts","import":"./dist/index.js"},"./extra":"./src/extra.ts"}}',
    'packages/math/src/index.ts': "export{increment}from'../../../src/a';",
    'packages/math/src/extra.ts': 'export const extra=1;',
  };
  const result = analyze(snapshotInput(texts));
  assert.equal(unit(result).decision, 'approved');
  assert.ok(result.local_dependencies.some(/** Exige caminho exato declarado no manifest. */ item => item.specifier === '@local/math' && item.resolved_path === 'packages/math/src/index.ts'));
  assert.ok(result.local_dependencies.some(/** Confere subpath explícito sem dedução de nome de diretório. */ item => item.specifier === '@local/math/extra' && item.resolved_path === 'packages/math/src/extra.ts'));
  texts['packages/math/package.json'] = '{"name":"@local/math","exports":"./dist/index.js"}';
  const missing = analyze(snapshotInput(texts));
  assert.equal(unit(missing).criteria.direct_test_association.status, 'not_evaluated');
  assert.notEqual(unit(missing).decision, 'approved');
  texts['packages/math/package.json'] = '{"name":"@local/math","exports":{".":{"source":"./src/index.ts","import":"./src/other.ts"}}}';
  texts['packages/math/src/other.ts'] = "export{increment}from'../../../src/a';";
  assert.notEqual(unit(analyze(snapshotInput(texts))).decision, 'approved');
});

test('V3 workspace resolve autorreferência e padrões exportados sem escolher entre pacotes homônimos', /** Respeita somente o contrato de exports, inclusive ao resolver o próprio pacote. */ () => {
  const texts = {
    'src/a.test.ts': "import{test}from'vitest';import{increment}from'@local/math/entry';test('direto',()=>increment(1));",
    'packages/math/package.json': '{"name":"@local/math","exports":{".":"./src/index.ts","./*":{"types":"./src/*.ts"}}}',
    'packages/math/src/index.ts': "export{increment}from'../../../src/a';",
    'packages/math/src/entry.ts': "export{increment}from'@local/math';",
  };
  assert.equal(unit(analyze(snapshotInput(texts))).decision, 'approved');
  const duplicate = analyze(snapshotInput({ ...texts, 'packages/duplicate/package.json': '{"name":"@local/math","exports":"./index.ts"}', 'packages/duplicate/index.ts': "export{increment}from'../../src/a';" }));
  assert.notEqual(unit(duplicate).decision, 'approved');
});

test('V3 declarações de módulo com reexport npm ausente não são raízes globais', /** Conserva o requisito externo e exige receptor concreto para associação de método. */ () => {
  const result = analyze(snapshotInput({ 'tools/test/shim.d.ts': "export*from'../../../node_modules/@external/plugin/dist/index';" }));
  assert.equal(unit(result).decision, 'approved');
  assert.ok(result.execution_requirements.some(/** Preserva necessidade de tipos npm sem veto global. */ item => item.type === 'external_dependency' && item.path === 'tools/test/shim.d.ts'));
  const uncertain = analyze(snapshotInput({
    'src/a.ts': '/** Processa a entrada. */export class A{/** Incrementa. */increment(x:number){return x+1;}}',
    'src/a.test.ts': "import{test}from'vitest';import{A}from'./a';test('incerto',()=>{const receiver:any=new A();receiver.increment(1);});",
  }));
  assert.equal(unit(uncertain).criteria.direct_test_association.status, 'not_evaluated');
});
