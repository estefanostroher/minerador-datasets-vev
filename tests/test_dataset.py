import copy
import hashlib

import pytest

from dataset_fixtures import dataset_chain  # noqa: F401
from robo_minerador.dataset_input import load_dataset_inputs


@pytest.fixture
def prepared(dataset_chain):
    paths, identity = dataset_chain
    names = ('discovery', 'triage', 'extraction', 'validation', 'deduplication')
    inputs = load_dataset_inputs(*(paths[name] for name in names))
    measurements = {identity: {
        'size': {'status': 'available', 'physical_lines': 3, 'method': 'original_declaration_physical_lines_v1', 'reason': None},
        'cyclomatic_complexity': {'status': 'available', 'value': 2, 'method': 'typescript_ast_decisions_v1', 'reason': None},
    }}
    return inputs, measurements


def test_preserves_evidence_and_is_deterministic(prepared):
    from robo_minerador.dataset import build_dataset, METRIC_POLICY

    inputs, measurements = prepared
    candidate = inputs['candidates'][0]
    unit = candidate['unit']
    test = copy.deepcopy(unit['tests'][0])
    test['parameterized'] = True
    test['skipped'] = True
    unit['tests'] = [test, copy.deepcopy(test)]
    candidate['duplicate_origins'] = [{'instance_id': 'z'}, {'instance_id': 'a'}]
    before = copy.deepcopy((inputs, measurements))
    result = build_dataset(inputs, measurements, builder_version='1.2')
    assert result == build_dataset(inputs, measurements, builder_version='1.2')
    assert (inputs, measurements) == before
    row = result[0]
    assert set(row) == {'schema_version', 'instance_id', 'language', 'function', 'tests', 'documentation', 'context', 'metrics', 'source', 'environment', 'validation', 'deduplication', 'provenance'}
    assert row['schema_version'] == 1 and row['language'] == 'TypeScript'
    assert row['function'] == unit['function'] and row['documentation'] == unit['documentation']
    from robo_minerador.deduplication_representation import _snippet
    assert row['tests'] == [{**test, 'original_text': _snippet(test, candidate['files'])}] * 2
    assert row['context']['files'] == [{'path': p, 'content': candidate['files'][p], 'sha256': hashlib.sha256(candidate['files'][p].encode('utf-8')).hexdigest()} for p in sorted(unit['context_paths'])]
    repo = candidate['extraction_repository']
    assert row['context']['snapshot'] == repo['context']['snapshot']
    assert row['context']['execution_requirements'] == repo['execution_requirements']
    assert row['context']['binary_policy'] == 'references_only'
    vr = candidate['validation_repository']
    vi = candidate['validation_instance']
    assert row['environment'] == {k: vr[k] for k in ('recipe', 'environment', 'lockfiles', 'execution')}
    assert row['validation'] == {'instance': vi, 'extraction_criteria': unit['criteria'], 'repository_evidence': vr}
    assert row['metrics'] == {**measurements[unit['instance_id']], 'test_cases': vi['test_cases'], 'coverage': vi['coverage']}
    assert row['source'] == candidate['source']
    assert row['deduplication'] == {'policy': inputs['deduplication_policy'], 'representative': candidate['representative'], 'duplicate_origins': sorted(candidate['duplicate_origins'], key=lambda x: x['instance_id'])}
    assert row['provenance'] == {'sources': inputs['sources'], 'builder': {'name': 'robo-minerador', 'version': '1.2', 'policy': 'dataset_build_v1'}, 'metric_policy': METRIC_POLICY}
    row['validation']['instance']['criteria'].clear()
    assert (inputs, measurements) == before


@pytest.mark.parametrize('measurements', [{}, {'extra': {}}])
def test_rejects_mismatched_measurements(prepared, measurements):
    from robo_minerador.dataset import build_dataset

    with pytest.raises(ValueError):
        build_dataset(prepared[0], measurements, builder_version='1')


def test_empty_and_sorted_representatives(prepared):
    from robo_minerador.dataset import build_dataset

    inputs, measurements = prepared
    other = copy.deepcopy(inputs['candidates'][0])
    original_id = other['unit']['instance_id']
    other['unit']['instance_id'] = '0'
    inputs['candidates'].append(other)
    measurements['0'] = copy.deepcopy(measurements[original_id])
    before = copy.deepcopy(inputs)
    rows = build_dataset(inputs, measurements, builder_version='1')
    assert [row['instance_id'] for row in rows] == sorted(measurements)
    assert rows == build_dataset({**inputs, 'candidates': list(reversed(inputs['candidates']))}, measurements, builder_version='1')
    rows[0]['provenance']['sources']['discovery']['status'] = 'changed'
    assert rows[1]['provenance']['sources'] == before['sources']
    assert inputs == before
    inputs['candidates'] = []
    assert build_dataset(inputs, {}, builder_version='1') == []
