"""Contratos portáteis da proveniência automática."""

from copy import deepcopy

import pytest

from recipe_resolution_fixtures import memory_snapshot
from robo_minerador.validation_recipe import load_recipes
from robo_minerador.validation_recipe_resolver import load_recipe_policy, resolve_effective_recipe
from robo_minerador.validation_format import validate_output_records, decision_counts, run_result


@pytest.fixture(scope='module')
def automatic():
    context, store = memory_snapshot()
    repository = {'repository_id': 900000001, 'repository_url': 'https://github.com/fixture/new',
                  'commit_sha': '0' * 40, **context}
    envelope, issues = resolve_effective_recipe(repository, load_recipes(), store, load_recipe_policy())
    assert not issues
    return envelope, repository, context['context']['snapshot']


def test_valid_automatic_provenance(automatic):
    from robo_minerador.validation_recipe_provenance import validate_recipe_provenance
    assert validate_recipe_provenance(*automatic) is None


@pytest.mark.parametrize('mutation', [
    lambda p: p.update(kind='manual'),
    lambda p: p.update(policy_sha256='bad'),
    lambda p: p['snapshot'].update(file_count=True),
    lambda p: p['inputs'].reverse(),
    lambda p: p['inputs'].append(deepcopy(p['inputs'][0])),
    lambda p: p['evidence'][0].update(path='missing'),
    lambda p: p['environment_profile'].update(node='0.0.0'),
])
def test_rejects_incoherent_provenance(automatic, mutation):
    from robo_minerador.validation_recipe_provenance import validate_recipe_provenance
    envelope, repository, snapshot = deepcopy(automatic)
    mutation(envelope['provenance'])
    with pytest.raises(ValueError):
        validate_recipe_provenance(envelope, repository, snapshot)


def test_reader_accepts_legacy_and_coherent_automatic_then_rejects_tampering(monkeypatch, tmp_path):
    from test_validation import published_fixture
    from robo_minerador.validation_format import validate_output_records

    rows, _, _ = published_fixture(monkeypatch, tmp_path, counts=(4,), policy_version=2)
    validate_output_records(rows)
    record = rows[1]
    recipe = record['recipe']['recipe']
    inputs = {e['path']: '1' * 64 for e in recipe['evidence']}
    inputs.update({lock['path']: lock['sha256'] for lock in recipe['lockfiles']})
    record['recipe']['provenance'] = {
        'kind': 'automatic', 'resolver_version': 'snapshot_npm_v1', 'policy_sha256': '2' * 64,
        'snapshot': deepcopy(record['snapshot']), 'rule': 'single_npm_local_vitest_v1',
        'inputs': [{'path': path, 'sha256': inputs[path]} for path in sorted(inputs, key=lambda x: x.encode())],
        'evidence': deepcopy(recipe['evidence']),
        'environment_profile': {'image': recipe['image'], 'platform': recipe['platform'],
                                **recipe['tools'], 'vite': '7.3.6'},
    }
    assert validate_output_records(rows) == rows[-1]
    record['recipe']['provenance']['snapshot']['tree_oid'] = 'f' * 40
    with pytest.raises(ValueError):
        validate_output_records(rows)


@pytest.mark.parametrize('field,value', [
    ('kind', 'other'), ('resolver_version', 'other'), ('rule', 'other'),
    ('policy_sha256', 'A' * 64), ('environment_profile', None),
])
def test_reader_rejects_changed_provenance_fields(automatic, field, value):
    from robo_minerador.validation_recipe_provenance import validate_recipe_provenance
    envelope, repository, snapshot = deepcopy(automatic)
    envelope['provenance'][field] = value
    with pytest.raises(ValueError):
        validate_recipe_provenance(envelope, repository, snapshot)


def test_pending_attempt_can_keep_analysis_snapshot(automatic):
    from robo_minerador.validation_recipe_provenance import validate_recipe_provenance
    envelope, repository, _ = deepcopy(automatic)
    envelope.update(recipe=None, recipe_sha256=None, resolution_status='not_evaluated', code='INPUT_UNSUPPORTED')
    envelope['provenance']['environment_profile'] = None
    assert validate_recipe_provenance(envelope, repository, None) is None


