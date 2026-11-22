import copy
import json

import pytest

from dataset_fixtures import dataset_chain, automatic_dataset_chain  # noqa: F401
from robo_minerador.dataset import build_dataset
from robo_minerador.dataset_input import load_dataset_inputs


@pytest.fixture
def dataset(dataset_chain):
    paths, identity = dataset_chain
    inputs = load_dataset_inputs(*(paths[name] for name in ('discovery', 'triage', 'extraction', 'validation', 'deduplication')))
    function = inputs['candidates'][0]['unit']['function']
    measurements = {identity: {
        'size': {'status': 'available', 'physical_lines': function['end_line'] - function['start_line'] + 1, 'method': 'original_declaration_physical_lines_v1', 'reason': None},
        'cyclomatic_complexity': {'status': 'available', 'value': 2, 'method': 'typescript_ast_decisions_v1', 'reason': None},
    }}
    return build_dataset(inputs, measurements, builder_version='1.2')


def test_roundtrip_and_empty(dataset, tmp_path, monkeypatch):
    from robo_minerador.dataset_format import load_dataset, validate_dataset_records
    import subprocess
    path = tmp_path / 'dataset.jsonl'
    path.write_text(json.dumps(dataset[0]) + '\n', encoding='utf-8')
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: pytest.fail('leitor chamou ferramenta externa'))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **kw: pytest.fail('leitor chamou ferramenta externa'))
    assert validate_dataset_records(dataset) == 1
    assert load_dataset(path) == dataset
    explained = copy.deepcopy(dataset)
    explained[0]['validation']['instance']['criteria']['minimum_valid_cases']['message'] = 'Outra explicação válida.'
    explained[0]['validation']['instance']['criteria']['original_target_execution']['message'] = 'Outra descrição válida.'
    assert validate_dataset_records(explained) == 1
    path.write_bytes(b'')
    assert load_dataset(path) == []


@pytest.fixture
def automatic_dataset(automatic_dataset_chain):
    paths, identity = automatic_dataset_chain
    inputs = load_dataset_inputs(*(paths[name] for name in ('discovery', 'triage', 'extraction', 'validation', 'deduplication')))
    function = inputs['candidates'][0]['unit']['function']
    measurements = {identity: {
        'size': {'status': 'available', 'physical_lines': function['end_line'] - function['start_line'] + 1, 'method': 'original_declaration_physical_lines_v1', 'reason': None},
        'cyclomatic_complexity': {'status': 'available', 'value': 2, 'method': 'typescript_ast_decisions_v1', 'reason': None},
    }}
    return build_dataset(inputs, measurements, builder_version='1.2'), inputs


def test_automatic_recipe_survives_dataset_roundtrip(automatic_dataset, tmp_path):
    from robo_minerador.dataset_format import load_dataset
    rows, inputs = automatic_dataset
    recipe = inputs['candidates'][0]['validation_repository']['recipe']
    assert inputs['candidates'][0]['validation_instance']['decision'] == 'approved'
    assert rows[0]['environment']['recipe'] == rows[0]['validation']['repository_evidence']['recipe'] == recipe
    assert recipe['provenance']['kind'] == 'automatic'
    path = tmp_path / 'automatic.jsonl'
    path.write_text(json.dumps(rows[0]) + '\n', encoding='utf-8')
    assert load_dataset(path) == rows


@pytest.mark.parametrize('damage', ['one_copy', 'identity', 'lock_hash', 'recipe_hash'])
def test_automatic_recipe_corruption_is_rejected(automatic_dataset, tmp_path, damage):
    from robo_minerador.dataset_format import load_dataset
    rows, _ = automatic_dataset
    row = copy.deepcopy(rows[0])
    recipe = row['validation']['repository_evidence']['recipe']
    if damage == 'one_copy':
        row['environment']['recipe'] = copy.deepcopy(row['environment']['recipe'])
        row['environment']['recipe']['provenance']['policy_sha256'] = '3' * 64
    else:
        if damage == 'identity': recipe['provenance']['snapshot']['repository_id'] += 1
        if damage == 'lock_hash':
            lock = recipe['recipe']['lockfiles'][0]
            next(item for item in recipe['provenance']['inputs'] if item['path'] == lock['path'])['sha256'] = '0' * 64
        if damage == 'recipe_hash': recipe['recipe_sha256'] = '0' * 64
        row['environment']['recipe'] = copy.deepcopy(recipe)
    path = tmp_path / 'corrupt-automatic.jsonl'
    path.write_text(json.dumps(row) + '\n', encoding='utf-8')
    with pytest.raises(ValueError):
        load_dataset(path)


