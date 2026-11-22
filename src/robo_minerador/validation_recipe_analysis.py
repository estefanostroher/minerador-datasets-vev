"""Análise offline e conservadora de entradas para receitas automáticas."""

import base64
import binascii
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import threading
import unicodedata

from .extraction_input import strict_object
from .triage import _invalid_constant
from .triage_snapshot import SnapshotUnavailable
from .validation_recipe_documents import classify_documents
from .validation_recipe_locks import analyze_npm_locks


_LOCKS = {'package-lock.json', 'npm-shrinkwrap.json', 'pnpm-lock.yaml',
          'yarn.lock', 'bun.lock', 'bun.lockb'}
_CONFIG = {f'{tool}.config.{ext}' for tool in ('vite', 'vitest')
           for ext in ('js', 'mjs', 'cjs', 'ts', 'mts', 'cts')}
_HELPER = Path(__file__).with_name('typescript') / 'validation-recipe-analysis.mjs'
_SCOPE = 'single_npm_local_vitest_v1'


def _fold(value):
    return unicodedata.normalize('NFC', value).casefold()


def _json(raw):
    return json.loads(raw.decode('utf-8'), object_pairs_hook=strict_object,
                      parse_constant=_invalid_constant)


def _helper(payload, limits):
    """Limita stdout durante a leitura, inclusive quando o processo não termina."""
    process = subprocess.Popen(['node', str(_HELPER)], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    output = bytearray()
    exceeded = threading.Event()

    def consume():
        while True:
            chunk = process.stdout.read(65536)
            if not chunk:
                return
            output.extend(chunk)
            if len(output) > limits['helper_stdout_bytes']:
                exceeded.set()
                process.kill()
                return

    def send():
        try:
            process.stdin.write(json.dumps(payload, ensure_ascii=True).encode())
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    reader = threading.Thread(target=consume, daemon=True)
    writer = threading.Thread(target=send, daemon=True)
    started = []
    try:
        reader.start()
        started.append(reader)
        writer.start()
        started.append(writer)
        process.wait(timeout=limits['helper_seconds'])
    except BaseException as error:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        process.wait()
        if isinstance(error, subprocess.TimeoutExpired):
            raise ValueError('Helper excedeu o prazo.') from None
        raise
    finally:
        for thread in started:
            thread.join()
        process.stdin.close()
        process.stdout.close()
    if exceeded.is_set() or process.returncode:
        raise ValueError('Saída do helper excedida ou processo falhou.')
    result = _json(bytes(output))
    if (not isinstance(result, dict) or set(result) != {'facts', 'issues'}
            or not isinstance(result['facts'], dict) or not isinstance(result['issues'], list)):
        raise ValueError('Resposta inválida do helper.')
    return result


def analyze_recipe_inputs(repository, store, policy):
    """Analisa metadados do snapshot sem executar código do repositório."""
    result = {'snapshot': None, 'inputs': [], 'facts': {}, 'issues': []}
    issues = result['issues']
    fatal_read = False

    def issue(code, message, **evidence):
        issues.append({'code': code, 'message': message, 'evidence': evidence})

    try:
        artifact = store.open_verified(repository['context']['snapshot'])
        result['snapshot'] = artifact.reference()
        entries = store.list_entries(artifact)
        limits = policy['limits']
        valid = {}
        for entry in entries:
            path = entry.get('path')
            encoded = entry.get('path_base64')
            if encoded is not None:
                detail = {}
                try:
                    if not isinstance(encoded, str) or 'path' in entry:
                        raise ValueError
                    decoded = base64.b64decode(encoded, validate=True)
                    if not decoded or base64.b64encode(decoded).decode('ascii') != encoded:
                        raise ValueError
                    try:
                        decoded.decode('utf-8')
                    except UnicodeDecodeError:
                        detail['path_base64'] = encoded
                except (ValueError, UnicodeError, binascii.Error):
                    pass
                issue('AUTO_RECIPE_INPUT_UNSUPPORTED', 'Caminho inválido ou indecodificável.', **detail)
                continue
            if (not isinstance(path, str) or not path or '\\' in path or path.startswith('/')
                    or any(part in ('', '.', '..') for part in path.split('/'))):
                issue('AUTO_RECIPE_INPUT_UNSUPPORTED', 'Caminho inválido ou indecodificável.')
                continue
            valid[path] = entry
        paths = sorted(valid, key=lambda value: value.encode('utf-8'))
        packages = [p for p in paths if _fold(PurePosixPath(p).name) == 'package.json']
        locks = [p for p in paths if _fold(PurePosixPath(p).name) in _LOCKS]
        if len(packages) != 1 or (packages and PurePosixPath(packages[0]).name != 'package.json'):
            issue('AUTO_RECIPE_INPUT_UNSUPPORTED', 'Quantidade ou nome de manifests não suportados.', paths=packages)
        package = str(PurePosixPath(packages[0]).parent) if len(packages) == 1 else '.'
        join = lambda name: name if package == '.' else package + '/' + name
        docs = {_fold(p) for p in ('README.md', 'docs/testing.md', join('README.md'), join('docs/testing.md'))}
        doc_paths = [p for p in paths if _fold(p) in docs]
        if len({_fold(p) for p in doc_paths}) != len(doc_paths):
            issue('UNIT_SCOPE_UNRESOLVED', 'Documentos com grafias concorrentes.')
        ci_names = {'.gitlab-ci.yml', '.circleci/config.yml', 'azure-pipelines.yml', '.travis.yml', 'jenkinsfile'}
        ci = [p for p in paths if _fold(p) in ci_names or
              (_fold(p).startswith('.github/workflows/') and _fold(p).endswith(('.yml', '.yaml')))]
        for path in locks:
            if str(PurePosixPath(path).parent) != package or PurePosixPath(path).name not in {'package-lock.json', 'npm-shrinkwrap.json'}:
                issue('AUTO_RECIPE_INPUT_UNSUPPORTED', 'Lock externo ou gerenciador não suportado.', path=path)
        candidates = set(packages + locks + doc_paths + ci)
        candidates.update(p for p in paths if str(PurePosixPath(p).parent) == package and PurePosixPath(p).name in _CONFIG)
        candidates.update(p for p in paths if p in {'.nvmrc', '.node-version', join('.nvmrc'), join('.node-version')})
        data = {}
        count = total = 0
        manifest = None
        match = None
        config_path = None
        manifest_error = None
        attempted = set()
        with store.open_reader(artifact) as reader:
            def read(path):
                nonlocal count, total, fatal_read
                attempted.add(path)
                entry = valid[path]
                size = entry.get('size')
                maximum = limits['max_lock_bytes'] if path in locks else limits['max_nonlock_bytes']
                if (entry.get('type') != 'blob' or entry.get('mode') not in ('100644', '100755')
                        or type(size) is not int or size < 0 or size > maximum
                        or count + 1 > limits['max_files'] or total + size > limits['max_total_bytes']):
                    issue('AUTO_RECIPE_INPUT_UNSUPPORTED', 'Entrada não legível ou orçamento excedido.', path=path)
                    return
                count += 1
                total += size
                try:
                    raw = reader.read_blobs([path])[path]
                except (OSError, SnapshotUnavailable) as error:
                    code = getattr(error, 'code', 'AUTO_RECIPE_INPUT_UNSUPPORTED')
                    if isinstance(error, SnapshotUnavailable) and code != 'unknown_path':
                        issues.insert(0, {'code': code, 'message': str(error) or 'Falha na leitura do snapshot.', 'evidence': {'path': path}})
                        fatal_read = True
                        raise
                    issue(code, str(error) or 'Falha na leitura da entrada.', path=path)
                    return
                total += len(raw) - size
                if len(raw) != size or len(raw) > maximum or total > limits['max_total_bytes']:
                    issue('AUTO_RECIPE_INPUT_UNSUPPORTED', 'Tamanho de entrada divergente.', path=path)
                    return
                data[path] = raw
                result['inputs'].append({'path': path, 'sha256': hashlib.sha256(raw).hexdigest()})
            if len(packages) == 1:
                read(packages[0])
                if packages[0] in data:
                    try:
                        manifest = _json(data[packages[0]])
                        if not isinstance(manifest, dict) or 'workspaces' in manifest:
                            raise ValueError('Manifesto ou workspace não suportado.')
                    except (ValueError, UnicodeError, RecursionError) as error:
                        manifest_error = error
                        manifest = None
                if manifest is not None:
                    scripts = manifest.get('scripts', {})
                    command = scripts.get('test', '') if isinstance(scripts, dict) else ''
                    match = re.fullmatch(r'vitest run(?: --config ([^\s]+))?', command) if isinstance(command, str) else None
                    if not match:
                        issue('UNIT_SCOPE_UNRESOLVED', 'Script de teste não suportado.', path=packages[0])
                    lifecycle = {key: scripts[key] for key in ('pretest', 'posttest') if isinstance(scripts, dict) and key in scripts}
                    result['facts']['lifecycle'] = lifecycle
                    for key, value in lifecycle.items():
                        if not isinstance(value, str) or value != '':
                            issue('UNIT_SCOPE_UNRESOLVED', 'Lifecycle de teste não suportado.', path=packages[0], script=key, reason='LIFECYCLE_UNSUPPORTED')
                    if isinstance(scripts, dict) and any(re.search(r'e2e|cypress|playwright|browser', str(k) + ' ' + str(v), re.I) for k, v in scripts.items()):
                        issue('UNIT_SCOPE_UNRESOLVED', 'Script browser ou E2E ambíguo.', path=packages[0])
                    if match and match.group(1):
                        target = match.group(1)
                        if ('\\' in target or target.startswith('/') or re.match(r'^[A-Za-z]:', target)
                                or any(p in ('', '.', '..') for p in target.split('/'))):
                            issue('UNIT_SCOPE_UNRESOLVED', 'Caminho de configuração inválido.', path=target)
                        else:
                            config_path = join(target)
                            if config_path in valid:
                                candidates.add(config_path)
                            else:
                                issue('UNIT_SCOPE_UNRESOLVED', 'Configuração explícita ausente.', path=config_path)
            for path in sorted(candidates - attempted, key=lambda value: value.encode('utf-8')):
                read(path)
        result['inputs'].sort(key=lambda item: item['path'].encode('utf-8'))
        if manifest_error is not None:
            raise manifest_error
        facts = result['facts']
        facts.update(package_path=package, scope_rule=_SCOPE, suite_argv=['npm', 'test'], preparation=[])
        documents = classify_documents(
            {path: data[path].decode('utf-8') for path in doc_paths if path in data},
            paths, package)
        facts['scope_evidence'] = documents['evidence']
        issues.extend(documents['issues'])
        if len(packages) != 1 or packages[0] not in data:
            return result
        facts.update(name=manifest.get('name'), package_name=manifest.get('name'), version=manifest.get('version'), locks=[])
        same_locks = [p for p in (join('npm-shrinkwrap.json'), join('package-lock.json')) if p in valid]
        lock_result = analyze_npm_locks(manifest, {p: data[p] for p in same_locks if p in data})
        issues.extend(lock_result['issues'])
        facts['locks'] = lock_result['lockfiles']
        facts['lockfiles'] = lock_result['lockfiles']
        facts['tools'] = {'vitest': lock_result['vitest'], 'vite': lock_result['vite']}
        facts['vitest'], facts['vite'] = lock_result['vitest'], lock_result['vite']
        facts['evidence'] = [
            {'path': item['path'], 'detail': (
                'Manifesto npm' if item['path'] == packages[0] else
                'Lock de dependências' if item['path'] in locks else
                'Confirmação documental de npm test' if item['path'] in doc_paths else
                'Configuração de teste' if item['path'] == config_path or PurePosixPath(item['path']).name in _CONFIG else
                'Entrada verificada da análise')}
            for item in result['inputs']]
        pins = [data[p].decode('utf-8') for p in sorted(set((join('.nvmrc'), join('.node-version'), '.nvmrc', '.node-version'))) if p in data]
        manager = manifest.get('packageManager')
        npm = manager[4:] if isinstance(manager, str) and manager.startswith('npm@') else None
        if 'packageManager' in manifest and npm is None:
            issue('AUTO_RECIPE_INPUT_UNSUPPORTED', 'Gerenciador não suportado.')
        files = [{'path': p, 'content': data[p].decode('utf-8')} for p in sorted(data) if p in candidates and (PurePosixPath(p).name in _CONFIG or p == config_path)]
        payload = {'files': files, 'profiles': policy['profiles'], 'rule': _SCOPE,
                   'inventory': {'packagePath': package, **({'configPath': config_path} if config_path else {})}}
        payload['compatibility'] = {'vitest': lock_result['vitest'], 'vite': lock_result['vite'], 'npm': npm,
                                    'nodePins': pins, 'engines': lock_result['node_engines'], 'npmEngines': lock_result['npm_engines']}
        helper = _helper(payload, limits)
        if (not isinstance(helper, dict) or set(helper) != {'facts', 'issues'}
                or not isinstance(helper['facts'], dict) or not isinstance(helper['issues'], list)
                or any(not isinstance(item, dict) or set(item) != {'code', 'message', 'evidence'}
                       or not isinstance(item['code'], str) or not isinstance(item['message'], str)
                       or not isinstance(item['evidence'], dict) for item in helper['issues'])
                or helper['facts'].get('package_path') != package
                or set(helper['facts']) != {'package_path', 'config', 'configs', 'environment_profile'}
                or not isinstance(helper['facts']['configs'], list)
                or (helper['facts']['config'] is not None and not isinstance(helper['facts']['config'], dict))
                or (helper['facts']['environment_profile'] is not None
                    and helper['facts']['environment_profile'] not in policy['profiles'])
                or (not helper['issues'] and helper['facts']['environment_profile'] is None)):
            raise ValueError('Resposta inválida do helper.')
        facts.update(helper['facts'])
        issues.extend(helper['issues'])
        config = helper['facts'].get('config') or {}
        test = config.get('test') or {}
        if test.get('pool', 'forks') != 'forks':
            issue('UNIT_SCOPE_UNRESOLVED', 'Pool de testes não suportado.', path=config.get('path', ''))
        for key in ('include', 'exclude'):
            for value in test.get(key, []) or []:
                if (not isinstance(value, str) or re.search(r'[*?{}\[\]!]', value) or
                        value.startswith('/') or '\\' in value or any(p in ('', '.', '..') for p in value.split('/'))):
                    issue('UNIT_SCOPE_UNRESOLVED', 'Seleção de testes não suportada.', path=config.get('path', ''))
                elif key == 'include' and join(value) not in valid:
                    issue('UNIT_SCOPE_UNRESOLVED', 'Entrada de teste ausente.', path=join(value))
        setup = test.get('setupFiles', [])
        if isinstance(setup, str):
            setup = [setup]
        for value in setup or []:
            target = value.removeprefix('./') if isinstance(value, str) else ''
            path = join(target)
            if (not target or any(p in ('', '.', '..') for p in target.split('/')) or path not in valid
                    or valid[path].get('type') != 'blob' or valid[path].get('mode') not in ('100644', '100755')):
                issue('UNIT_SCOPE_UNRESOLVED', 'Setup de teste indisponível.', path=path)
        for path in paths:
            name = PurePosixPath(path).name
            parent = str(PurePosixPath(path).parent)
            if re.match(r'^(?:vite|vitest)\.config\.', _fold(name)) and (name not in _CONFIG or parent != package):
                issue('UNIT_SCOPE_UNRESOLVED', 'Configuração desconhecida.', path=path)
            if re.search(r'\.(?:test|spec)\.[cm]?[jt]sx?$', name):
                if package != '.' and not path.startswith(package + '/'):
                    issue('UNIT_SCOPE_UNRESOLVED', 'Teste fora da fronteira do pacote.', path=path)
                elif valid[path].get('type') != 'blob' or valid[path].get('mode') not in ('100644', '100755'):
                    issue('UNIT_SCOPE_UNRESOLVED', 'Entrada de teste não regular.', path=path)
            if valid[path].get('type') == 'commit':
                issue('UNIT_SCOPE_UNRESOLVED', 'Gitlink impede inventário completo.', path=path)
        if any(re.search(r'(^|/)(e2e|cypress|playwright)(/|\.|$)', _fold(p)) or re.search(r'[._-]e2e[._/-]', _fold(p)) for p in paths):
            issue('UNIT_SCOPE_UNRESOLVED', 'Suíte browser ou E2E ambígua.')
    except Exception as error:
        if not fatal_read:
            code = getattr(error, 'code', 'AUTO_RECIPE_INPUT_UNSUPPORTED')
            issue(code, str(error) or 'Falha na análise do snapshot.')
    result['inputs'].sort(key=lambda item: item['path'].encode('utf-8'))
    return result
