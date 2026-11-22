"""Contratos da análise offline de entradas de receita."""

import pytest

from robo_minerador.validation_recipe_resolver import load_recipe_policy


def test_analysis_reads_verified_snapshot_and_preserves_input_hashes():
    from robo_minerador.validation_recipe_analysis import analyze_recipe_inputs
    from recipe_resolution_fixtures import memory_snapshot

    repository, store = memory_snapshot()
    result = analyze_recipe_inputs(repository, store, load_recipe_policy())

    assert result['snapshot'] == repository['context']['snapshot']
    assert result['facts']['package_path'] == '.'
    assert result['facts']['scope_rule'] == 'single_npm_local_vitest_v1'
    assert result['facts']['suite_argv'] == ['npm', 'test']
    assert result['facts']['preparation'] == []
    assert all(set(item) == {'path', 'sha256'} for item in result['inputs'])
    assert store.closed
    assert result['issues'] == []
    assert result['facts']['environment_profile']['vitest'] == '4.1.10'
    assert result['facts']['locks'][0]['path'] == 'package-lock.json'
    assert result['facts']['scope_evidence'][0]['path'] == 'README.md'


def test_explicit_config_is_read_and_analyzed():
    import json
    from robo_minerador.validation_recipe_analysis import analyze_recipe_inputs
    from recipe_resolution_fixtures import memory_snapshot

    manifest = {'name': 'fixture', 'version': '1.0.0',
                'scripts': {'test': 'vitest run --config config/custom.ts'}}
    repository, store = memory_snapshot(extra={
        'package.json': json.dumps(manifest),
        'config/custom.ts': "import { defineConfig } from 'vitest/config'; export default defineConfig({ test: { environment: 'jsdom' } });"})
    result = analyze_recipe_inputs(repository, store, load_recipe_policy())
    assert result['issues'] == []
    assert result['facts']['config']['path'] == 'config/custom.ts'
    assert 'config/custom.ts' in [item['path'] for item in result['inputs']]


def test_default_config_precedence_preserves_both_input_hashes():
    import hashlib
    from robo_minerador.validation_recipe_analysis import analyze_recipe_inputs
    from recipe_resolution_fixtures import memory_snapshot

    configs = {'vitest.config.ts': "import { defineConfig } from 'vitest/config'; export default defineConfig({});",
               'vite.config.ts': 'throw new Error("sentinel");'}
    result = analyze_recipe_inputs(*memory_snapshot(extra=configs), load_recipe_policy())
    assert result['issues'] == []
    assert result['facts']['config']['path'] == 'vitest.config.ts'
    hashes = {item['path']: item['sha256'] for item in result['inputs']}
    for path, content in configs.items():
        assert hashes[path] == hashlib.sha256(content.encode()).hexdigest()


def test_documented_preparation_is_not_invented():
    from robo_minerador.validation_recipe_analysis import analyze_recipe_inputs
    from recipe_resolution_fixtures import memory_snapshot

    repository, store = memory_snapshot(extra={
        'README.md': '## Testing\n```sh\nnpm run build\nnpm test\n```\n'})
    result = analyze_recipe_inputs(repository, store, load_recipe_policy())
    assert 'AUTO_RECIPE_PREPARATION_UNRESOLVED' in [item['code'] for item in result['issues']]
    assert result['facts']['preparation'] == []


def test_external_lock_is_recorded_and_rejected():
    from robo_minerador.validation_recipe_analysis import analyze_recipe_inputs
    from recipe_resolution_fixtures import memory_snapshot

    repository, store = memory_snapshot(extra={'vendor/package-lock.json': '{}'})
    result = analyze_recipe_inputs(repository, store, load_recipe_policy())
    assert 'vendor/package-lock.json' in [entry['path'] for entry in result['inputs']]
    assert 'AUTO_RECIPE_INPUT_UNSUPPORTED' in [issue['code'] for issue in result['issues']]


