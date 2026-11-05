"""Verifica vínculos reais do auxiliar e rejeição de protocolos adulterados."""

from copy import deepcopy
import hashlib
import io
import json
import subprocess

import pytest

from robo_minerador.extraction_analyzer import Analyzer, AnalyzerUnavailable, preflight, validate_output
from robo_minerador import extraction, extraction_analyzer
from test_extraction_input import candidate
from test_extraction_snapshot import snapshot_project  # noqa: F401
from robo_minerador.extraction_snapshot import SnapshotInventory, project_snapshot


@pytest.fixture(scope="module")
def settings():
    """Usa Node e TypeScript instalados ou informa precisamente o requisito ausente."""
    try:
        return preflight()
    except ValueError as error:
        pytest.skip(str(error))


def snapshot_arguments(projection):
    """Explicita a interface V3 que a integração e o loader devem fornecer."""
    return {'snapshot': projection.snapshot, 'inventory': projection.inventory,
            'analysis_complete': projection.analysis_complete}


V3_TEXTS = {
    'src/a.ts': '\ufeff/** Incrementa a entrada. */\r\nexport function increment(x:number){return x+1;}\r\n',
    'src/a.test.ts': "import{test}from'vitest';import{increment}from'./a';import fixture from '../fixtures/binary.bin';test('direto',()=>increment(Number(process.env.INPUT)));",
    'vitest.config.ts': "import{defineConfig}from'vitest/config';export default defineConfig({test:{environment:'node'}});",
    'package.json': '{"devDependencies":{"vitest":"1"}}',
    'fixtures/binary.bin': b'\0\xff\x80',
}


@pytest.mark.parametrize('config_extension', ['ts', 'js'])
@pytest.mark.parametrize('setup_extension', ['ts', 'js'])
@pytest.mark.parametrize('allow_js', [False, True])
@pytest.mark.parametrize('mocked', [False, True])
def test_v3_js_context_keeps_mock_evidence_in_real_projection(
        snapshot_project, settings, config_extension, setup_extension, allow_js, mocked):
    """Cruza ASTs de contexto JS/TS com Git real sem habilitar execução ou promover mocks."""
    config_path = f'vitest.config.{config_extension}'
    setup_path = f'setup.{setup_extension}'
    texts = {**V3_TEXTS, 'vitest.config.ts': 'export {};',
             config_path: f"export default {{test:{{setupFiles:['./{setup_path}']}}}};",
             setup_path: "import{vi}from'vitest';vi.mock('./src/a');" if mocked else 'export {};',
             'tools/irrelevant.js': "import{vi}from'vitest';vi.mock('../src/a');"}
    texts['tsconfig.json'] = json.dumps({'files': [name for name in texts if name.endswith(('.ts', '.js'))],
                                       'compilerOptions': {'allowJs': allow_js}})
    _, _, projection = snapshot_project(texts)
    result = Analyzer(settings).analyze(projection.files, projection.issues, **snapshot_arguments(projection))
    unit = next(item for item in result['instances'] if item['function']['path'] == 'src/a.ts')
    assert unit['decision'] == ('not_evaluated' if mocked else 'approved')
    assert unit['validation']['status'] == 'not_evaluated'
    assert validate_output(result, projection.files, settings['analyzer'],
                           **snapshot_arguments(projection)) == result


@pytest.mark.parametrize('allow_js', [False, True])
@pytest.mark.parametrize('automock', [False, True])
def test_v3_imported_js_preset_keeps_policy_in_real_projection(snapshot_project, settings, allow_js, automock):
    """Preserva configuração e preset JS importado no atestado real e no replay Python."""
    texts = {**V3_TEXTS,
             'jest.config.js': "export default {preset:'./preset.js'};",
             'preset.js': "import configuration from './preset-value.js';export default configuration;",
             'preset-value.js': 'export default ' + json.dumps({'automock': automock}) + ';',
             'src/a.test.ts': "import{test}from'@jest/globals';import{increment}from'./a';test('direto',()=>increment(1));"}
    texts['tsconfig.json'] = json.dumps({'files': [name for name in texts if name.endswith(('.ts', '.js'))],
                                       'compilerOptions': {'allowJs': allow_js}})
    _, _, projection = snapshot_project(texts)
    result = Analyzer(settings).analyze(projection.files, projection.issues, **snapshot_arguments(projection))
    unit = next(item for item in result['instances'] if item['function']['path'] == 'src/a.ts')
    assert unit['decision'] == ('not_evaluated' if automock else 'approved')
    assert validate_output(result, projection.files, settings['analyzer'],
                           **snapshot_arguments(projection)) == result


