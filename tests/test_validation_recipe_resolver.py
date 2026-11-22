"""Contratos da resolução pelo catálogo e da identidade das origens."""

from copy import deepcopy
import json

import pytest

from robo_minerador.validation_recipe import _hash, _validate_recipe, load_recipes, resolve_recipe
from robo_minerador.validation_recipe_resolver import load_recipe_policy, resolve_effective_recipe
from recipe_resolution_fixtures import memory_snapshot


class InaccessibleStore:
    def __getattribute__(self, name):
        raise AssertionError(f"Acesso inesperado ao store: {name}")


@pytest.fixture
def catalog():
    return load_recipes()


def repository_for(recipe):
    return {key: recipe[key] for key in ("repository_id", "repository_url", "commit_sha")}


@pytest.mark.parametrize("index", range(6))
def test_catalog_recipe_preserves_legacy_envelope_and_deep_copy(catalog, index):
    recipe = list(catalog["recipes"].values())[index]
    repository = repository_for(recipe)
    before = deepcopy(catalog)

    envelope, diagnostics = resolve_effective_recipe(repository, catalog, InaccessibleStore(), None)

    assert envelope == resolve_recipe(repository, catalog)
    assert envelope["catalog_sha256"] == catalog["sha256"]
    assert diagnostics == []
    assert envelope["recipe"] is not recipe
    envelope["recipe"]["phases"]["installation"][0]["argv"].append("changed")
    assert catalog == before


@pytest.mark.parametrize("known_id", [False, True])
def test_missing_recipe_resolves_from_snapshot(catalog, known_id):
    recipe = next(iter(catalog["recipes"].values()))
    repository = repository_for(recipe)
    repository["commit_sha"] = "0" * 40
    if not known_id:
        repository.update(repository_id=900000001, repository_url="https://github.com/fixture/new")

    context, store = memory_snapshot(repository_id=repository['repository_id'], commit_sha=repository['commit_sha'])
    repository.update(context)
    envelope, diagnostics = resolve_effective_recipe(repository, catalog, store, load_recipe_policy())

    assert envelope['resolution_status'] == 'resolved'
    _validate_recipe(envelope['recipe'])
    assert envelope['recipe_sha256'] == _hash(envelope['recipe'])
    assert diagnostics == []


@pytest.mark.parametrize('prefix', ['', 'application/local-app'])
def test_automatic_recipe_contract_and_determinism(catalog, prefix):
    context, store = memory_snapshot(prefix=prefix)
    repository = {'repository_id': 900000001, 'repository_url': 'https://github.com/fixture/new',
                  'commit_sha': '0' * 40, **context}
    policy = load_recipe_policy()
    before = deepcopy((repository, catalog, policy))
    first = resolve_effective_recipe(repository, catalog, store, policy)
    original = store.list_entries
    store.list_entries = lambda artifact: list(reversed(original(artifact)))
    assert first == resolve_effective_recipe(repository, catalog, store, policy)
    envelope, diagnostics = first
    recipe = envelope['recipe']
    _validate_recipe(recipe)
    assert diagnostics == []
    assert recipe['phases'] == {'installation': [{'argv': ['npm', 'ci'], 'cwd': prefix or '.'}],
                                'preparation': [], 'unit_suite': [{'argv': ['npm', 'test'], 'cwd': prefix or '.'}]}
    assert set(recipe['tools']) == {'node', 'npm', 'vitest'}
    assert recipe['unit_scope']['external_services']['status'] == 'not_evaluated'
    provenance = envelope['provenance']
    assert provenance['environment_profile']['vite'] == '7.3.6'
    assert provenance['evidence'] == recipe['evidence']
    assert envelope['catalog_sha256'] == catalog['sha256']
    assert (repository, catalog, policy) == before