@pytest.fixture(scope='module')
def context_publications(automatic, tmp_path_factory):
    """Publicações sintéticas fechadas, sem passar pelo produtor da tarefa 7."""
    from test_validation import controlled_fixture, published_fixture
    from robo_minerador.validation import _not_started
    from robo_minerador.validation_evaluation import criterion, evaluate_repository, execution_prerequisites

    monkeypatch = pytest.MonkeyPatch()
    try:
        rows, _, _ = published_fixture(monkeypatch, tmp_path_factory.mktemp('provenance'), counts=(4,), policy_version=2)
    finally:
        monkeypatch.undo()
    group, _, _, _ = controlled_fixture((4,))
    envelope, identity, analysis = deepcopy(automatic)
    group['repository'] = identity
    phases = {name: _not_started(envelope['recipe'], name, 'Contexto indisponível.')
              for name in ('installation', 'preparation', 'unit_suite')}
    repository, instances = evaluate_repository(group, envelope, {'phases': phases}, policy_version=2)
    # A tarefa 7 alterará o avaliador; aqui construímos diretamente o contrato 4.3.
    repository['criteria']['required_preparation'] = criterion()
    repository['decision'] = 'not_evaluated'
    for instance in instances:
        instance['criteria']['execution_prerequisites'] = execution_prerequisites(repository['criteria'])
        instance['decision'] = 'not_evaluated'
    repository['instance_decisions'] = decision_counts(instances)
    timestamp = rows[1]['timestamp']
    repository['timestamp'] = timestamp
    for instance in instances:
        instance['timestamp'] = timestamp
    started, finished = deepcopy(rows[0]), deepcopy(rows[-1])
    started['limits'] = [{'repository_id': identity['repository_id'], 'limits': deepcopy(envelope['recipe']['limits'])}]
    finished.update(repository_decisions=decision_counts([repository]), instance_decisions=decision_counts(instances),
                    operational_errors=1, status=run_result([repository], instances, 1))
    result = {}
    for kind in ('UNAVAILABLE', 'MISMATCH'):
        observed = None if kind == 'UNAVAILABLE' else {**analysis, 'tree_oid': 'f' * 40}
        diagnostic = {'schema_version': 3, 'stage': 'validation', 'record_type': 'diagnostic',
                      'timestamp': timestamp, 'repository_id': identity['repository_id'],
                      'phase': 'context', 'code': 'RECIPE_CONTEXT_' + kind, 'operational': True,
                      'message': 'Contexto indisponível.',
                      'evidence': {'analysis_snapshot': analysis, 'observed_snapshot': observed,
                                   'cause': {'code': 'SNAPSHOT_ERROR', 'message': 'Contexto não verificado.'}}}
        result[kind] = deepcopy([started, repository, *instances, diagnostic, finished])
    return result


def reopen(rows):
    """Reabre JSONL pelo mesmo decoder estrito usado na leitura real."""
    import json
    from robo_minerador.validation_evidence import decode_events
    data = ''.join(json.dumps(row, ensure_ascii=True) + '\n' for row in rows).encode()
    return decode_events(data)


@pytest.mark.parametrize('kind', ['UNAVAILABLE', 'MISMATCH'])
def test_context_failure_roundtrip(context_publications, kind):
    rows = reopen(context_publications[kind])
    assert validate_output_records(rows) == rows[-1]
    assert rows[1]['snapshot'] is None
    assert rows[1]['recipe']['resolution_status'] == 'resolved'
    assert rows[0]['limits'][0]['limits'] == rows[1]['recipe']['recipe']['limits']
    assert rows[-1]['status'] == 'failed'