def test_nested_headless_config_and_document():
    from robo_minerador.validation_recipe_analysis import analyze_recipe_inputs
    from recipe_resolution_fixtures import memory_snapshot

    repository, store = memory_snapshot(prefix='application/local-app', extra={
        'application/local-app/vitest.config.ts': """import { defineConfig } from 'vitest/config';
import { resolve } from 'node:path';
export default defineConfig({test: {environment: 'jsdom', setupFiles: 'src/setup.ts'}, resolve: {alias: {'@': resolve(import.meta.dirname, 'src')}}});""",
        'application/local-app/src/setup.ts': '',
        'application/local-app/README.md': '## Qualité\n```sh\nnpm test\nnpm run lint\n```\n'})
    result = analyze_recipe_inputs(repository, store, load_recipe_policy())
    assert result['issues'] == []
    assert result['facts']['package_path'] == 'application/local-app'
    assert result['facts']['config']['test']['environment'] == 'jsdom'
    assert result['facts']['scope_evidence'][0]['heading'] == 'qualité'


def test_custom_glob_and_missing_setup_are_pending():
    from robo_minerador.validation_recipe_analysis import analyze_recipe_inputs
    from recipe_resolution_fixtures import memory_snapshot

    repository, store = memory_snapshot(extra={'vitest.config.ts': """import { defineConfig } from 'vitest/config';
export default defineConfig({test: {include: ['src/**/*.test.ts'], setupFiles: 'missing.ts'}});"""})
    result = analyze_recipe_inputs(repository, store, load_recipe_policy())
    assert len([i for i in result['issues'] if i['code'] == 'UNIT_SCOPE_UNRESOLVED']) >= 2