def test_analysis_issue_stays_pending(catalog):
    context, store = memory_snapshot(remove=('package-lock.json',))
    repository = {'repository_id': 900000001, 'repository_url': 'https://github.com/fixture/new',
                  'commit_sha': '0' * 40, **context}
    envelope, diagnostics = resolve_effective_recipe(repository, catalog, store, load_recipe_policy())
    assert envelope['recipe'] is None and envelope['recipe_sha256'] is None
    assert envelope['code'] == diagnostics[0]['code'] == 'AUTO_RECIPE_INPUT_UNSUPPORTED'


def test_failed_snapshot_open_has_no_provenance(catalog):
    from robo_minerador.triage_snapshot import SnapshotUnavailable

    class BrokenStore:
        def open_verified(self, reference):
            raise SnapshotUnavailable('snapshot_missing', 'Snapshot ausente.')

    context, _ = memory_snapshot()
    repository = {'repository_id': 900000001, 'repository_url': 'https://github.com/fixture/new',
                  'commit_sha': '0' * 40, **context}
    envelope, diagnostics = resolve_effective_recipe(repository, catalog, BrokenStore(), load_recipe_policy())
    assert envelope['recipe'] is None and 'provenance' not in envelope
    assert envelope['code'] == diagnostics[0]['code'] == 'snapshot_missing'


@pytest.mark.parametrize('key, value', [('repository_id', 900000002), ('commit_sha', '1' * 40)])
def test_reference_identity_mismatch_never_calls_analyzer(catalog, monkeypatch, key, value):
    from robo_minerador import validation_recipe_resolver as resolver

    context, _ = memory_snapshot()
    repository = {'repository_id': 900000001, 'repository_url': 'https://github.com/fixture/new',
                  'commit_sha': '0' * 40, **context}
    repository['context']['snapshot'][key] = value
    monkeypatch.setattr(resolver, 'analyze_recipe_inputs', lambda *args: pytest.fail('analyzer called'))
    envelope, diagnostics = resolve_effective_recipe(repository, catalog, InaccessibleStore(), load_recipe_policy())
    assert envelope['resolution_status'] == 'not_evaluated'
    assert envelope['recipe'] is None and envelope['recipe_sha256'] is None
    assert 'provenance' not in envelope
    assert envelope['code'] == diagnostics[0]['code'] == 'AUTO_RECIPE_INPUT_UNSUPPORTED'


def test_returned_snapshot_mismatch_preserves_analysis_issues(catalog, monkeypatch):
    from robo_minerador import validation_recipe_resolver as resolver

    context, _ = memory_snapshot()
    repository = {'repository_id': 900000001, 'repository_url': 'https://github.com/fixture/new',
                  'commit_sha': '0' * 40, **context}
    issue = {'code': 'snapshot_read_failed', 'message': 'Leitura falhou.', 'evidence': {'path': 'package.json'}}
    analysis = {'snapshot': {**context['context']['snapshot'], 'repository_id': 900000002},
                'inputs': [], 'facts': {}, 'issues': [issue]}
    monkeypatch.setattr(resolver, 'analyze_recipe_inputs', lambda *args: analysis)
    envelope, diagnostics = resolve_effective_recipe(repository, catalog, InaccessibleStore(), load_recipe_policy())
    assert envelope['recipe'] is None and 'provenance' not in envelope
    assert envelope['code'] == diagnostics[0]['code'] == 'AUTO_RECIPE_INPUT_UNSUPPORTED'
    assert diagnostics[1:] == [issue]