@pytest.mark.parametrize('kind', ['UNAVAILABLE', 'MISMATCH'])
@pytest.mark.parametrize('tamper', [
    'missing', 'wrong_phase', 'wrong_code', 'nonoperational', 'empty_message', 'other_repository',
    'analysis', 'observed', 'invalid_observed', 'cause_code', 'cause_message', 'extra_evidence',
    'started_command', 'started_phase', 'timestamp', 'planned_commands', 'approved_preparation',
    'approved_prerequisite', 'instance_prerequisite', 'instance_count', 'environment', 'locks', 'observation',
    'execution_snapshot', 'limits',
])
def test_context_failure_tampering_is_rejected(context_publications, kind, tamper):
    rows = deepcopy(context_publications[kind])
    repository, instance, diagnostic = rows[1:4]
    evidence = diagnostic['evidence']
    if tamper == 'missing':
        rows.pop(3)
        rows[-1]['operational_errors'] = 0
    elif tamper == 'wrong_phase': diagnostic['phase'] = 'installation'
    elif tamper == 'wrong_code': diagnostic['code'] = 'OTHER'
    elif tamper == 'nonoperational': diagnostic['operational'] = False
    elif tamper == 'empty_message': diagnostic['message'] = ''
    elif tamper == 'other_repository': diagnostic['repository_id'] += 1
    elif tamper == 'analysis': evidence['analysis_snapshot']['tree_oid'] = 'c' * 40
    elif tamper == 'observed': evidence['observed_snapshot'] = None if kind == 'MISMATCH' else deepcopy(evidence['analysis_snapshot'])
    elif tamper == 'invalid_observed': evidence['observed_snapshot'] = {'tree_oid': 'f' * 40}
    elif tamper == 'cause_code': evidence['cause']['code'] = ''
    elif tamper == 'cause_message': evidence['cause'].pop('message')
    elif tamper == 'extra_evidence': evidence['extra'] = 1
    elif tamper == 'started_command': repository['execution']['installation']['commands'].append({'argv': ['npm', 'ci'], 'cwd': '.', 'exitcode': None})
    elif tamper == 'started_phase': repository['execution']['installation']['status'] = 'completed'
    elif tamper == 'timestamp': repository['execution']['installation']['start'] = rows[0]['timestamp']
    elif tamper == 'planned_commands': repository['execution']['installation']['not_started_commands'] = []
    elif tamper == 'approved_preparation': repository['criteria']['required_preparation']['status'] = 'approved'
    elif tamper == 'approved_prerequisite': repository['criteria']['reproducible_environment']['status'] = 'approved'
    elif tamper == 'instance_prerequisite': instance['criteria']['execution_prerequisites']['status'] = 'approved'
    elif tamper == 'instance_count': instance['test_cases']['valid_count'] = 1
    elif tamper == 'environment': repository['environment'] = {'node': '1.0.0'}
    elif tamper == 'locks': repository['lockfiles'] = repository['recipe']['recipe']['lockfiles']
    elif tamper == 'observation': repository['unit_suite'] = {'complete': True}
    elif tamper == 'execution_snapshot': repository['snapshot'] = {**evidence['analysis_snapshot'], 'tree_oid': 'f' * 40}
    elif tamper == 'limits': rows[0]['limits'][0]['limits'] = None
    with pytest.raises(ValueError):
        validate_output_records(reopen(rows))