@pytest.mark.parametrize(('case', 'related'), [
    ('suffixes', False), ('suffixes', True),
    ('runtime', False), ('runtime', True),
    ('alias', False), ('alias', True),
    ('automock', False), ('automock', True),
    ('globals', False), ('globals', True),
])
def test_v3_review_fixes_preserve_identity_in_real_projection(snapshot_project, settings, case, related):
    """Confere pares reais de identidade e escopo via Git, projeção, Analyzer e replay."""
    texts = dict(V3_TEXTS)
    target = 'src/a.ts'
    expected = 'not_evaluated' if related else 'approved'
    if case == 'suffixes':
        texts.update({
            'tsconfig.json': '{"compilerOptions":{"moduleSuffixes":[".native",""]}}',
            'tsconfig.test.json': ('{"include":["src/**/*.test.ts"]}' if related else
                                   '{"extends":"./tsconfig.json","include":["src/**/*.test.ts"]}'),
            'src/a.native.ts': '/** Incrementa nativamente. */export function increment(x:number){return x+2;}',
        })
        target = 'src/a.native.ts'
    elif case == 'runtime':
        texts.update({
            'src/a.test.ts': "import{test}from'vitest';import{increment}from'@local/math';test('direto',()=>increment(1));",
            'packages/math/package.json': ('{"name":"@local/math","exports":{"types":"./src/index.ts",'
                                           '"import":"./dist/index.js"}}'),
            'packages/math/src/index.ts': "export{increment}from'../../../src/a';",
        })
        if related:
            texts['packages/math/dist/index.js'] = 'export function increment(x){return x+9;}'
    else:
        prefix = '' if related else 'other/'
        texts['vitest.config.ts'] = 'export default {test:{globals:true}};'
        if case == 'alias':
            texts[prefix + 'vitest.config.ts'] = "export default {resolve:{alias:{'./a':'./replacement.ts'}}};"
            texts[prefix + 'replacement.ts'] = 'export function increment(x:number){return x+9;}'
        elif case == 'automock':
            texts[prefix + 'jest.config.json'] = '{"automock":true}'
            texts['src/a.test.ts'] = "import{test}from'@jest/globals';import{increment}from'./a';test('direto',()=>increment(1));"
        else:
            texts[prefix + 'vitest.config.ts'] = "export default {test:{globals:true,setupFiles:['./setup.ts']}};"
            texts[prefix + 'setup.ts'] = 'globalThis.test = (name, callback) => {};'
            texts['src/a.test.ts'] = "import{increment}from'./a';test('direto',()=>increment(1));"
    _, _, projection = snapshot_project(texts)
    result = Analyzer(settings).analyze(projection.files, projection.issues, **snapshot_arguments(projection))
    unit = next(item for item in result['instances'] if item['function']['path'] == target)
    assert unit['decision'] == expected
    assert unit['validation'] == {'status': 'not_evaluated', 'executed_test_cases': None,
                                  'suite_passed': None, 'external_services': 'not_evaluated'}
    assert validate_output(result, projection.files, settings['analyzer'],
                           **snapshot_arguments(projection)) == result


def test_v3_public_constructor_cannot_attest_self_declared_git_data(settings):
    """Hashes autoconsistentes e SHAs inventados não substituem abertura do store."""
    files, entries = [], []
    for path, content in V3_TEXTS.items():
        raw = content.encode('utf-8') if isinstance(content, str) else content
        entries.append({'path': path, 'mode': '100644', 'type': 'blob', 'size': len(raw),
                        'oid': hashlib.sha1(f'blob {len(raw)}\0'.encode() + raw).hexdigest()})
        if isinstance(content, str):
            files.append({'path': path, 'content': content, 'sha256': hashlib.sha256(raw).hexdigest()})
    reference = {'format': 'git-bare-v1', 'status': 'complete', 'repository_id': 42,
                 'cache_key': '42/' + 'a' * 40, 'commit_sha': 'a' * 40, 'tree_oid': 'b' * 40,
                 'manifest_sha256': 'c' * 64, 'file_count': len(entries),
                 'total_blob_bytes': sum(entry['size'] for entry in entries)}
    with pytest.raises(TypeError, match='project_snapshot'):
        inventory = SnapshotInventory(entries, reference, files, True, [])
        Analyzer(settings).analyze(files, [], snapshot=reference, inventory=inventory,
                                   analysis_complete=True)
    with pytest.raises(TypeError, match='project_snapshot'):
        SnapshotInventory(entries, reference, files, True, [], _capability=object())


def test_v3_real_store_projection_and_node_approve_without_execution(snapshot_project, settings):
    """Integra Git local, projeção atestada, Node e replay Python sem executar candidato."""
    store, artifact, projection = snapshot_project(V3_TEXTS)
    analyzer = Analyzer(settings)
    result = analyzer.analyze(projection.files, projection.issues, **snapshot_arguments(projection))
    unit = result['instances'][0]
    assert unit['decision'] == 'approved'
    assert unit['function']['path'] == 'src/a.ts'
    assert unit['validation']['executed_test_cases'] is None
    assert unit['validation']['suite_passed'] is None
    assert not result['issues']
    assert {item['type'] for item in result['execution_requirements']} >= {
        'test_configuration', 'environment', 'external_dependency'}
    # O loader obtém novo atestado offline; saída JSON não substitui o reader.
    reopened = project_snapshot(store, store.open_verified(artifact.reference()))
    assert validate_output(result, reopened.files, settings['analyzer'],
                           **snapshot_arguments(reopened)) == result