def test_same_priority_issues_sort_by_path_and_position(catalog, monkeypatch):
    from robo_minerador import validation_recipe_resolver as resolver
    context, store = memory_snapshot()
    repository = {'repository_id': 900000001, 'repository_url': 'https://github.com/fixture/new',
                  'commit_sha': '0' * 40, **context}
    issues = [{'code': 'UNIT_SCOPE_UNRESOLVED', 'message': 'scope',
               'evidence': {'path': path, 'line': line}}
              for path, line in [('z.md', 1), ('a.md', 3), ('a.md', 1)]]
    monkeypatch.setattr(resolver, 'analyze_recipe_inputs', lambda *args: {
        'snapshot': context['context']['snapshot'], 'inputs': [], 'facts': {}, 'issues': issues})
    _, diagnostics = resolve_effective_recipe(repository, catalog, store, load_recipe_policy())
    assert [(i['evidence']['path'], i['evidence']['line']) for i in diagnostics] == [
        ('a.md', 1), ('a.md', 3), ('z.md', 1)]


def test_incomplete_returned_snapshot_is_rejected(catalog, monkeypatch):
    from robo_minerador import validation_recipe_resolver as resolver
    context, store = memory_snapshot()
    repository = {'repository_id': 900000001, 'repository_url': 'https://github.com/fixture/new',
                  'commit_sha': '0' * 40, **context}
    monkeypatch.setattr(resolver, 'analyze_recipe_inputs', lambda *args: {
        'snapshot': {'repository_id': 900000001, 'commit_sha': '0' * 40},
        'inputs': [], 'facts': {}, 'issues': []})
    envelope, diagnostics = resolve_effective_recipe(repository, catalog, store, load_recipe_policy())
    assert envelope['recipe'] is None and 'provenance' not in envelope
    assert diagnostics[0]['code'] == 'AUTO_RECIPE_INPUT_UNSUPPORTED'


@pytest.mark.parametrize('extra,expected_reason', [
    ({'.github/workflows/ci.yml': 'jobs: {}'}, 'CI_WORKFLOW_UNSUPPORTED'),
    ({'README.md': '## Testing\n`npm run build`\n`npm test`\n'}, None),
])
def test_global_issue_priority(catalog, extra, expected_reason):
    manifest = {'name': 'fixture', 'version': '1.0.0', 'scripts': {'test': 'unsupported'}}
    context, store = memory_snapshot(extra={**extra, 'package.json': json.dumps(manifest)})
    repository = {'repository_id': 900000001, 'repository_url': 'https://github.com/fixture/new',
                  'commit_sha': '0' * 40, **context}
    envelope, diagnostics = resolve_effective_recipe(repository, catalog, store, load_recipe_policy())
    assert envelope['code'] == diagnostics[0]['code']
    if expected_reason:
        assert diagnostics[0]['evidence']['reason'] == expected_reason
    else:
        assert diagnostics[0]['code'] == 'AUTO_RECIPE_PREPARATION_UNRESOLVED'


def test_no_analysis_evidence_cannot_create_recipe_or_provenance(catalog, monkeypatch):
    from robo_minerador import validation_recipe_resolver as resolver
    from robo_minerador.validation_recipe_analysis import analyze_recipe_inputs
    context, store = memory_snapshot()
    repository = {'repository_id': 900000001, 'repository_url': 'https://github.com/fixture/new',
                  'commit_sha': '0' * 40, **context}
    policy = load_recipe_policy()
    analysis = analyze_recipe_inputs(repository, store, policy)
    analysis['facts']['evidence'] = []
    monkeypatch.setattr(resolver, 'analyze_recipe_inputs', lambda *args: analysis)
    envelope, diagnostics = resolve_effective_recipe(repository, catalog, store, policy)
    assert envelope['recipe'] is None and 'provenance' not in envelope
    assert diagnostics[0]['message'] == 'Fatos insuficientes para gerar receita.'


@pytest.mark.parametrize('mutation', [
    lambda p: None, lambda p: {k: v for k, v in p.items() if k != 'limits'},
    lambda p: {**p, 'profiles': []}, lambda p: {**p, 'sha256': 'invalid'},
    lambda p: {**p, 'rules': {}},
])
def test_invalid_loaded_policy_raises_before_analysis(catalog, monkeypatch, mutation):
    from robo_minerador import validation_recipe_resolver as resolver
    context, store = memory_snapshot()
    repository = {'repository_id': 900000001, 'repository_url': 'https://github.com/fixture/new',
                  'commit_sha': '0' * 40, **context}
    monkeypatch.setattr(resolver, 'analyze_recipe_inputs', lambda *args: pytest.fail('analyzer called'))
    with pytest.raises(ValueError):
        resolve_effective_recipe(repository, catalog, store, mutation(load_recipe_policy()))


