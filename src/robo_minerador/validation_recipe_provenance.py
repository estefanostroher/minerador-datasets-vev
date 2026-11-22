"""Verificação portátil de coerência declarada da proveniência automática."""

import re

from .validation_recipe import _hash, _path, _validate_recipe


def _require(condition):
    if not condition:
        raise ValueError('Proveniência automática inválida.')


def _digest(value, length=64):
    return type(value) is str and re.fullmatch('[0-9a-f]{%d}' % length, value) is not None


def valid_snapshot(snapshot, repository):
    """Confere uma referência completa sem abrir o snapshot."""
    return (type(repository) is dict and type(repository.get('repository_id')) is int
        and repository['repository_id'] > 0 and type(snapshot) is dict and set(snapshot) == {
        'format', 'cache_key', 'repository_id', 'commit_sha', 'tree_oid',
        'manifest_sha256', 'file_count', 'total_blob_bytes', 'status'}
        and type(snapshot['repository_id']) is int and snapshot['repository_id'] > 0
        and snapshot['repository_id'] == repository.get('repository_id')
        and _digest(snapshot['commit_sha'], 40) and snapshot['commit_sha'] == repository.get('commit_sha')
        and snapshot['cache_key'] == f"{snapshot['repository_id']}/{snapshot['commit_sha']}"
        and snapshot['format'] == 'git-bare-v1' and snapshot['status'] == 'complete'
        and _digest(snapshot['tree_oid'], 40) and _digest(snapshot['manifest_sha256'])
        and all(type(snapshot[k]) is int and snapshot[k] >= 0 for k in ('file_count', 'total_blob_bytes')))


def validate_recipe_provenance(resolution, repository, snapshot) -> None:
    """Valida vínculos declarados, sem rederivar análise ou consultar política/store."""
    try:
        p = resolution['provenance']
        _require(type(p) is dict and set(p) == {'kind', 'resolver_version', 'policy_sha256',
                 'snapshot', 'inputs', 'rule', 'evidence', 'environment_profile'})
        _require(p['kind'] == 'automatic' and p['resolver_version'] == 'snapshot_npm_v1'
                 and p['rule'] == 'single_npm_local_vitest_v1' and _digest(p['policy_sha256'])
                 and _digest(resolution.get('catalog_sha256')) and valid_snapshot(p['snapshot'], repository))
        _require(snapshot is None or valid_snapshot(snapshot, repository) and snapshot == p['snapshot'])
        inputs = p['inputs']
        _require(type(inputs) is list and bool(inputs))
        paths = []
        for item in inputs:
            _require(type(item) is dict and set(item) == {'path', 'sha256'}
                     and type(item['path']) is str and _digest(item['sha256']))
            _path(item['path'])
            paths.append(item['path'])
        _require(paths == sorted(set(paths), key=lambda x: x.encode('utf-8')))
        evidence = p['evidence']
        _require(type(evidence) is list and bool(evidence))
        for item in evidence:
            _require(type(item) is dict and set(item) == {'path', 'detail'}
                     and item['path'] in paths and type(item['detail']) is str and bool(item['detail']))
        profile = p['environment_profile']
        if profile is not None:
            _require(type(profile) is dict and set(profile) == {'image', 'platform', 'node', 'npm', 'vitest', 'vite'}
                     and all(type(v) is str and bool(v) for v in profile.values())
                     and re.fullmatch(r'[^\s@]+@sha256:[0-9a-f]{64}', profile['image']) is not None
                     and re.fullmatch(r'[a-z0-9]+/[a-z0-9_]+', profile['platform']) is not None
                     and all(re.fullmatch(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)', profile[k]) is not None
                             for k in ('node', 'npm', 'vitest', 'vite')))
        recipe = resolution.get('recipe')
        status, code = resolution.get('resolution_status'), resolution.get('code')
        _require((status == 'resolved' and code is None) or
                 (status == 'not_evaluated' and type(code) is str and bool(code)))
        if recipe is None:
            _require(status == 'not_evaluated' and resolution.get('recipe_sha256') is None)
        else:
            _validate_recipe(recipe)
            _require(profile is not None and recipe['resolution_status'] == status
                     and recipe['resolution']['code'] == code
                     and resolution.get('recipe_sha256') == _hash(recipe)
                     and all(recipe.get(k) == repository.get(k) for k in ('repository_id', 'repository_url', 'commit_sha'))
                     and all(recipe[k] == profile[k] for k in ('image', 'platform'))
                     and recipe['tools'] == {k: profile[k] for k in ('node', 'npm', 'vitest')}
                     and recipe['evidence'] == evidence)
            digests = {item['path']: item['sha256'] for item in inputs}
            _require(all(digests.get(lock['path']) == lock['sha256'] for lock in recipe['lockfiles']))
    except (KeyError, TypeError, AttributeError, UnicodeError, IndexError) as error:
        raise ValueError('Proveniência automática inválida.') from error