@pytest.mark.parametrize(('setup', 'test_source', 'expected'), [
    ("import{vi}from'vitest';vi.spyOn(globalThis,'test').mockImplementation(()=>{});",
     "import{increment}from'./a';test('direto',()=>increment(1));", 'not_evaluated'),
    ("import{vi}from'vitest';import*as api from'vitest';vi.spyOn(api,'test').mockImplementation(()=>{});",
     "import{test}from'vitest';import{increment}from'./a';test('direto',()=>increment(1));", 'not_evaluated'),
    ("import{vi}from'vitest';vi.stubGlobal('test',()=>{});",
     "import{increment}from'./a';test('direto',()=>increment(1));", 'not_evaluated'),
    ("import{vi}from'vitest';const api={test:()=>{}};vi.spyOn(api,'test').mockImplementation(()=>{});",
     "import{increment}from'./a';test('direto',()=>increment(1));", 'approved'),
])
def test_v3_setup_spies_keep_registration_identity(snapshot_project, settings, setup, test_source, expected):
    """Integra AST de setup sem tipos npm ao store real e à conferência de promoções."""
    texts = {**V3_TEXTS, 'setup.ts': setup, 'src/a.test.ts': test_source,
             'vitest.config.ts': "export default {test:{globals:true,setupFiles:['./setup.ts']}};"}
    _, _, projection = snapshot_project(texts)
    result = Analyzer(settings).analyze(projection.files, projection.issues, **snapshot_arguments(projection))
    unit = next(item for item in result['instances'] if item['function']['path'] == 'src/a.ts')
    assert unit['criteria']['direct_test_association']['status'] == expected
    assert unit['decision'] == expected
    if expected != 'approved':
        for criterion in unit['criteria'].values():
            criterion['status'] = 'approved'
        unit['decision'], unit['reasons'] = 'approved', []
        result['issues'] = []
        with pytest.raises(AnalyzerUnavailable):
            validate_output(result, projection.files, settings['analyzer'], **snapshot_arguments(projection))


@pytest.mark.parametrize(('configuration', 'expected'), [
    ('{"automock":true}', 'not_evaluated'), ('{"automock":false}', 'approved'), ('{}', 'approved'),
])
def test_v3_jest_automock_is_static_target_uncertainty(snapshot_project, settings, configuration, expected):
    """Preserva controle positivo e não inventa exceção unmock para alvo importado."""
    texts = {**V3_TEXTS, 'jest.config.json': configuration,
             'src/a.test.ts': "import{test}from'@jest/globals';import{increment}from'./a';test('direto',()=>increment(1));"}
    _, _, projection = snapshot_project(texts)
    result = Analyzer(settings).analyze(projection.files, projection.issues, **snapshot_arguments(projection))
    unit = next(item for item in result['instances'] if item['function']['path'] == 'src/a.ts')
    assert unit['criteria']['direct_test_association']['status'] == expected
    assert unit['decision'] == expected
    assert unit['validation']['executed_test_cases'] is None


@pytest.mark.parametrize('case', ['sibling_config', 'uncertain_calls', 'incidental_doc',
                                 'workspace_exports', 'external_declaration'])
def test_v3_scoped_static_evidence_uses_real_store(snapshot_project, settings, case):
    """Integra os cinco sobrebloqueios do piloto com bytes Git e replay Python reais."""
    texts = dict(V3_TEXTS)
    if case == 'sibling_config':
        texts.update({'tsconfig.json': '{}', 'tsconfig.web.json': '{"include":["web/**/*.ts"]}',
                      'other/tsconfig.json': '{ broken json'})
    elif case == 'uncertain_calls':
        texts['src/a.test.ts'] = ("import{test}from'vitest';import{increment}from'./a';"
                                  "test('direto',()=>{increment(1);const s:any={};s.meta.get('x');"
                                  "const replay:any={};replay.days.map(x=>x);});test('outro',callback);")
    elif case == 'incidental_doc':
        texts['README.md'] = 'O arquivo `src/a.ts` faz parte do projeto.'
    elif case == 'workspace_exports':
        texts.update({'packages/math/package.json': '{"name":"@local/math","exports":{".":{"types":"./src/index.ts","default":"./dist/index.js"}}}',
                      'packages/math/src/index.ts': "export{increment}from'../../../src/a';",
                      'src/a.test.ts': "import{test}from'vitest';import{increment}from'@local/math';test('direto',()=>increment(1));"})
    else:
        texts['tools/test/shim.d.ts'] = "export*from'../../../node_modules/@external/plugin/dist/index';"
    _, _, projection = snapshot_project(texts)
    result = Analyzer(settings).analyze(projection.files, projection.issues, **snapshot_arguments(projection))
    unit = next(item for item in result['instances'] if item['function']['path'] == 'src/a.ts')
    assert unit['decision'] == 'approved'
    assert unit['validation']['executed_test_cases'] is None
    if case in ('sibling_config', 'uncertain_calls', 'incidental_doc'):
        assert result['issues']
    if case == 'external_declaration':
        assert any(item['type'] == 'external_dependency' and item['path'] == 'tools/test/shim.d.ts'
                   for item in result['execution_requirements'])