@pytest.mark.parametrize('tamper', [
    'repository_id', 'repository_url', 'commit_sha', 'snapshot_id', 'snapshot_sha',
    'tree', 'manifest', 'snapshot_extra', 'snapshot_boolean', 'cache_key', 'execution_snapshot',
    'input_hash', 'lock_hash', 'lock_path', 'recipe_hash', 'catalog_hash', 'image', 'platform',
    'node', 'npm', 'vitest', 'vite_invalid', 'profile_extra', 'evidence_detail', 'evidence_path',
    'input_path', 'input_order', 'resolved_code', 'pending_null_code',
])
def test_reader_rejects_automatic_identity_and_integrity_tampering(context_publications, tamper):
    from robo_minerador.validation_recipe import _hash

    rows = deepcopy(context_publications['UNAVAILABLE'])
    record = rows[1]
    envelope = record['recipe']
    provenance = envelope['provenance']
    snapshot = provenance['snapshot']
    recipe = envelope['recipe']
    profile = provenance['environment_profile']
    if tamper == 'repository_id': record['repository_id'] += 1
    elif tamper == 'repository_url': record['repository_url'] = 'https://github.com/fixture/other'
    elif tamper == 'commit_sha': record['commit_sha'] = '1' * 40
    elif tamper == 'snapshot_id': snapshot['repository_id'] += 1
    elif tamper == 'snapshot_sha': snapshot['commit_sha'] = '1' * 40
    elif tamper == 'tree': snapshot['tree_oid'] = 'invalid'
    elif tamper == 'manifest': snapshot['manifest_sha256'] = 'invalid'
    elif tamper == 'snapshot_extra': snapshot['extra'] = 1
    elif tamper == 'snapshot_boolean': snapshot['file_count'] = True
    elif tamper == 'cache_key': snapshot['cache_key'] = 'incorrect'
    elif tamper == 'execution_snapshot': record['snapshot'] = {**snapshot, 'manifest_sha256': 'f' * 64}
    elif tamper == 'input_hash':
        path = recipe['lockfiles'][0]['path']
        next(item for item in provenance['inputs'] if item['path'] == path)['sha256'] = 'f' * 64
    elif tamper == 'lock_hash':
        recipe['lockfiles'][0]['sha256'] = 'f' * 64
        envelope['recipe_sha256'] = _hash(recipe)
    elif tamper == 'lock_path':
        recipe['lockfiles'][0]['path'] = 'other-lock.json'
        envelope['recipe_sha256'] = _hash(recipe)
    elif tamper == 'recipe_hash': envelope['recipe_sha256'] = 'f' * 64
    elif tamper == 'catalog_hash': envelope['catalog_sha256'] = 'invalid'
    elif tamper == 'image': profile['image'] = 'node@sha256:' + 'f' * 64
    elif tamper == 'platform': profile['platform'] = 'linux/arm64'
    elif tamper in ('node', 'npm', 'vitest'): profile[tamper] = '1.2.3'
    elif tamper == 'vite_invalid': profile['vite'] = 'not-a-version'
    elif tamper == 'profile_extra': profile['extra'] = 'x'
    elif tamper == 'evidence_detail': provenance['evidence'][0]['detail'] = ''
    elif tamper == 'evidence_path': provenance['evidence'][0]['path'] = 'other'
    elif tamper == 'input_path': provenance['inputs'][0]['path'] = '../outside'
    elif tamper == 'input_order': provenance['inputs'].reverse()
    elif tamper == 'resolved_code': envelope['code'] = 'unexpected'
    elif tamper == 'pending_null_code': envelope.update(resolution_status='not_evaluated', code=None)
    with pytest.raises(ValueError):
        validate_output_records(reopen(rows))


def test_execution_snapshot_rejects_boolean_equal_to_integer(automatic):
    from robo_minerador.validation_recipe_provenance import validate_recipe_provenance
    envelope, repository, snapshot = deepcopy(automatic)
    envelope['provenance']['snapshot']['file_count'] = 1
    snapshot['file_count'] = True
    with pytest.raises(ValueError):
        validate_recipe_provenance(envelope, repository, snapshot)


def test_repository_identity_rejects_boolean_equal_to_integer(automatic):
    from robo_minerador.validation_recipe_provenance import validate_recipe_provenance
    envelope, repository, snapshot = deepcopy(automatic)
    repository['repository_id'] = True
    snapshot['repository_id'] = envelope['provenance']['snapshot']['repository_id'] = 1
    snapshot['cache_key'] = envelope['provenance']['snapshot']['cache_key'] = '1/' + repository['commit_sha']
    envelope['recipe'] = None
    envelope.update(recipe_sha256=None, resolution_status='not_evaluated', code='UNRESOLVED')
    with pytest.raises(ValueError):
        validate_recipe_provenance(envelope, repository, snapshot)


def test_vite_version_is_declared_not_rederived_from_lock_hash(automatic):
    from robo_minerador.validation_recipe_provenance import validate_recipe_provenance
    envelope, repository, snapshot = deepcopy(automatic)
    envelope['provenance']['environment_profile']['vite'] = '999.0.0'
    validate_recipe_provenance(envelope, repository, snapshot)


def test_context_diagnostic_snapshot_requires_exact_types(context_publications):
    rows = deepcopy(context_publications['UNAVAILABLE'])
    rows[1]['recipe']['provenance']['snapshot']['file_count'] = 1
    rows[3]['evidence']['analysis_snapshot']['file_count'] = True
    with pytest.raises(ValueError):
        validate_output_records(reopen(rows))


def test_pending_profile_still_requires_supported_platform_shape(automatic):
    from robo_minerador.validation_recipe_provenance import validate_recipe_provenance
    envelope, repository, snapshot = deepcopy(automatic)
    envelope.update(recipe=None, recipe_sha256=None, resolution_status='not_evaluated', code='UNRESOLVED')
    envelope['provenance']['environment_profile']['platform'] = 'not-a-platform'
    with pytest.raises(ValueError):
        validate_recipe_provenance(envelope, repository, snapshot)