def test_real_git_snapshot_is_analyzed_offline(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from robo_minerador.git_transport import GitTransport
    from robo_minerador.snapshot_store import SnapshotStore
    from robo_minerador.validation_recipe_analysis import analyze_recipe_inputs
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
    sha = runner.run(['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                      'commit-tree', tree], repository=origin, input_data=b'fixture\n').decode().strip()
    store = SnapshotStore(tmp_path / 'cache', LocalTransport(origin))
    client = SimpleNamespace(get_repo=lambda _: SimpleNamespace(id=900000001, private=False, full_name='fixture/local-app'))
    artifact = store.acquire({'repository_id': 900000001, 'commit_sha': sha}, client)
    monkeypatch.setattr(store, 'acquire', lambda *args, **kwargs: pytest.fail('unexpected acquisition'))
    result = analyze_recipe_inputs({'context': {'snapshot': artifact.reference()}}, store, load_recipe_policy())
    assert result['issues'] == []
    assert result['snapshot'] == artifact.reference()


def test_invalid_helper_output_does_not_authorize(monkeypatch):
    from robo_minerador import validation_recipe_analysis as analysis
    from recipe_resolution_fixtures import memory_snapshot
    monkeypatch.setattr(analysis, '_helper', lambda *args: {'facts': {}, 'issues': ['invalid']})
    result = analysis.analyze_recipe_inputs(*memory_snapshot(), load_recipe_policy())
    assert any(item['code'] == 'AUTO_RECIPE_INPUT_UNSUPPORTED' for item in result['issues'])


def test_unsupported_profile_preserves_locked_versions():
    import json
    from robo_minerador.validation_recipe_analysis import analyze_recipe_inputs
    from recipe_resolution_fixtures import memory_snapshot
    lock = {'name': 'fixture', 'version': '1.0.0', 'lockfileVersion': 3,
            'packages': {'': {'name': 'fixture', 'version': '1.0.0'},
                         'node_modules/vitest': {'version': '1.0.0'},
                         'node_modules/vite': {'version': '1.0.0'}}}
    result = analyze_recipe_inputs(*memory_snapshot(extra={'package-lock.json': json.dumps(lock)}), load_recipe_policy())
    assert result['facts']['environment_profile'] is None
    assert result['facts']['tools'] == {'vitest': '1.0.0', 'vite': '1.0.0'}
    assert any(i['code'] == 'AUTO_RECIPE_ENVIRONMENT_UNSUPPORTED' for i in result['issues'])


def test_dynamic_config_is_pending():
    from robo_minerador.validation_recipe_analysis import analyze_recipe_inputs
    from recipe_resolution_fixtures import memory_snapshot
    result = analyze_recipe_inputs(*memory_snapshot(extra={'vitest.config.ts':
        "import { defineConfig } from 'vitest/config'; export default defineConfig({test: {environment: process.env.MODE}});"}), load_recipe_policy())
    assert result['facts']['config'] is None
    assert any(i['code'] == 'UNIT_SCOPE_UNRESOLVED' for i in result['issues'])


@pytest.mark.parametrize('extra,remove', [
    ({}, ('package-lock.json',)),
    ({'package.json': b'\xff'}, ()),
    ({'README.md': b'\xff'}, ()),
    ({'package.json': '{"name":"fixture","version":"1.0.0","scripts":{"test":"vitest run --config missing.ts"}}'}, ()),
    ({'src/e2e/browser.test.ts': ''}, ()),
    ({'package.json': '{"name":"fixture","version":"1.0.0","workspaces":[],"scripts":{"test":"vitest run"}}'}, ()),
])
def test_unreadable_or_ambiguous_inputs_are_pending(extra, remove):
    from robo_minerador.validation_recipe_analysis import analyze_recipe_inputs
    from recipe_resolution_fixtures import memory_snapshot
    result = analyze_recipe_inputs(*memory_snapshot(extra=extra, remove=remove), load_recipe_policy())
    assert result['issues']


@pytest.mark.parametrize('message', ['Helper excedeu o prazo.', 'Saída do helper excedida ou processo falhou.'])
def test_helper_failures_are_pending(monkeypatch, message):
    from robo_minerador import validation_recipe_analysis as analysis
    from recipe_resolution_fixtures import memory_snapshot
    def fail(*args):
        raise ValueError(message)
    monkeypatch.setattr(analysis, '_helper', fail)
    result = analysis.analyze_recipe_inputs(*memory_snapshot(), load_recipe_policy())
    assert any(i['code'] == 'AUTO_RECIPE_INPUT_UNSUPPORTED' for i in result['issues'])
    assert result['facts']['tools']['vitest'] == '4.1.10'


@pytest.mark.parametrize('path', ['integration.test.ts', 'other/vitest.config.ts',
                                  'application/local-app/nested/vite.config.ts'])
def test_inventory_rejects_unknown_boundaries(path):
    from robo_minerador.validation_recipe_analysis import analyze_recipe_inputs
    from recipe_resolution_fixtures import memory_snapshot
    result = analyze_recipe_inputs(*memory_snapshot(prefix='application/local-app', extra={path: ''}), load_recipe_policy())
    assert any(i['code'] == 'UNIT_SCOPE_UNRESOLVED' and i['evidence'].get('path') == path for i in result['issues'])


@pytest.mark.parametrize('manifest', ['{', '{"workspaces": []}', b'\xff'])
def test_bad_manifest_does_not_prevent_hashing_locks_and_docs(manifest):
    from robo_minerador.validation_recipe_analysis import analyze_recipe_inputs
    from recipe_resolution_fixtures import memory_snapshot
    result = analyze_recipe_inputs(*memory_snapshot(extra={'package.json': manifest}), load_recipe_policy())
    assert result['issues']
    assert {'package.json', 'package-lock.json', 'README.md'} <= {i['path'] for i in result['inputs']}


@pytest.mark.parametrize('value', ['', None, 0, 'yarn@1.0.0'])
def test_present_invalid_package_manager_is_not_absent(value):
    import json
    from robo_minerador.validation_recipe_analysis import analyze_recipe_inputs
    from recipe_resolution_fixtures import memory_snapshot
    manifest = {'name': 'fixture', 'version': '1.0.0', 'scripts': {'test': 'vitest run'}, 'packageManager': value}
    result = analyze_recipe_inputs(*memory_snapshot(extra={'package.json': json.dumps(manifest)}), load_recipe_policy())
    assert any(i['code'] == 'AUTO_RECIPE_INPUT_UNSUPPORTED' for i in result['issues'])


@pytest.mark.parametrize('key', ['pretest', 'posttest'])
@pytest.mark.parametrize('value,pending', [('', False), ('npm run build', True), (12, True)])
def test_lifecycle_scripts_are_preserved_and_classified(key, value, pending):
    import json
    from robo_minerador.validation_recipe_analysis import analyze_recipe_inputs
    from recipe_resolution_fixtures import memory_snapshot
    manifest = {'name': 'fixture', 'version': '1.0.0', 'scripts': {'test': 'vitest run', key: value}}
    result = analyze_recipe_inputs(*memory_snapshot(extra={'package.json': json.dumps(manifest)}), load_recipe_policy())
    assert result['facts']['lifecycle'] == {key: value}
    assert bool(result['issues']) == pending
    if pending:
        assert any(i['evidence'].get('reason') == 'LIFECYCLE_UNSUPPORTED' for i in result['issues'])


@pytest.mark.parametrize('error', [OSError('falha'), None])
def test_recoverable_read_failure_records_path_and_continues(monkeypatch, error):
    from robo_minerador.validation_recipe_analysis import analyze_recipe_inputs
    from robo_minerador.triage_snapshot import SnapshotUnavailable
    from recipe_resolution_fixtures import memory_snapshot
    repository, store = memory_snapshot(extra={'vendor/package-lock.json': '{}'})
    original = store.read_blobs
    def read(paths):
        if paths == ['README.md']:
            raise error or SnapshotUnavailable('unknown_path', 'ausente')
        return original(paths)
    monkeypatch.setattr(store, 'read_blobs', read)
    result = analyze_recipe_inputs(repository, store, load_recipe_policy())
    assert any(i['evidence'].get('path') == 'README.md' for i in result['issues'])
    assert {'package-lock.json', 'vendor/package-lock.json'} <= {i['path'] for i in result['inputs']}


def test_failed_read_consumes_budget(monkeypatch):
    from robo_minerador.validation_recipe_analysis import analyze_recipe_inputs
    from recipe_resolution_fixtures import memory_snapshot
    repository, store = memory_snapshot()
    original = store.read_blobs
    def read(paths):
        if paths == ['README.md']:
            raise OSError('falha')
        return original(paths)
    monkeypatch.setattr(store, 'read_blobs', read)
    policy = load_recipe_policy()
    policy['limits']['max_files'] = 2
    result = analyze_recipe_inputs(repository, store, policy)
    assert 'package-lock.json' not in {i['path'] for i in result['inputs']}


def test_integrity_failure_aborts_with_path_and_code(monkeypatch):
    from robo_minerador.validation_recipe_analysis import analyze_recipe_inputs
    from robo_minerador.triage_snapshot import SnapshotUnavailable
    from recipe_resolution_fixtures import memory_snapshot
    repository, store = memory_snapshot()
    original = store.read_blobs
    def read(paths):
        if paths == ['README.md']:
            raise SnapshotUnavailable('SNAPSHOT_CORRUPT', 'corrompido')
        return original(paths)
    monkeypatch.setattr(store, 'read_blobs', read)
    result = analyze_recipe_inputs(repository, store, load_recipe_policy())
    assert any(i['code'] == 'SNAPSHOT_CORRUPT' and i['evidence'].get('path') == 'README.md' for i in result['issues'])
    assert 'package-lock.json' not in {i['path'] for i in result['inputs']}


def test_undecodable_path_retains_safe_base64(monkeypatch):
    from robo_minerador.validation_recipe_analysis import analyze_recipe_inputs
    from recipe_resolution_fixtures import memory_snapshot
    repository, store = memory_snapshot()
    original = store.list_entries
    monkeypatch.setattr(store, 'list_entries', lambda artifact: original(artifact) + [{'path_base64': 'aW52/y50eHQ='}])
    result = analyze_recipe_inputs(repository, store, load_recipe_policy())
    assert any(i['evidence'].get('path_base64') == 'aW52/y50eHQ=' for i in result['issues'])


@pytest.mark.parametrize('error', [KeyboardInterrupt(), RuntimeError('wait falhou')])
def test_helper_interrupt_kills_before_join(monkeypatch, error):
    import io
    from robo_minerador import validation_recipe_analysis as analysis
    class Process:
        stdin = io.BytesIO()
        stdout = io.BytesIO()
        returncode = None
        killed = False
        def wait(self, timeout=None):
            if not self.killed:
                raise error
            self.returncode = -1
        def kill(self):
            self.killed = True
    process = Process()
    class Thread:
        def __init__(self, **kwargs): pass
        def start(self): pass
        def join(self): assert process.killed
    monkeypatch.setattr(analysis.subprocess, 'Popen', lambda *args, **kwargs: process)
    monkeypatch.setattr(analysis.threading, 'Thread', Thread)
    with pytest.raises(type(error)):
        analysis._helper({}, load_recipe_policy()['limits'])
    assert process.killed and process.returncode == -1


def test_real_sleeping_helper_is_reaped_on_interrupt(tmp_path, monkeypatch):
    import threading
    from robo_minerador import validation_recipe_analysis as analysis
    helper = tmp_path / 'sleep.mjs'
    helper.write_text("process.stdout.write('x'.repeat(65536)); setInterval(() => {}, 1000);", encoding='utf-8')
    monkeypatch.setattr(analysis, '_HELPER', helper)
    popen = analysis.subprocess.Popen
    processes = []
    ready = threading.Event()
    def spawn(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        stream = process.stdout
        class ObservedStream:
            def read(self, size):
                chunk = stream.read(size)
                if chunk:
                    ready.set()
                return chunk
            def close(self):
                stream.close()
            @property
            def closed(self):
                return stream.closed
        process.stdout = ObservedStream()
        wait = process.wait
        first = True
        def interrupt_once(*args, **kwargs):
            nonlocal first
            if first:
                first = False
                assert ready.wait(10), 'helper não iniciou'
                raise KeyboardInterrupt()
            return wait(*args, **kwargs)
        process.wait = interrupt_once
        return process
    monkeypatch.setattr(analysis.subprocess, 'Popen', spawn)
    with pytest.raises(KeyboardInterrupt):
        analysis._helper({}, load_recipe_policy()['limits'])
    assert processes[0].poll() is not None
    assert processes[0].stdin.closed and processes[0].stdout.closed


@pytest.mark.parametrize('encoded', ['not base64!', '', 'YWJj', 123, 'YR=='])
def test_malformed_path_base64_is_not_echoed(monkeypatch, encoded):
    from robo_minerador.validation_recipe_analysis import analyze_recipe_inputs
    from recipe_resolution_fixtures import memory_snapshot
    repository, store = memory_snapshot()
    original = store.list_entries
    monkeypatch.setattr(store, 'list_entries', lambda artifact: original(artifact) + [{'path_base64': encoded}])
    result = analyze_recipe_inputs(repository, store, load_recipe_policy())
    assert any(i['code'] == 'AUTO_RECIPE_INPUT_UNSUPPORTED' for i in result['issues'])
    assert all('path_base64' not in i['evidence'] for i in result['issues'])


def test_read_keyboard_interrupt_propagates(monkeypatch):
    from robo_minerador.validation_recipe_analysis import analyze_recipe_inputs
    from recipe_resolution_fixtures import memory_snapshot
    repository, store = memory_snapshot()
    def interrupt(paths):
        raise KeyboardInterrupt()
    monkeypatch.setattr(store, 'read_blobs', interrupt)
    with pytest.raises(KeyboardInterrupt):
        analyze_recipe_inputs(repository, store, load_recipe_policy())
    assert store.closed