def test_v3_effective_config_divergence_keeps_specific_reason_and_refuses_forgery(snapshot_project, settings):
    """Configurações efetivamente aplicáveis não podem trocar a identidade necessária silenciosamente."""
    texts = {**V3_TEXTS,
             'src/a.test.ts': "import{test}from'vitest';import{increment}from'@app/a';test('direto',()=>increment(1));",
             'tsconfig.json': '{"compilerOptions":{"baseUrl":".","paths":{"@app/*":["src/*"]}}}',
             'tsconfig.test.json': '{"include":["src/**/*.test.ts"],"compilerOptions":{"baseUrl":".","paths":{"@app/*":["other/*"]}}}',
             'other/a.ts': '/** Incremento alternativo. */export function increment(x:number){return x+2;}'}
    _, _, projection = snapshot_project(texts)
    result = Analyzer(settings).analyze(projection.files, projection.issues, **snapshot_arguments(projection))
    unit = next(item for item in result['instances'] if item['function']['path'] == 'src/a.ts')
    reason = unit['criteria']['preserved_local_context']['reason']
    assert unit['criteria']['preserved_local_context']['status'] == 'not_evaluated'
    assert 'AMBIGUOUS_MODULE_RESOLUTION' in reason and 'src/a.test.ts' in reason
    assert 'tsconfig.test.json' in reason
    for criterion in unit['criteria'].values():
        criterion['status'] = 'approved'
    unit['decision'], unit['reasons'] = 'approved', []
    result['issues'] = []
    with pytest.raises(AnalyzerUnavailable):
        validate_output(result, projection.files, settings['analyzer'], **snapshot_arguments(projection))


@pytest.mark.parametrize('damage', ['plain_inventory', 'reference', 'inventory', 'text',
                                    'missing_text', 'output_mode', 'promoted', 'requirements'])
def test_v3_rejects_untrusted_inputs_and_forged_output(snapshot_project, settings, damage):
    """Recusa adulteração de bytes, vínculo ao manifesto, modo e decisões do auxiliar."""
    _, _, projection = snapshot_project(V3_TEXTS)
    arguments = snapshot_arguments(projection)
    result = Analyzer(settings).analyze(projection.files, projection.issues, **arguments)
    files = deepcopy(projection.files)
    if damage == 'plain_inventory':
        arguments['inventory'] = list(projection.inventory)
    elif damage == 'reference':
        arguments['snapshot'] = {**projection.snapshot, 'manifest_sha256': 'd' * 64}
    elif damage == 'inventory':
        projection.inventory.pop()
    elif damage == 'text':
        files[0]['content'] += ' '
    elif damage == 'missing_text':
        files.pop()
    elif damage == 'output_mode':
        for key in ('snapshot', 'inventory', 'analysis_complete', 'execution_requirements'):
            del result[key]
    elif damage == 'promoted':
        result['instances'][0]['criteria']['direct_test_association']['reason'] = 'Promoção forjada.'
    elif damage == 'requirements':
        result['execution_requirements'] = []
    with pytest.raises(AnalyzerUnavailable):
        validate_output(result, files, settings['analyzer'], **arguments)


def test_v3_search_budget_does_not_reject_absent_evidence(snapshot_project, settings, monkeypatch):
    """Busca textual limitada mantém ausências pendentes, preservando integridade Git."""
    from robo_minerador import extraction_snapshot

    texts = {'src/a.ts': 'export function increment(x:number){return x+1;}',
             'docs/a.md': 'Descrição longa. ' * 30,
             'src/z.test.ts': "import{test}from'vitest';import{increment}from'./a';test('direto',()=>increment(1));"}
    store, artifact, _ = snapshot_project(texts)
    monkeypatch.setattr(extraction_snapshot.config, 'SNAPSHOT_MAX_TEXT_BYTES', 50, raising=False)
    projection = project_snapshot(store, artifact)
    result = Analyzer(settings).analyze(projection.files, projection.issues, **snapshot_arguments(projection))
    unit = result['instances'][0]
    assert not projection.analysis_complete
    assert unit['decision'] == 'not_evaluated'
    assert unit['criteria']['associated_documentation']['status'] == 'not_evaluated'
    assert unit['criteria']['direct_test_association']['status'] == 'not_evaluated'
    with pytest.raises(AnalyzerUnavailable):
        Analyzer(settings).analyze(projection.files, projection.issues,
                                   **{**snapshot_arguments(projection), 'analysis_complete': True})


