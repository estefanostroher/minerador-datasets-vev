"""Constrói publicações temporárias vinculadas para os testes do dataset."""

import hashlib
import json
from copy import deepcopy

import pytest

from robo_minerador import validation
from robo_minerador.deduplication import deduplicate_instances
from robo_minerador.deduplication_format import load_deduplication
from robo_minerador.deduplication_input import load_deduplication_inputs
from robo_minerador.extraction_input import load_triage
from robo_minerador.jsonl import write_jsonl
from robo_minerador.triage import load_discovery
from robo_minerador.validation_format import validate_output_records
from robo_minerador.validation_input import load_extraction
from test_deduplication_cli import positive_inputs
from test_extraction_input import candidate, triage_rows
from test_triage import discovery_rows
from test_validation_input import empty_repository_rows, write_rows
from test_validation_local import unrelated_failure


def _publish(path, rows):
    """Serializa uma publicação temporária e retorna seu hash de bytes."""
    write_jsonl(path, rows, ensure_ascii=True, compact=True)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _chain(tmp_path, extraction_path, validation_path, validation_rows, instance_id):
    """Vincula as cinco publicações sem alterar evidências da validação."""
    extraction_rows = [json.loads(line) for line in extraction_path.read_bytes().splitlines()]
    repository = next(row for row in extraction_rows if row['record_type'] == 'repository')
    identity = {key: repository[key] for key in ('repository_id', 'repository_url', 'commit_sha')}
    name = identity['repository_url'].removeprefix('https://github.com/')
    discovery = {**identity, 'record_type': 'repository', 'repository': name,
                 'sha_status': 'resolved', 'stars': 0}
    paths = {name: tmp_path / f'{name}.jsonl' for name in
             ('discovery', 'triage', 'extraction', 'validation', 'deduplication')}
    paths['extraction'], paths['validation'] = extraction_path, validation_path
    discovery_hash = _publish(paths['discovery'], discovery_rows([discovery], status='partial'))

    screened = candidate(identity['repository_id'])
    screened.update(identity, readme={'present': False, 'paths': [], 'total': 0}, reasons=[])
    screened['criteria']['license']['evidence'] = {
        'path': 'LICENSE', 'blob_sha': 'a' * 40, 'spdx_id': 'MIT',
        'candidates': {'paths': ['LICENSE'], 'total': 1},
    }
    triage = triage_rows([screened])
    triage[0].update(input_path=str(paths['discovery']), input_sha256=discovery_hash,
                     input_status='partial')
    triage_hash = _publish(paths['triage'], triage)

    extraction_rows[0].update(input_path=str(paths['triage']), input_sha256=triage_hash,
                              input_status=triage[-1]['status'], input_count=1, eligible_count=1)
    extraction_hash = _publish(extraction_path, extraction_rows)
    validation_rows[0]['source']['sha256'] = extraction_hash
    validation_rows[-1]['source_sha256'] = extraction_hash
    _publish(validation_path, validation_rows)
    candidates, source = load_deduplication_inputs(validation_path, extraction_path)
    _publish(paths['deduplication'], deduplicate_instances(candidates, source))

    load_discovery(paths['discovery'])
    load_triage(paths['triage'])
    load_extraction(extraction_path)
    validate_output_records(validation_rows)
    load_deduplication(paths['deduplication'])
    return paths, instance_id


@pytest.fixture
def dataset_chain(monkeypatch, tmp_path):
    """Produz uma representante aprovada com provas controladas e origem parcial."""
    extraction, output, rows, instance_id = positive_inputs(monkeypatch, tmp_path, mutate=unrelated_failure)
    repository = next(row for row in rows if row['record_type'] == 'repository')
    instance = next(row for row in rows if row['record_type'] == 'instance')
    assert repository['decision'] == 'rejected'
    assert instance['instance_id'] == instance_id and instance['decision'] == 'approved'
    return _chain(tmp_path, extraction, output, rows, instance_id)


@pytest.fixture
def automatic_dataset_chain(monkeypatch, tmp_path):
    """Publicação sintética aprovada com envelope automático portátil."""
    extraction, output, rows, instance_id = positive_inputs(monkeypatch, tmp_path, mutate=unrelated_failure)
    repository = next(row for row in rows if row['record_type'] == 'repository')
    recipe = repository['recipe']['recipe']
    inputs = {item['path']: '1' * 64 for item in recipe['evidence']}
    inputs.update({item['path']: item['sha256'] for item in recipe['lockfiles']})
    repository['recipe']['provenance'] = {
        'kind': 'automatic', 'resolver_version': 'snapshot_npm_v1', 'policy_sha256': '2' * 64,
        'snapshot': deepcopy(repository['snapshot']), 'rule': 'single_npm_local_vitest_v1',
        'inputs': [{'path': path, 'sha256': inputs[path]} for path in sorted(inputs, key=lambda p: p.encode())],
        'evidence': deepcopy(recipe['evidence']),
        'environment_profile': {'image': recipe['image'], 'platform': recipe['platform'],
                                **recipe['tools'], 'vite': '7.3.6'},
    }
    return _chain(tmp_path, extraction, output, rows, instance_id)


@pytest.fixture
def empty_dataset_chain(tmp_path, extraction_rows):
    """Produz cinco publicações íntegras sem instâncias elegíveis."""
    extraction, _ = write_rows(tmp_path, empty_repository_rows(extraction_rows))
    groups, source = load_extraction(extraction)
    rows = list(validation.validate_repositories(groups, source, {}, policy_version=2))
    return _chain(tmp_path, extraction, tmp_path / 'validation.jsonl', rows, None)


@pytest.fixture
def failed_dataset_chain(monkeypatch, tmp_path):
    """Preserva uma candidata extraída, mas sem receita nem avaliação dinâmica."""
    from robo_minerador.validation_recipe import resolve_recipe
    from test_validation import controlled_fixture, FixtureExecutor

    extraction, output, _, _ = positive_inputs(monkeypatch, tmp_path)
    groups, source = load_extraction(extraction)
    catalog = {'recipes': {}, 'sha256': 'd' * 64}
    monkeypatch.setattr(validation, 'resolve_effective_recipe',
                        lambda repository, catalog, *args: (resolve_recipe(repository, catalog), []))
    executor = FixtureExecutor(controlled_fixture((4,))[3])
    rows = list(validation.validate_repositories(groups, source, catalog, executor, object(), policy_version=2))
    assert rows[-1]['status'] == 'failed' and not executor.calls
    return _chain(tmp_path, extraction, output, rows, None)