def test_v2_publication_rejects_provenance(monkeypatch, tmp_path):
    from test_validation import published_fixture
    rows, _, _ = published_fixture(monkeypatch, tmp_path, counts=(4,), policy_version=1)
    validate_output_records(rows)
    rows[1]['recipe']['provenance'] = {}
    with pytest.raises(ValueError, match='schema v3'):
        validate_output_records(reopen(rows))


def test_reader_does_not_access_helpers_store_or_policy(context_publications, monkeypatch):
    import subprocess
    from robo_minerador import validation_recipe_resolver, validation_recipe_analysis
    from robo_minerador.snapshot_store import SnapshotStore

    def forbidden(*args, **kwargs):
        pytest.fail('portable reader accessed external analysis or store')

    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(validation_recipe_resolver, 'load_recipe_policy', forbidden)
    monkeypatch.setattr(validation_recipe_analysis, 'analyze_recipe_inputs', forbidden)
    monkeypatch.setattr(SnapshotStore, 'open_verified', forbidden)
    for rows in context_publications.values():
        assert validate_output_records(reopen(rows)) == rows[-1]


def test_reader_accepts_pending_attempt_without_execution_snapshot(context_publications):
    from robo_minerador.validation import _not_started
    from robo_minerador.validation_evaluation import criterion, execution_prerequisites

    rows = deepcopy(context_publications['UNAVAILABLE'])
    repository, instance = rows[1:3]
    repository['recipe'].update(recipe=None, recipe_sha256=None, resolution_status='not_evaluated', code='UNRESOLVED')
    repository['recipe']['provenance']['environment_profile'] = None
    repository['criteria']['unit_suite_scope'] = criterion()
    repository['execution'] = {phase: _not_started(None, phase, 'Receita pendente.')
                               for phase in ('installation', 'preparation', 'unit_suite')}
    instance['criteria']['execution_prerequisites'] = execution_prerequisites(repository['criteria'])
    rows[0]['limits'][0]['limits'] = None
    assert validate_output_records(reopen(rows)) == rows[-1]


def test_historical_profile_does_not_require_current_matrix(automatic):
    from robo_minerador.validation_recipe import _hash
    from robo_minerador.validation_recipe_provenance import validate_recipe_provenance

    envelope, repository, snapshot = deepcopy(automatic)
    envelope['provenance']['environment_profile']['vitest'] = '99.0.0'
    envelope['recipe']['tools']['vitest'] = '99.0.0'
    envelope['recipe_sha256'] = _hash(envelope['recipe'])
    validate_recipe_provenance(envelope, repository, snapshot)


def test_reader_rejects_pending_recipe_conflicting_code(context_publications):
    from robo_minerador.validation_recipe import _hash

    rows = deepcopy(context_publications['UNAVAILABLE'])
    record = rows[1]
    envelope = record['recipe']
    recipe = envelope['recipe']
    record['snapshot'] = deepcopy(envelope['provenance']['snapshot'])
    recipe['resolution_status'] = 'not_evaluated'
    recipe['resolution']['code'] = 'RECIPE_PENDING'
    envelope.update(resolution_status='not_evaluated', code='RECIPE_PENDING', recipe_sha256=_hash(recipe))
    assert validate_output_records(reopen(rows)) == rows[-1]
    recipe['resolution']['code'] = 'OTHER_PENDING'
    envelope['recipe_sha256'] = _hash(recipe)
    with pytest.raises(ValueError):
        validate_output_records(reopen(rows))


@pytest.mark.parametrize('field', ['command', 'phase'])
@pytest.mark.parametrize('cause', [{'invented': True}, ['text'], True, 42, '', None])
def test_reader_rejects_nontext_context_phase_causes(context_publications, field, cause):
    rows = deepcopy(context_publications['UNAVAILABLE'])
    phase = rows[1]['execution']['installation']
    target = phase if field == 'phase' else phase['not_started_commands'][0]
    target['cause'] = cause
    with pytest.raises(ValueError):
        validate_output_records(reopen(rows))