def test_v3_store_budget_keeps_spread_setup_dependency(snapshot_project, settings, monkeypatch):
    """Orçamento real omite setup grande sem perder sua aresta declarativa verificável."""
    from robo_minerador import extraction_snapshot

    texts = {**V3_TEXTS,
             'vitest.config.ts': "const setups=['./setup.ts'];export default {test:{setupFiles:[...setups]}};",
             'setup.ts': "import{vi}from'vitest';vi.mock('./src/a');" + ' ' * 2000}
    monkeypatch.setattr(extraction_snapshot.config, 'SNAPSHOT_MAX_TEXT_BYTES', 1000, raising=False)
    _, _, projection = snapshot_project(texts)
    assert not projection.analysis_complete
    assert 'setup.ts' not in {file['path'] for file in projection.files}
    assert 'setup.ts' in {entry['path'] for entry in projection.inventory}
    assert any(issue['code'] == 'ANALYSIS_TEXT_LIMIT' and issue['path'] == 'setup.ts'
               for issue in projection.issues)
    result = Analyzer(settings).analyze(projection.files, projection.issues,
                                        **snapshot_arguments(projection))
    unit = next(item for item in result['instances'] if item['function']['path'] == 'src/a.ts')
    assert unit['criteria']['preserved_local_context']['status'] == 'not_evaluated'
    assert {'from': 'vitest.config.ts', 'specifier': './setup.ts',
            'resolved_path': 'setup.ts'} in result['local_dependencies']
    unit['criteria']['preserved_local_context']['status'] = 'approved'
    unit['decision'], unit['reasons'] = 'approved', []
    result['issues'] = []
    with pytest.raises(AnalyzerUnavailable):
        validate_output(result, projection.files, settings['analyzer'], **snapshot_arguments(projection))


@pytest.mark.parametrize('risk', ['missing_identity', 'setup_mock', 'shadowed_api'])
def test_v3_negative_static_associations_remain_unapproved(snapshot_project, settings, risk):
    """Mantém proteções reais de identidade, mocks conhecidos e APIs sombreadas."""
    texts = dict(V3_TEXTS)
    if risk == 'missing_identity':
        texts['src/a.test.ts'] = "import{test}from'vitest';import{increment}from'./missing';test('direto',()=>increment(1));"
    elif risk == 'setup_mock':
        texts['vitest.config.ts'] = "export default {test:{setupFiles:['./setupfilename.ts']}};"
        texts['setupfilename.ts'] = "import{vi}from'vitest';vi.mock('./src/a',()=>({increment:()=>0}));"
    else:
        texts['src/a.test.ts'] = "import{increment}from'./a';const test=(name,fn)=>{};test('falso',()=>increment(1));"
    _, _, projection = snapshot_project(texts)
    result = Analyzer(settings).analyze(projection.files, projection.issues, **snapshot_arguments(projection))
    unit = next(item for item in result['instances'] if item['function']['path'] == 'src/a.ts')
    assert unit['decision'] != 'approved'
    assert unit['criteria']['direct_test_association']['status'] != 'approved'
    # Mesmo removendo diagnósticos, uma promoção de todos os critérios é recusada.
    for criterion in unit['criteria'].values():
        criterion['status'] = 'approved'
    unit['decision'], unit['reasons'] = 'approved', []
    result['issues'] = []
    with pytest.raises(AnalyzerUnavailable):
        validate_output(result, projection.files, settings['analyzer'], **snapshot_arguments(projection))


@pytest.fixture(scope="module")
def analysis(settings):
    """Analisa alias e documentação com teste parametrizado sem executar candidatos."""
    texts = {
        "src/math.ts": "/** Dobra a entrada e acrescenta uma unidade. */\nexport function double(x: number) { return x * 2 + 1; }\n",
        "src/index.ts": "export { double as calculate } from './math';\n",
        "tests/math.test.ts": "import { test } from 'vitest';\nimport { calculate } from '@app/index';\ntest.each([1,2])('calculate', (x) => { calculate(x); });\n",
        "tsconfig.json": '{"compilerOptions":{"baseUrl":".","paths":{"@app/*":["src/*"]}}}',
        "package.json": '{"devDependencies":{"vitest":"1"}}',
    }
    files = [{"path": path, "content": content, "sha256": hashlib.sha256(content.encode()).hexdigest()}
             for path, content in texts.items()]
    analyzer = Analyzer(settings)
    try:
        return files, analyzer.analyze(files, [])
    finally:
        analyzer.cancel()


def test_real_alias_documentation_and_unexecuted_counts(analysis):
    """Confirma resolução ao símbolo original e preserva contagens executadas nulas."""
    files, result = analysis
    assert len(result["instances"]) == 1
    unit = result["instances"][0]
    assert unit["decision"] == "approved"
    assert unit["function"]["path"] == "src/math.ts"
    assert unit["documentation"][0]["original_text"] == files[0]["content"].splitlines()[0]
    assert len(unit["tests"]) == 1
    assert unit["tests"][0]["parameterized"] is True
    assert unit["tests"][0]["association"]["resolved_target"]["name"] == "double"
    assert unit["validation"]["executed_test_cases"] is None
    assert unit["validation"]["suite_passed"] is None
    assert any(item["specifier"] == "vitest" and item["resolved_path"] is None
               for item in result["local_dependencies"])


