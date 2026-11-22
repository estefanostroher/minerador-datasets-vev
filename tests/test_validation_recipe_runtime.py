"""Prova opt-in com snapshot sintético; nunca executa um candidato real."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import pytest

from robo_minerador.extraction_context import load_repository_context
from robo_minerador.validation_docker import DockerExecutor
from robo_minerador.validation_recipe import load_recipes
from robo_minerador.validation_recipe import _hash
from robo_minerador.validation_evaluation import evaluate_repository
from robo_minerador.validation import _incorporate
from robo_minerador.validation_workspace import prepare_workspace
from robo_minerador import validation_instrument
from test_extraction_context import published_case
from test_validation_evidence import _nested_snapshot
from test_validation import controlled_fixture


FIXTURE = Path(__file__).parent / "fixtures" / "recipe_runtime"
pytestmark = pytest.mark.skipif(os.environ.get("ROBO_RECIPE_RUNTIME") != "1", reason="Runtime Docker requer opt-in")


def test_automatic_pipeline_publishes_portable_evidence(tmp_path):
    """Resolve o snapshot sintético, executa uma passagem e reabre pelo leitor."""
    from robo_minerador.validation import validate_repositories
    from robo_minerador.validation_format import validate_output_records
    contents = {p.relative_to(FIXTURE).as_posix(): p.read_bytes() for p in FIXTURE.rglob('*') if p.is_file()}
    state = _nested_snapshot(tmp_path, contents)
    repository, reasons, units = published_case(state)
    approved = [unit for unit in units if unit['decision'] == 'approved']
    assert len(approved) == 1
    source = controlled_fixture((4,))[2]
    group = {'repository': repository, 'reasons': reasons, 'instances': approved}
    executor = DockerExecutor()
    rows = list(validate_repositories([group], source, {'recipes': {}, 'sha256': 'd' * 64}, executor, state.store))
    assert not executor.cleanup_errors
    record = next(row for row in rows if row['record_type'] == 'repository')
    instance = next(row for row in rows if row['record_type'] == 'instance')
    assert record['recipe']['resolution_status'] == 'resolved', record['recipe']
    assert record['recipe']['provenance']['kind'] == 'automatic'
    assert instance['decision'] == 'approved', instance
    assert instance['test_cases']['valid_count'] == 4
    assert validate_output_records(rows)['status'] == 'completed'
    tampered = deepcopy(rows)
    changed = next(row for row in tampered if row['record_type'] == 'instance')
    changed['case_evidence'][0]['calls'][0]['entered']['instance_id'] = '0' * 64
    with pytest.raises(ValueError):
        validate_output_records(tampered)


def test_control_preserves_default_pool_and_census(tmp_path):
    """Controle independente usa a mesma config sem bootstrap de instrumento."""
    contents = {p.relative_to(FIXTURE).as_posix(): p.read_bytes() for p in FIXTURE.rglob('*') if p.is_file()}
    state = _nested_snapshot(tmp_path, contents)
    repository, reasons, units = published_case(state)
    approved = [unit for unit in units if unit['decision'] == 'approved']
    assert len(approved) == 1
    verified = load_repository_context(approved, repository, reasons, state.store)
    recipe = deepcopy(list(load_recipes()['recipes'].values())[0])
    recipe.update(repository_id=1, repository_url=state.candidate['repository_url'], commit_sha=state.candidate['commit_sha'])
    recipe['tools'] = {'node': '22.23.4', 'npm': '10.9.9', 'vitest': '4.1.11'}
    recipe['lockfiles'] = [{'path': 'package-lock.json', 'sha256': hashlib.sha256(contents['package-lock.json']).hexdigest(), 'manager': 'npm'}]
    recipe['phases'] = {'installation': [{'argv': ['npm', 'ci'], 'cwd': '.'}], 'preparation': [], 'unit_suite': [{'argv': ['npm', 'test'], 'cwd': '.'}]}
    recipe['unit_scope']['packages'] = [{'path': '.', 'name': 'synthetic-recipe-runtime', 'scope': 'Fixture sintética completa.'}]
    executor = DockerExecutor()
    try:
        executor.preflight()
        workspace = prepare_workspace(state.store, verified, recipe, executor)
        assert executor.run_phase(workspace, 'installation', recipe)['status'] == 'completed'
        event = executor._trusted(recipe, ['npm', '--prefix', '/workspace', 'test', '--', '--reporter=/workspace/control-reporter.mjs'], volume=workspace.volume)
        line = next(line for line in executor.read_log(event).decode().splitlines() if line.startswith('ROBO_CONTROL='))
        control = json.loads(line.split('=', 1)[1])
        assert control == {'pool': 'forks', 'isolate': True, 'environment': 'jsdom', 'planned': 1, 'modules': 1, 'cases': 4, 'passed': 4}
        print({'control': control, 'environment': workspace.environment})
    finally:
        executor.close()
        assert not executor.cleanup_errors


@pytest.mark.parametrize('scenario', ['positive', 'hook', 'hook_only', 'retry', 'mock', 'threads'])
def test_synthetic_runtime_original_suite(tmp_path, monkeypatch, scenario):
    """Executa cada cenário sintético uma só vez e avalia as provas originais."""
    contents = {p.relative_to(FIXTURE).as_posix(): p.read_bytes() for p in FIXTURE.rglob('*') if p.is_file()}
    test = contents['src/target.test.tsx'].decode()
    if scenario == 'hook':
        test += "\nafterAll(function failsAfterCases() { throw Error('fixture hook failure'); });\n"
    elif scenario == 'hook_only':
        test = test.replace('expect(discountedPrice(', 'if (false) expect(discountedPrice(')
        test += "\nbeforeAll(function callsInHook() { discountedPrice(200, 25); });\n"
    elif scenario == 'retry':
        test = "let attempts = 0;\n" + test.replace("test('aplica desconto', function appliesDiscount() { expect(discountedPrice(200, 25)).toBe(150); });", "test('aplica desconto', {retry: 1}, function appliesDiscount() { expect(discountedPrice(200, 25)).toBe(150); if (++attempts === 1) throw Error('fixture first attempt'); });")
    elif scenario == 'threads':
        contents['vitest.config.ts'] = contents['vitest.config.ts'].replace(b"test: { environment:", b"test: { pool: 'threads', environment:")
    elif scenario == 'mock':
        test = '\n'.join(test.splitlines()[:3]) + '\n'
        contents['src/mock.test.tsx'] = b"import {vi} from 'vitest';\nvi.mock('./target', () => ({discountedPrice: () => 150}));\nimport {discountedPrice} from './target';\ntest.each([1,2,3,4])('mock %s', function mocked() { expect(discountedPrice(200,25)).toBe(150); });\n"
    contents['src/target.test.tsx'] = test.encode()
    state = _nested_snapshot(tmp_path, contents)
    repository, reasons, units = published_case(state)
    approved = [unit for unit in units if unit['decision'] == 'approved']
    assert len(approved) == 1, units
    verified = load_repository_context(approved, repository, reasons, state.store)
    recipe = deepcopy(list(load_recipes()['recipes'].values())[0])
    recipe.update(repository_id=1, repository_url=state.candidate['repository_url'], commit_sha=state.candidate['commit_sha'])
    recipe['tools'] = {'node': '22.23.4', 'npm': '10.9.9', 'vitest': '4.1.11'}
    recipe['lockfiles'] = [{'path': 'package-lock.json', 'sha256': hashlib.sha256(contents['package-lock.json']).hexdigest(), 'manager': 'npm'}]
    recipe['phases'] = {'installation': [{'argv': ['npm', 'ci'], 'cwd': '.'}], 'preparation': [],
                        'unit_suite': [{'argv': ['npm', 'test'], 'cwd': '.'}]}
    recipe['unit_scope']['packages'] = [{'path': '.', 'name': 'synthetic-recipe-runtime', 'scope': 'Fixture sintética completa.'}]
    executor = DockerExecutor()
    try:
        executor.preflight()
        workspace = prepare_workspace(state.store, verified, recipe, executor)
        installation = executor.run_phase(workspace, 'installation', recipe)
        assert installation['status'] == 'completed', installation
        preparation = executor.run_phase(workspace, 'preparation', recipe)
        instrument = validation_instrument.configure_instrument(workspace, verified, recipe, executor)
        assert not instrument.plan['limitations'], instrument.plan['limitations']
        assert workspace.bootstrap_argv == ('node', '/instrument/validation-command.mjs')
        jsdom = [p['version'] for p in workspace.environment['dependencies']['packages'] if p.get('name') == 'jsdom']
        assert jsdom == ['29.1.1']
        suite = executor.run_phase(workspace, 'unit_suite', recipe)
        collection = instrument.collect(executor, suite)
        command = collection['commands'][0]
        report = command.get('report') or {}
        stderr = executor.read_log(suite['commands'][0], 'stderr').decode(errors='replace')
        if scenario == 'threads':
            assert not collection['complete']
            assert 'Pool/browser fora das formas comprovadas' in stderr
            resolution = {'recipe': recipe, 'recipe_sha256': _hash(recipe), 'resolution_status': 'resolved'}
            execution = {'verified': verified, 'environment': workspace.environment, 'observation': collection,
                         'phases': {name: _incorporate(result, executor) for name, result in
                                    [('installation', installation), ('preparation', preparation), ('unit_suite', suite)]}}
            _, instances = evaluate_repository({'repository': repository, 'instances': approved}, resolution, execution, policy_version=2)
            assert instances[0]['decision'] != 'approved'
            return
        if not collection['complete']:
            print(stderr.encode('ascii', 'backslashreplace').decode())
        assert collection['complete'], (report.get('limitations'), stderr)
        assert report['original_argv'] == ['npm', 'test']
        assert command['evidence']['complete']
        assert len(command['evidence']['cases']) == (5 if scenario == 'mock' else 4)
        context = report['suites'][0]['resource_contexts'][0]
        assert (context['pool'], context['isolate'], context['environment']) == ('forks', True, 'jsdom')
        resolution = {'recipe': recipe, 'recipe_sha256': _hash(recipe), 'resolution_status': 'resolved'}
        execution = {'verified': verified, 'environment': workspace.environment, 'observation': collection,
                     'phases': {name: _incorporate(result, executor) for name, result in
                                [('installation', installation), ('preparation', preparation), ('unit_suite', suite)]}}
        _, instances = evaluate_repository({'repository': repository, 'instances': approved}, resolution, execution, policy_version=2)
        instance = instances[0]
        if scenario == 'positive':
            assert suite['status'] == 'completed' and len(suite['commands']) == 1
            assert all([attempt['status'] for attempt in case['finished']['attempts']] == ['passed']
                       for case in command['evidence']['cases'].values())
            assert instance['test_cases']['valid_count'] == 4, instance
            assert instance['criteria']['original_target_execution']['status'] == 'approved', instance
            assert instance['criteria']['associated_tests_execution']['status'] == 'approved', instance
            assert instance['decision'] == 'approved', instance
            from robo_minerador.validation_evidence import validate_census
            tampered = deepcopy(report)
            tampered['suites'][0]['tasks'].pop()
            assert not validate_census(tampered, instrument.command_packages[0], instrument.plan)['complete']
        else:
            assert instance['decision'] != 'approved', instance
            if scenario == 'hook':
                assert instance['criteria']['associated_tests_execution']['status'] == 'rejected', instance
            elif scenario == 'hook_only':
                assert instance['test_cases']['valid_count'] == 0, instance
            elif scenario == 'mock':
                assert instance['test_cases']['valid_count'] == 1, instance
            else:
                assert any([attempt['status'] for attempt in case['finished']['attempts']] == ['failed', 'passed'] for case in command['evidence']['cases'].values())
        print({'scenario': scenario, 'lock_sha256': recipe['lockfiles'][0]['sha256'], 'tools': instrument.installations, 'census': command['census'], 'decision': instance['decision'], 'valid': instance['test_cases']['valid_count'], 'contexts': report['suites'][0].get('resource_contexts')})
    finally:
        executor.close()
        assert not executor.cleanup_errors