@pytest.mark.parametrize('corruption', ['observed_status', 'observed_duplicate', 'case_detail', 'case_duplicate', 'original_tests'])
def test_canonical_roundtrip_still_rejects_case_evidence_corruption(dataset, tmp_path, corruption):
    from robo_minerador.dataset_format import load_dataset
    row = json.loads(json.dumps(dataset[0], sort_keys=True))
    instance = row['validation']['instance']
    association = instance['test_cases']['associations'][0]
    cases = instance['case_evidence']
    if corruption == 'observed_status': association['cases'][0]['status'] = 'unavailable'
    if corruption == 'observed_duplicate': association['cases'].append(copy.deepcopy(association['cases'][0]))
    if corruption == 'case_detail': cases[0]['calls'] = []
    if corruption == 'case_duplicate': cases[1] = copy.deepcopy(cases[0])
    if corruption == 'original_tests': cases[0]['original_tests'].append(copy.deepcopy(cases[0]['original_tests'][0]))
    path = tmp_path / 'corrupt.jsonl'
    path.write_text(json.dumps(row, sort_keys=True) + '\n', encoding='utf-8')
    with pytest.raises(ValueError):
        load_dataset(path)


@pytest.mark.parametrize('change', ['hash', 'snippet', 'plan', 'mirror', 'criteria', 'origin', 'version',
                                   'representation', 'metric', 'capture', 'policy', 'duplicate',
                                   'minimum_code', 'target_code'])
def test_rejects_corruption(dataset, change):
    from robo_minerador.dataset_format import validate_dataset_records
    row = copy.deepcopy(dataset[0])
    if change == 'hash': row['context']['files'][0]['sha256'] = '0' * 64
    if change == 'snippet': row['tests'][0]['original_text'] += 'x'
    if change == 'plan': row['validation']['repository_evidence']['unit_suite']['plan']['targets'][row['instance_id']]['path'] = 'other.ts'
    if change == 'mirror': row['environment']['execution'] = {}
    if change == 'criteria': row['validation']['extraction_criteria'] = {}
    if change == 'origin': row['source']['repository_id'] = True
    if change == 'version': row['schema_version'] = True
    if change == 'representation': row['deduplication']['representative']['representation_sha256'] = '0' * 64
    if change == 'metric': row['metrics']['cyclomatic_complexity']['value'] = True
    if change == 'capture': row['validation']['repository_evidence']['corrupt_capture'] = {'encoding': 'utf-8', 'byte_count': 1, 'text': 'x', 'sha256': '0' * 64}
    if change == 'policy': row['provenance']['metric_policy']['typescript'] = '0'
    if change == 'minimum_code': row['validation']['instance']['criteria']['minimum_valid_cases']['code'] = 'REJECTED'
    if change == 'target_code': row['validation']['instance']['criteria']['original_target_execution']['code'] = 'REJECTED'
    if change == 'duplicate':
        duplicate = copy.deepcopy(row['deduplication']['representative'])
        duplicate['disposition'] = 'duplicate'
        duplicate['decision'] = 'rejected'
        duplicate['criteria']['exact_uniqueness'].update(status='rejected', code='EXACT_DUPLICATE')
        row['deduplication']['duplicate_origins'] = [duplicate]
    with pytest.raises(ValueError): validate_dataset_records([row])


def test_strict_json_and_duplicate(dataset, tmp_path):
    from robo_minerador.dataset_format import load_dataset, validate_dataset_records
    with pytest.raises(ValueError): validate_dataset_records(dataset * 2)
    path = tmp_path / 'bad.jsonl'
    for raw in (b'\n', b'{"x":1,"x":2}\n', b'{"x":NaN}\n', json.dumps(dataset[0]).encode() + b'\n\n'):
        path.write_bytes(raw)
        with pytest.raises(ValueError): load_dataset(path)


def test_duplicate_origins_are_references_not_embedded_bytes(dataset):
    from robo_minerador.dataset_format import validate_dataset_records
    row = copy.deepcopy(dataset[0])
    duplicate = copy.deepcopy(row['deduplication']['representative'])
    duplicate.update(instance_id='f' * 64, disposition='duplicate', decision='rejected')
    duplicate['criteria']['exact_uniqueness'].update(status='rejected', code='EXACT_DUPLICATE')
    row['deduplication']['duplicate_origins'] = [duplicate]
    assert validate_dataset_records([row]) == 1
    row['deduplication']['duplicate_origins'].append(copy.deepcopy(duplicate))
    with pytest.raises(ValueError):
        validate_dataset_records([row])