@pytest.mark.parametrize("damage", ["duplicate", "decision", "snippet", "line", "target", "validation",
                                     "missing_context", "documentation", "criterion", "unknown_field"])
def test_malformed_protocol_never_approves(analysis, settings, damage):
    """Bloqueia associações forjadas, posições erradas e promoção por protocolo inválido."""
    files, original = analysis
    result = deepcopy(original)
    unit = result["instances"][0]
    if damage == "duplicate":
        result["instances"].append(deepcopy(unit))
    elif damage == "decision":
        unit["decision"] = "rejected"
    elif damage == "snippet":
        unit["function"]["reference_code"] = "function forged() {}"
    elif damage == "line":
        unit["function"]["start_line"] = 999
    elif damage == "target":
        unit["tests"][0]["association"]["resolved_target"]["path"] = "src/index.ts"
    elif damage == "validation":
        unit["validation"]["executed_test_cases"] = 2
    elif damage == "missing_context":
        unit["context_paths"].append("missing.json")
    elif damage == "documentation":
        unit["documentation"][0]["original_text"] = "Descrição forjada."
    elif damage == "criterion":
        unit["criteria"]["preserved_local_context"]["status"] = []
    elif damage == "unknown_field":
        unit["tests"][0]["executed_count"] = 2
    with pytest.raises(AnalyzerUnavailable):
        validate_output(result, files, settings["analyzer"])


def test_incomplete_snapshot_never_promoted(analysis, settings):
    """Mantém pendência de contexto quando arquivos foram omitidos antes da análise."""
    files, _ = analysis
    result = Analyzer(settings).analyze(files, [{"code": "BINARY_CONTEXT", "message": "Fixture não inspecionada."}])
    assert result["instances"][0]["decision"] == "not_evaluated"
    assert result["instances"][0]["criteria"]["preserved_local_context"]["status"] == "not_evaluated"


def test_independent_missing_import_keeps_approved_context_and_diagnostics(settings):
    """Aceita independência comprovada sem remover a pendência do repositório."""
    files = [
        {"path": "src/a.ts", "content": "/** Incrementa a entrada. */\nexport function increment(x:number) { return x + 1; }"},
        {"path": "src/a.test.ts", "content": "import{test}from'vitest';import{increment}from'./a';test('local',()=>increment(1));"},
        {"path": "other/b.ts", "content": "import missing from './absent';export const unrelated=missing;"},
    ]
    result = Analyzer(settings).analyze(files, [])
    assert result["instances"][0]["decision"] == "approved"
    assert result["unresolved_references"] == [{"from": "other/b.ts", "specifier": "./absent"}]
    assert any(issue.get("path") == "other/b.ts" for issue in result["issues"])
    assert validate_output(result, files, settings["analyzer"]) == result


@pytest.mark.parametrize("risk", ["implementation", "test", "transitive", "triple_slash",
                                  "ambient", "config", "resource", "unknown", "removed_edge"])
def test_forged_context_approval_is_recomputed_from_snapshot(settings, risk):
    """Recusa promoção adulterada mesmo quando apaga arestas, referências e diagnósticos."""
    texts = {
        "src/a.ts": "/** Incrementa a entrada. */\nexport function increment(x:number) { return x + 1; }",
        "src/a.test.ts": "import{test}from'vitest';import{increment}from'./a';test('local',()=>increment(1));",
    }
    issues = []
    if risk in ("implementation", "test"):
        path = "src/a.ts" if risk == "implementation" else "src/a.test.ts"
        texts[path] = "import './missing';\n" + texts[path]
    elif risk in ("transitive", "removed_edge"):
        texts["src/a.ts"] = "import './bridge';\n" + texts["src/a.ts"]
        texts["src/bridge.ts"] = "import './missing';export {};"
    elif risk == "triple_slash":
        texts["src/a.ts"] = '/// <reference path="./missing.d.ts" />\n' + texts["src/a.ts"]
    elif risk == "ambient":
        texts["other/globals.d.ts"] = '/// <reference path="./missing.d.ts" />\ndeclare const shared: number;'
    elif risk == "config":
        texts["tsconfig.json"] = '{"extends":"./missing.json"}'
    elif risk == "resource":
        texts["src/a.test.ts"] += "readFileSync(dynamicPath);"
    elif risk == "unknown":
        issues = [{"code": "UNKNOWN_CONTEXT", "message": "Alcance não comprovado."}]
    files = [{"path": path, "content": content} for path, content in texts.items()]
    result = Analyzer(settings).analyze(files, issues)
    unit = result["instances"][0]
    assert unit["criteria"]["preserved_local_context"]["status"] == "not_evaluated"
    unit["criteria"]["preserved_local_context"]["status"] = "approved"
    unit["decision"] = "approved"
    unit["reasons"] = []
    if risk != "unknown":
        result["issues"] = []
        result["unresolved_references"] = []
    if risk == "removed_edge":
        result["local_dependencies"] = [item for item in result["local_dependencies"]
                                        if item["specifier"] != "./bridge"]
    with pytest.raises(AnalyzerUnavailable):
        validate_output(result, files, settings["analyzer"])


