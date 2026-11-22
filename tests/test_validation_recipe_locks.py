import hashlib
import json

import pytest

from robo_minerador.validation_recipe_locks import analyze_npm_locks


MANIFEST = {'name': 'app', 'version': '1.0.0'}


def lock():
    return {'name': 'app', 'version': '1.0.0', 'lockfileVersion': 3,
            'packages': {'': dict(MANIFEST),
                         'node_modules/vitest': {'version': '4.1.10', 'engines': {'node': '>=22'}},
                         'node_modules/vite': {'version': '7.3.6', 'engines': {'npm': '>=10'}}}}


def raw(value):
    return json.dumps(value).encode()


def analyze(value, manifest=MANIFEST):
    return analyze_npm_locks(manifest, {'package-lock.json': raw(value)})


def test_valid_lock_extracts_versions_engines_and_raw_hash():
    data = raw(lock())
    result = analyze_npm_locks(MANIFEST, {'package-lock.json': data})
    assert result == {'lockfiles': [{'path': 'package-lock.json', 'manager': 'npm', 'sha256': hashlib.sha256(data).hexdigest()}],
                      'vitest': '4.1.10', 'vite': '7.3.6', 'node_engines': ['>=22'],
                      'npm_engines': ['>=10'], 'issues': []}


def test_lock_names_may_differ_but_versions_and_name_shapes_remain_checked():
    value = lock()
    value['name'] = 'old-top'
    value['packages']['']['name'] = 'old-root'
    data = raw(value)
    result = analyze_npm_locks(MANIFEST, {'package-lock.json': data})
    assert not result['issues']
    assert result['lockfiles'][0]['sha256'] == hashlib.sha256(data).hexdigest()
    for record in (value, value['packages']['']):
        original = record['name']
        for invalid in ('', ' ', None, 1):
            record['name'] = invalid
            assert analyze(value)['issues']
        record['name'] = original
        record['version'] = '2.0.0'
        assert analyze(value)['issues']
        record['version'] = '1.0.0'


def test_shrinkwrap_wins_after_both_are_validated():
    value = lock()
    result = analyze_npm_locks(MANIFEST, {'pkg/package-lock.json': raw(value), 'pkg/npm-shrinkwrap.json': raw(value)})
    assert not result['issues']
    assert [item['path'] for item in result['lockfiles']] == ['pkg/npm-shrinkwrap.json']


@pytest.mark.parametrize('field', ['version', 'engines'])
def test_conflicting_locks_report_issue(field):
    first, second = lock(), lock()
    second['packages']['node_modules/vitest'][field] = '5.0.0' if field == 'version' else {'node': '>=24'}
    result = analyze_npm_locks(MANIFEST, {'package-lock.json': raw(first), 'npm-shrinkwrap.json': raw(second)})
    assert result['issues']


@pytest.mark.parametrize('data', [b'{"a":1,"a":2}', b'{"x":NaN}', b'\xff', b'[]', b'{'])
def test_invalid_json_reports_issue(data):
    assert analyze_npm_locks(MANIFEST, {'package-lock.json': data})['issues']


@pytest.mark.parametrize('change', [
    lambda x: x.update(lockfileVersion=True),
    lambda x: x.update(packages=[]),
    lambda x: x['packages'].update({'../bad': {}}),
    lambda x: x['packages'].update({'node_modules/foo': []}),
    lambda x: x['packages'][''].pop('version'),
    lambda x: x['packages']['node_modules/vitest'].update(engines=[]),
    lambda x: x['packages']['node_modules/vitest'].update(engines={'node': 12}),
    lambda x: x['packages']['node_modules/vitest'].update(dependencies={'foo': []}),
    lambda x: x['packages']['node_modules/vitest'].update(link=True),
    lambda x: x['packages']['node_modules/vitest'].update(resolved='file:../foo'),
    lambda x: x['packages'][''].update(devDependencies={'foo': 'workspace:*'}),
])
def test_malformed_and_local_entries_report_issues(change):
    value = lock()
    change(value)
    assert analyze(value)['issues']


def test_manifest_local_dependency_is_rejected():
    assert analyze(lock(), {**MANIFEST, 'dependencies': {'foo': 'file:../foo'}})['issues']


def test_nested_vite_resolution_and_ambiguous_installs():
    value = lock()
    value['packages']['node_modules/vitest/node_modules/vite'] = value['packages'].pop('node_modules/vite')
    assert analyze(value)['vite'] == '7.3.6'
    value['packages']['node_modules/vite'] = {'version': '8.0.0'}
    assert analyze(value)['issues']


def test_duplicate_vitest_installation_is_rejected():
    value = lock()
    value['packages']['node_modules/other/node_modules/vitest'] = {'version': '4.1.10'}
    assert analyze(value)['issues']


def test_missing_version_reports_unknown():
    value = lock()
    del value['packages']['node_modules/vitest']['version']
    result = analyze(value)
    assert result['vitest'] is None and result['issues']
    assert result['vite'] == '7.3.6'


def test_manifest_engines_are_included_and_invalid_types_rejected():
    result = analyze(lock(), {**MANIFEST, 'engines': {'node': '>=20'}})
    assert '>=20' in result['node_engines'] and not result['issues']
    assert analyze(lock(), {**MANIFEST, 'engines': {'node': []}})['issues']