def test_diagnostic_replay_opt_out_is_restricted_and_checks_audit(monkeypatch, tmp_path):
    import hashlib
    from robo_minerador.validation_evaluation import PAIR_POLICY
    from robo_minerador.validation_format import _repository
    from robo_minerador.validation_replay import replay_pair_records
    from robo_minerador.validation_resources import _produce
    from robo_minerador.validation_evaluation import _observation_complete
    from robo_minerador.validation_recipe import _hash as recipe_hash
    from test_validation import published_fixture
    import subprocess

    rows, _, _ = published_fixture(monkeypatch, tmp_path, counts=(4,))
    repository = replay_pair_records(rows, source_path='historical.jsonl', source_sha256='a' * 64)[1]
    assert repository['resource_evidence'] is not None
    incomplete = copy.deepcopy(repository)
    observation = incomplete['unit_suite']
    observation['commands'][0]['report']['resource_modules'] = []
    incomplete['resource_evidence'] = _produce(
        incomplete['resource_evidence']['sources'], incomplete['resource_universe'],
        incomplete['recipe']['recipe'], observation, incomplete['environment'], _observation_complete(observation))
    assert incomplete['resource_evidence']['complete'] is False
    variants = []
    for text in ("import {get} from 'node:https'; get('https://api.github.com/repos');", "import('unknown');"):
        variant = copy.deepcopy(repository)
        universe = variant['resource_universe']
        sources = variant['resource_evidence']['sources']
        next(item for item in sources if item['path'] == 'a.test.ts')['content'] = text
        entry = next(item for item in universe['files'] if item['path'] == 'a.test.ts')
        entry.update(sha256=hashlib.sha256(text.encode()).hexdigest(), line_count=len(text.splitlines()))
        universe['sha256'] = recipe_hash({key: value for key, value in universe.items() if key != 'sha256'})
        variant['resource_evidence'] = _produce(sources, universe, variant['recipe']['recipe'],
            variant['unit_suite'], variant['environment'], _observation_complete(variant['unit_suite']))
        variants.append(variant)
    assert any(f['resource'] == 'real_service' for item in variants[0]['resource_evidence']['files'] for f in item['findings'])
    assert any(item['limitations'] for item in variants[1]['resource_evidence']['files'])
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: pytest.fail('replay AST executado'))
    _repository(repository, rederive_diagnostic_resources=False, resource_scope=PAIR_POLICY['resource_gate_scope'])
    _repository(incomplete, rederive_diagnostic_resources=False, resource_scope=PAIR_POLICY['resource_gate_scope'])
    for variant in variants:
        _repository(variant, rederive_diagnostic_resources=False, resource_scope=PAIR_POLICY['resource_gate_scope'])
    with pytest.raises(ValueError):
        _repository(repository, rederive_diagnostic_resources=False, resource_scope='global_provisional')
    legacy = copy.deepcopy(repository)
    legacy['schema_version'] = 2
    with pytest.raises(ValueError):
        _repository(legacy, rederive_diagnostic_resources=False, resource_scope=PAIR_POLICY['resource_gate_scope'])
    corrupt = copy.deepcopy(repository)
    corrupt['resource_evidence']['sources'][0]['content'] += 'corruption'
    with pytest.raises(ValueError):
        _repository(corrupt, rederive_diagnostic_resources=False, resource_scope=PAIR_POLICY['resource_gate_scope'])
    for key, value in (('effects', [42]), ('excluded_files', [None]), ('files', [])):
        corrupt = copy.deepcopy(repository)
        corrupt['resource_evidence'][key] = value
        with pytest.raises(ValueError):
            _repository(corrupt, rederive_diagnostic_resources=False, resource_scope=PAIR_POLICY['resource_gate_scope'])
    corrupt = copy.deepcopy(repository)
    file = next(item for item in corrupt['resource_evidence']['files'] if item['findings'])
    file['findings'] = []
    with pytest.raises(ValueError):
        _repository(corrupt, rederive_diagnostic_resources=False, resource_scope=PAIR_POLICY['resource_gate_scope'])


@pytest.mark.parametrize('corruption', ['complete', 'audit_key', 'finding_key', 'real_service', 'binding_type', 'detail'])
def test_diagnostic_rejects_invalid_current_shapes(monkeypatch, tmp_path, corruption):
    from robo_minerador.validation_evaluation import PAIR_POLICY
    from robo_minerador.validation_format import _repository
    from robo_minerador.validation_replay import replay_pair_records
    from test_validation import published_fixture

    rows, _, _ = published_fixture(monkeypatch, tmp_path, counts=(4,))
    repository = replay_pair_records(rows, source_path='historical.jsonl', source_sha256='a' * 64)[1]
    audit = repository['resource_evidence']
    finding = next(item['findings'][0] for item in audit['files'] if item['findings'])
    if corruption == 'complete': audit['complete'], audit['limitations'] = True, ['limitação']
    if corruption == 'audit_key': audit['unexpected'] = 1
    if corruption == 'finding_key': finding['unexpected'] = 1
    if corruption == 'real_service':
        audit['files'][0]['findings'].append({'start_line': 1, 'end_line': 1, 'resource': 'real_service',
            'role': 'effective_dependency', 'rule': 'real_service', 'start': 0, 'end': 1, 'expression': 'x'})
    if corruption == 'binding_type': audit['bindings']['node']['verified'] = 1
    if corruption == 'detail': audit['files'][0]['limitation_details'] = []
    with pytest.raises(ValueError):
        _repository(repository, rederive_diagnostic_resources=False, resource_scope=PAIR_POLICY['resource_gate_scope'])