@pytest.mark.parametrize("extra", [
    {"vitest.config.ts": "import './missing'; export default {};"},
    {"vitest.config.ts": "import './setup'; export default {};",
     "setup.ts": "import './missing'; export {};"},
    {"vitest.config.json": '{"test":{"setupFiles":["./missing.ts"]}}'},
    {"vite.config.ts": "import './missing'; export default {};"},
    {"jest.config.ts": "import './missing'; export default {};"},
    {"jest.config.json": '{"setupFilesAfterEnv":["<rootDir>/missing.ts"]}'},
    {".mocharc.json": '{"require":["./missing.ts"]}'},
    {".mocharc.yaml": "require:\n  - ./missing.ts"},
    {"package.json": '{"jest":{"setupFiles":["./missing.ts"]}}'},
    {"package.json": '{"mocha":{"require":["./missing.ts"]}}'},
    {"src/a.test.ts": "import{test}from'vitest';import{increment}from'./a';"
     "test('env',()=>{process.loadEnvFile('./missing.env');increment(Number(process.env.INPUT));});"},
    {"src/a.test.ts": "import{test}from'vitest';import{increment}from'./a';"
     "import {loadEnvFile as load} from 'node:process';"
     "test('env',()=>{load('./missing.env');increment(1);});"},
])
def test_test_configuration_and_environment_never_allow_forged_context(settings, extra):
    """Mantém contexto pendente e recusa adulteração que apaga dependências ou issues de setup e ambiente."""
    texts = {
        "src/a.ts": "/** Incrementa a entrada. */\nexport function increment(x:number) { return x + 1; }",
        "src/a.test.ts": "import{test}from'vitest';import{increment}from'./a';test('local',()=>increment(1));",
        **extra,
    }
    files = [{"path": path, "content": content} for path, content in texts.items()]
    result = Analyzer(settings).analyze(files, [])
    unit = next(item for item in result["instances"] if item["function"]["path"] == "src/a.ts")
    assert unit["decision"] == "not_evaluated"
    assert unit["criteria"]["preserved_local_context"]["status"] == "not_evaluated"
    assert unit["criteria"]["direct_test_association"]["status"] == "approved"
    assert unit["criteria"]["associated_documentation"]["status"] == "approved"
    assert any(issue["code"] == "UNPROVEN_RESOURCE_SCOPE" for issue in result["issues"])
    unit["criteria"]["preserved_local_context"]["status"] = "approved"
    unit["decision"] = "approved"
    unit["reasons"] = []
    result["issues"] = []
    result["unresolved_references"] = []
    result["local_dependencies"] = [item for item in result["local_dependencies"]
                                    if item["from"] in ("src/a.ts", "src/a.test.ts")]
    with pytest.raises(AnalyzerUnavailable):
        validate_output(result, files, settings["analyzer"])


@pytest.mark.parametrize("loader", [
    "module.require('../other/bridge');",
    "const load = require; load('../other/bridge');",
    "const {require: load} = module; load('../other/bridge');",
    "const load = module.require.bind(module); load('../other/bridge');",
    "import {createRequire} from 'node:module'; const load = createRequire(import.meta.url); load('../other/bridge');",
    "import {createRequire as makeLoader} from 'module'; const load = makeLoader(import.meta.url); load('../other/bridge');",
    "import * as loaders from 'node:module'; const load = loaders.createRequire(import.meta.url); load('../other/bridge');",
])
def test_commonjs_loaders_never_allow_forged_context(settings, loader):
    """Recusa contexto aprovado por carregadores indiretos, inclusive após apagar os diagnósticos."""
    files = [
        {"path": "src/a.ts", "content": "/** Incrementa a entrada. */\nexport function increment(x:number) { return x + 1; }"},
        {"path": "src/a.test.ts", "content": loader +
         "import{test}from'vitest';import{increment}from'./a';test('ok',()=>increment(1));"},
        {"path": "other/bridge.ts", "content": "import './missing'; export {};"},
    ]
    result = Analyzer(settings).analyze(files, [])
    unit = result["instances"][0]
    assert unit["decision"] == "not_evaluated"
    assert unit["criteria"]["preserved_local_context"]["status"] == "not_evaluated"
    assert unit["criteria"]["direct_test_association"]["status"] == "approved"
    assert unit["criteria"]["associated_documentation"]["status"] == "approved"
    assert {"from": "other/bridge.ts", "specifier": "./missing"} in result["unresolved_references"]
    assert any(issue["code"] == "UNPROVEN_RESOURCE_SCOPE" and issue.get("path") == "src/a.test.ts"
               for issue in result["issues"])
    unit["criteria"]["preserved_local_context"]["status"] = "approved"
    unit["decision"] = "approved"
    unit["reasons"] = []
    with pytest.raises(AnalyzerUnavailable):
        validate_output(result, files, settings["analyzer"])
    result["issues"] = []
    result["unresolved_references"] = []
    with pytest.raises(AnalyzerUnavailable):
        validate_output(result, files, settings["analyzer"])