@pytest.mark.parametrize('reference', [None, {}, {'repository_id': 900000001, 'commit_sha': '0' * 40}])
def test_invalid_reference_does_not_call_analyzer(catalog, monkeypatch, reference):
    from robo_minerador import validation_recipe_resolver as resolver
    repository = {'repository_id': 900000001, 'repository_url': 'https://github.com/fixture/new',
                  'commit_sha': '0' * 40, 'context': {'snapshot': reference}}
    monkeypatch.setattr(resolver, 'analyze_recipe_inputs', lambda *args: pytest.fail('analyzer called'))
    envelope, diagnostics = resolve_effective_recipe(repository, catalog, InaccessibleStore(), load_recipe_policy())
    assert envelope['recipe'] is None and 'provenance' not in envelope
    assert envelope['code'] == diagnostics[0]['code'] == 'AUTO_RECIPE_INPUT_UNSUPPORTED'


@pytest.mark.parametrize('profile_index', range(4))
def test_all_policy_profiles_are_resolved(catalog, profile_index):
    policy = load_recipe_policy()
    profile = policy['profiles'][profile_index]
    manifest = {'name': 'fixture', 'version': '1.0.0', 'scripts': {'test': 'vitest run'},
                'packageManager': 'npm@' + profile['npm']}
    lock = {'name': 'fixture', 'version': '1.0.0', 'lockfileVersion': 3,
            'packages': {'': {'name': 'fixture', 'version': '1.0.0'},
                         'node_modules/vitest': {'version': profile['vitest']},
                         'node_modules/vite': {'version': profile['vite']}}}
    context, store = memory_snapshot(extra={'package.json': json.dumps(manifest),
                                             'package-lock.json': json.dumps(lock),
                                             '.nvmrc': profile['node']})
    repository = {'repository_id': 900000001, 'repository_url': 'https://github.com/fixture/new',
                  'commit_sha': '0' * 40, **context}
    envelope, diagnostics = resolve_effective_recipe(repository, catalog, store, policy)
    assert diagnostics == []
    _validate_recipe(envelope['recipe'])
    assert envelope['provenance']['environment_profile'] == profile


@pytest.mark.parametrize('extra, expected', [
    ({'README.md': '## Testing\nnpm test\n'}, 'UNIT_SCOPE_UNRESOLVED'),
    ({'README.md': '## Testing\n`npm run build`\n`npm test`\n'}, 'AUTO_RECIPE_PREPARATION_UNRESOLVED'),
    ({'pnpm-lock.yaml': ''}, 'AUTO_RECIPE_INPUT_UNSUPPORTED'),
    ({'package-lock.json': json.dumps({'name': 'fixture', 'version': '1.0.0', 'lockfileVersion': 3,
       'packages': {'': {'name': 'fixture', 'version': '1.0.0'},
                    'node_modules/vitest': {'version': '4.1.11'},
                    'node_modules/vite': {'version': '8.0.15'}}})}, 'AUTO_RECIPE_ENVIRONMENT_UNSUPPORTED'),
])
def test_unsupported_inputs_never_generate_recipe(catalog, extra, expected):
    context, store = memory_snapshot(extra=extra)
    repository = {'repository_id': 900000001, 'repository_url': 'https://github.com/fixture/new',
                  'commit_sha': '0' * 40, **context}
    envelope, diagnostics = resolve_effective_recipe(repository, catalog, store, load_recipe_policy())
    assert envelope['recipe'] is None
    assert envelope['code'] == diagnostics[0]['code']
    assert expected in [item['code'] for item in diagnostics]


