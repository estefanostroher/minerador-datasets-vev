"""Análise conservadora e sem I/O de lockfiles npm."""

import hashlib
import json
import posixpath
import re


_GROUPS = ('dependencies', 'devDependencies', 'optionalDependencies', 'peerDependencies')
_LOCAL = re.compile(r'^(?:file:|link:|workspace:|\.{1,2}(?:/|\\|$)|/|[a-zA-Z]:[\\/])', re.I)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Chave JSON duplicada.')
        result[key] = value
    return result


def _constant(value):
    raise ValueError('Constante JSON inválida: ' + value)


def _fields(record):
    for group in _GROUPS:
        if group not in record:
            continue
        values = record[group]
        if not isinstance(values, dict) or any(
                not isinstance(name, str) or not isinstance(value, str) or _LOCAL.match(value)
                for name, value in values.items()):
            raise ValueError('Dependências inválidas ou locais.')
    engines = record.get('engines', {})
    if not isinstance(engines, dict) or any(not isinstance(value, str) for value in engines.values()):
        raise ValueError('Engines inválidos.')
    return engines


def _parse(raw, manifest):
    if not isinstance(raw, bytes):
        raise ValueError('Conteúdo de lock inválido.')
    data = json.loads(raw.decode('utf-8'), object_pairs_hook=_object, parse_constant=_constant)
    if not isinstance(data, dict) or type(data.get('lockfileVersion')) is not int or data['lockfileVersion'] != 3:
        raise ValueError('Versão de lock inválida.')
    records = data.get('packages')
    if not isinstance(records, dict) or not isinstance(records.get(''), dict):
        raise ValueError('Pacotes do lock inválidos.')
    if (any(not isinstance(record.get('name'), str) or not record['name'].strip()
            or record.get('version') != manifest['version'] for record in (data, records['']))):
        raise ValueError('Identidade do lock divergente.')
    engines = {'node': [], 'npm': []}
    installs = {'vitest': [], 'vite': []}
    for path in sorted(records):
        record = records[path]
        if (not isinstance(path, str) or (path and (not path.startswith('node_modules/')
                or posixpath.normpath(path) != path or '\\' in path)) or not isinstance(record, dict)):
            raise ValueError('Entrada de pacote inválida.')
        if 'link' in record and (not isinstance(record['link'], bool) or record['link']):
            raise ValueError('Link não suportado.')
        if 'resolved' in record and (not isinstance(record['resolved'], str) or _LOCAL.match(record['resolved'])):
            raise ValueError('Resolução local ou inválida.')
        if 'version' in record and not isinstance(record['version'], str):
            raise ValueError('Versão de pacote inválida.')
        fields = _fields(record)
        for kind in engines:
            if kind in fields:
                engines[kind].append(fields[kind])
        for name in installs:
            if path.endswith('/node_modules/' + name) or path == 'node_modules/' + name:
                installs[name].append(path)
    if len(installs['vitest']) > 1 or len(installs['vite']) > 1:
        raise ValueError('Instalações Vitest ou Vite ambíguas.')
    vitest = records.get('node_modules/vitest', {}).get('version')
    vite = records.get('node_modules/vitest/node_modules/vite', records.get('node_modules/vite', {})).get('version')
    vitest = vitest if isinstance(vitest, str) and vitest.strip() else None
    vite = vite if isinstance(vite, str) and vite.strip() else None
    return vitest, vite, engines


def analyze_npm_locks(manifest, locks):
    """Valida todos os locks fornecidos e retorna somente o lock efetivo."""
    result = {'lockfiles': [], 'vitest': None, 'vite': None,
              'node_engines': [], 'npm_engines': [], 'issues': []}

    def issue(message, path=''):
        result['issues'].append({'code': 'AUTO_RECIPE_INPUT_UNSUPPORTED',
                                 'message': message, 'evidence': {'path': path}})

    if (not isinstance(manifest, dict) or any(not isinstance(manifest.get(k), str)
            or not manifest[k].strip() for k in ('name', 'version'))):
        issue('Identidade do manifesto inválida.')
        return result
    try:
        manifest_engines = _fields(manifest)
    except ValueError as error:
        issue(str(error))
        return result
    if not isinstance(locks, dict) or not locks:
        issue('Lock npm ausente ou inválido.')
        return result
    if any(not isinstance(path, str) for path in locks):
        issue('Caminho de lock inválido.')
        return result
    paths = sorted(locks)
    if (any(posixpath.normpath(path) != path or path.startswith('/') or '\\' in path
            or path.startswith('../') or posixpath.basename(path) not in ('package-lock.json', 'npm-shrinkwrap.json') for path in paths)
            or len({posixpath.dirname(path) for path in paths}) != 1):
        issue('Caminhos de lock inválidos ou em diretórios distintos.')
        return result
    effective = next((p for p in paths if posixpath.basename(p) == 'npm-shrinkwrap.json'), paths[0])
    parsed = {}
    for path in paths:
        try:
            parsed[path] = _parse(locks[path], manifest)
            if parsed[path][0] is None or parsed[path][1] is None:
                issue('Versão Vitest ou Vite desconhecida.', path)
        except (ValueError, TypeError, UnicodeError, RecursionError) as error:
            issue(str(error), path)
    if effective in parsed:
        vitest, vite, engines = parsed[effective]
        result['vitest'], result['vite'] = vitest, vite
        for kind in ('node', 'npm'):
            result[kind + '_engines'] = engines[kind] + ([manifest_engines[kind]] if kind in manifest_engines else [])
        result['lockfiles'] = [{'path': effective, 'sha256': hashlib.sha256(locks[effective]).hexdigest(), 'manager': 'npm'}]
    if len(parsed) > 1:
        values = list(parsed.values())
        if any((v[0], v[1], sorted(v[2]['node']), sorted(v[2]['npm'])) !=
               (values[0][0], values[0][1], sorted(values[0][2]['node']), sorted(values[0][2]['npm'])) for v in values[1:]):
            issue('Locks com versões ou engines divergentes.')
    return result