def test_same_line_methods_and_anonymous_class_are_valid_protocol(settings):
    """Preserva métodos homônimos de classes distintas sem inventar nome de classe anônima."""
    content = ("class A { double(x:number) { return x*2; } } "
               "class B { double(x:number) { return x*2; } } "
               "export default class { triple(x:number) { return x*3; } }")
    result = Analyzer(settings).analyze([{"path": "a.ts", "content": content}], [])
    assert len(result["instances"]) == 3
    assert [unit["function"]["class_name"] for unit in result["instances"]] == ["A", "B", None]


def test_identical_same_line_declarations_preserve_unique_stable_instances(settings, monkeypatch):
    """Preserva duas ocorrências reais idênticas e rejeita unidades além do fonte original."""
    content = ("namespace A { export function f(x:number) {return x*2;} } "
               "namespace B { export function f(x:number) {return x*2;} }")
    files = [{"path": "a.ts", "content": content,
              "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()}]

    def collect(client, row):
        """Fornece o fonte preservado sem recuperar ou executar conteúdo candidato."""
        return files, []

    monkeypatch.setattr(extraction, "collect_context", collect)
    analyzer = Analyzer(settings)
    try:
        first = extraction._extract_candidate(None, candidate(), analyzer)
        second = extraction._extract_candidate(None, candidate(), analyzer)
        result = analyzer.analyze(files, [])
    finally:
        analyzer.cancel()
    assert first[0]["extraction_status"] != "failed"
    assert first[0]["summary"]["instance_count"] == 2
    assert not any(issue["code"] == "ANALYZER_FAILURE" for issue in first[0]["issues"])
    first_units = [row for row in first if row["record_type"] == "instance"]
    second_units = [row for row in second if row["record_type"] == "instance"]
    assert len(first_units) == len(second_units) == 2
    assert first_units[0]["function"] == first_units[1]["function"]
    identities = [row["instance_id"] for row in first_units]
    assert len(set(identities)) == 2
    assert identities == [row["instance_id"] for row in second_units]
    assert identities == [extraction.instance_id(candidate(), row["function"], ordinal=index)
                          for index, row in enumerate(first_units)]
    result["instances"].append(deepcopy(result["instances"][0]))
    with pytest.raises(AnalyzerUnavailable):
        validate_output(result, files, settings["analyzer"])


def test_context_replay_keeps_occurrence_identity_for_identical_functions(settings):
    """Confere evidências por ocorrência lexical quando nome, linha e código coincidem."""
    files = [
        {"path": "a.ts", "content":
         "export namespace A { /** Dobra. */ export function f(x:number) {return x*2;} } "
         "export namespace B { /** Dobra. */ export function f(x:number) {return x*2;} }"},
        {"path": "a.test.ts", "content":
         "import {test} from 'vitest'; import {A} from './a'; test('A',()=>A.f(1));"},
    ]
    result = Analyzer(settings).analyze(files, [])
    assert len(result["instances"]) == 2
    assert result["instances"][0]["function"] == result["instances"][1]["function"]
    assert len(result["instances"][0]["tests"]) == 1
    assert result["instances"][1]["tests"] == []


@pytest.mark.parametrize("interrupted", [False, True])
def test_timeout_or_interruption_kills_and_waits(monkeypatch, interrupted):
    """Impede processos órfãos ao expirar o prazo ou interromper a comunicação."""
    processes = []

    class Process:
        """Simula filho confiável travado, registrando comunicação e limpeza."""

        def __init__(self, command, **kwargs):
            """Registra argumentos de execução sem iniciar processo de candidato."""
            self.command = command
            self.returncode = None
            self.stdin, self.stdout, self.stderr = io.StringIO(), io.StringIO(), io.StringIO()
            self.killed = self.waited = False
            processes.append(self)

        def communicate(self, payload, timeout):
            """Exige envio somente em memória e dispara falha controlada."""
            assert timeout == 180
            assert '"files": []' in payload
            if interrupted:
                raise KeyboardInterrupt()
            raise subprocess.TimeoutExpired(self.command, timeout)

        def poll(self):
            """Mantém o processo em voo até seu recolhimento explícito."""
            return self.returncode

        def kill(self):
            """Registra interrupção forçada do filho."""
            self.killed = True

        def wait(self):
            """Registra recolhimento posterior ao encerramento forçado."""
            assert self.killed
            self.waited = True
            self.returncode = -1

    monkeypatch.setattr(extraction_analyzer.subprocess, "Popen", Process)
    analyzer = Analyzer({"node": "trusted-node", "helper": "trusted-helper", "analyzer": {}})
    with pytest.raises(KeyboardInterrupt if interrupted else AnalyzerUnavailable):
        analyzer.analyze([], [])
    assert processes[0].killed and processes[0].waited
    assert "--max-old-space-size=512" in processes[0].command
    assert processes[0].stdin.closed and processes[0].stdout.closed and processes[0].stderr.closed
    assert not analyzer._processes