@pytest.mark.parametrize('prefix', ['', 'application/local-app/'])
def test_real_verified_store_offline(catalog, tmp_path, monkeypatch, prefix):
    from types import SimpleNamespace
    from robo_minerador.git_transport import GitTransport
    from robo_minerador.snapshot_store import SnapshotStore
    from test_git_transport import LocalTransport

    origin = tmp_path / 'origin.git'
    runner = GitTransport()
    runner.run(['init', '--bare', '--template=', str(origin)])
    manifest = {'name': 'fixture', 'version': '1.0.0', 'scripts': {'test': 'vitest run'}}
    lock = {'name': 'fixture', 'version': '1.0.0', 'lockfileVersion': 3,
            'packages': {'': {'name': 'fixture', 'version': '1.0.0'},
                         'node_modules/vitest': {'version': '4.1.10'},
                         'node_modules/vite': {'version': '7.3.6'}}}
    records = []
    for path, content in {'package.json': json.dumps(manifest), 'package-lock.json': json.dumps(lock),
                          'README.md': '## Testing\n`npm test`\n'}.items():
        oid = runner.run(['hash-object', '-w', '--stdin'], repository=origin, input_data=content.encode()).strip()
        records.append(b'100644 blob ' + oid + b'\t' + path.encode() + b'\0')
    tree = runner.run(['mktree', '-z'], repository=origin, input_data=b''.join(records)).decode().strip()
    if prefix:
        for name in reversed(prefix.rstrip('/').split('/')):
            tree = runner.run(['mktree', '-z'], repository=origin,
                              input_data=b'040000 tree ' + tree.encode() + b'\t' + name.encode() + b'\0').decode().strip()
    sha = runner.run(['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                      'commit-tree', tree], repository=origin, input_data=b'fixture\n').decode().strip()
    store = SnapshotStore(tmp_path / 'cache', LocalTransport(origin))
    client = SimpleNamespace(get_repo=lambda _: SimpleNamespace(id=900000001, private=False, full_name='fixture/new'))
    artifact = store.acquire({'repository_id': 900000001, 'commit_sha': sha}, client)
    monkeypatch.setattr(store, 'acquire', lambda *args, **kwargs: pytest.fail('unexpected acquisition'))
    repository = {'repository_id': 900000001, 'repository_url': 'https://github.com/fixture/new',
                  'commit_sha': sha, 'context': {'snapshot': artifact.reference()}}
    envelope, diagnostics = resolve_effective_recipe(repository, catalog, store, load_recipe_policy())
    assert diagnostics == []
    _validate_recipe(envelope['recipe'])
    assert envelope['provenance']['snapshot'] == artifact.reference()
    assert envelope['recipe']['phases']['unit_suite'][0]['cwd'] == (prefix.rstrip('/') or '.')
    repository['commit_sha'] = '0' * 40
    mismatch, issues = resolve_effective_recipe(repository, catalog, store, load_recipe_policy())
    assert mismatch['recipe'] is None and 'provenance' not in mismatch
    assert issues[0]['code'] == 'AUTO_RECIPE_INPUT_UNSUPPORTED'


@pytest.mark.parametrize("conflict", ["exact_sha", "different_sha", "different_id"])
def test_conflicting_identity_is_rejected_without_store(catalog, conflict):
    recipe = next(iter(catalog["recipes"].values()))
    repository = repository_for(recipe)
    if conflict == "different_id":
        repository["repository_id"] = 900000001
        repository["repository_url"] = repository["repository_url"].upper()
    else:
        repository["repository_url"] = "https://github.com/fixture/other"
        if conflict == "different_sha":
            repository["commit_sha"] = "0" * 40

    with pytest.raises(ValueError):
        resolve_effective_recipe(repository, catalog, InaccessibleStore(), None)
