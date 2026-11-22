"""Resolve receitas cadastradas preservando a identidade da origem."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path
import re

from .extraction_input import strict_object
from .triage import _invalid_constant
from .validation_recipe import DEFAULT_LIMITS, _hash, _validate_recipe, resolve_recipe
from .validation_recipe_analysis import analyze_recipe_inputs


DEFAULT_POLICY = Path(__file__).with_name("validation_recipe_policy.json")
_LIMITS = {"max_files": 64, "max_nonlock_bytes": 1048576, "max_lock_bytes": 8388608,
           "max_total_bytes": 16777216, "helper_seconds": 30, "helper_stdout_bytes": 1048576}
_ENVIRONMENTS = {
    ("node@sha256:539d4d5b9c0df96bb9d1229264d358dc816e8592cb8867828a580b527e7ad0fa", "linux/amd64", "22.23.4", "10.9.9"),
    ("node@sha256:6b9d4ea31e614e29f364bf75758458731eb1e105b80c99667438dd6b3fe262e0", "linux/amd64", "24.22.2", "11.19.1"),
    ("node@sha256:1d9e3c29bf8eae6becc330e2546d461b23c1a86ac9207d015b5ea8ba26cbe210", "linux/amd64", "26.13.0", "11.20.0"),
}


def load_recipe_policy(path=DEFAULT_POLICY) -> dict:
    """Valida a política global e calcula o SHA dos bytes originais."""
    raw = Path(path).read_bytes()
    policy = json.loads(raw.decode("utf-8"), object_pairs_hook=strict_object,
                        parse_constant=_invalid_constant)
    if not isinstance(policy, dict) or 'sha256' in policy:
        raise ValueError('Política de receitas inválida.')
    policy['sha256'] = hashlib.sha256(raw).hexdigest()
    _validate_policy(policy)
    return policy


def _validate_policy(policy):
    from .validation_instrument import MATRIX

    def require(condition):
        if not condition:
            raise ValueError("Política de receitas inválida.")

    require(isinstance(policy, dict) and set(policy) == {"version", "profiles", "rules", "limits", "sha256"})
    require(isinstance(policy['sha256'], str) and re.fullmatch(r'[a-f0-9]{64}', policy['sha256']) is not None)
    require(type(policy["version"]) is int and policy["version"] == 1)
    require(policy["rules"] == {"scope": "single_npm_local_vitest_v1"})
    limits = policy["limits"]
    require(isinstance(limits, dict) and set(limits) == set(_LIMITS))
    require(all(type(limits[k]) is int and 0 < limits[k] <= maximum for k, maximum in _LIMITS.items()))
    profiles = policy["profiles"]
    require(isinstance(profiles, list) and bool(profiles))
    seen = set()
    for profile in profiles:
        require(isinstance(profile, dict) and set(profile) == {"image", "platform", "node", "npm", "vitest", "vite"})
        require(all(isinstance(profile[k], str) and re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", profile[k]) for k in ("node", "npm", "vitest", "vite")))
        require(all(isinstance(profile[k], str) for k in ("image", "platform")))
        environment = tuple(profile[k] for k in ("image", "platform", "node", "npm"))
        trio = tuple(profile[k] for k in ("node", "vitest", "vite"))
        require(environment in _ENVIRONMENTS and trio in MATRIX and trio not in seen)
        seen.add(trio)


def _valid_snapshot(snapshot, repository):
    fields = {'format', 'cache_key', 'repository_id', 'commit_sha', 'tree_oid',
              'manifest_sha256', 'file_count', 'total_blob_bytes', 'status'}
    return (isinstance(snapshot, dict) and set(snapshot) == fields
            and type(snapshot['repository_id']) is int and snapshot['repository_id'] == repository['repository_id']
            and snapshot['commit_sha'] == repository['commit_sha']
            and isinstance(snapshot['commit_sha'], str) and re.fullmatch(r'[a-f0-9]{40}', snapshot['commit_sha']) is not None
            and snapshot['cache_key'] == f"{repository['repository_id']}/{repository['commit_sha']}"
            and snapshot['format'] == 'git-bare-v1' and snapshot['status'] == 'complete'
            and all(isinstance(snapshot[key], str) and re.fullmatch(pattern, snapshot[key]) is not None
                    for key, pattern in (('tree_oid', r'[a-f0-9]{40}'), ('manifest_sha256', r'[a-f0-9]{64}')))
            and all(type(snapshot[key]) is int and snapshot[key] >= 0 for key in ('file_count', 'total_blob_bytes')))


def _issue_order(issue):
    evidence = issue['evidence']
    code = issue['code']
    reason = evidence.get('reason')
    priority = (-1 if code not in {'AUTO_RECIPE_INPUT_UNSUPPORTED', 'AUTO_RECIPE_PREPARATION_UNRESOLVED',
                                  'UNIT_SCOPE_UNRESOLVED', 'AUTO_RECIPE_ENVIRONMENT_UNSUPPORTED'} else
                0 if code == 'AUTO_RECIPE_INPUT_UNSUPPORTED' or reason == 'DOCUMENT_PATH_AMBIGUOUS' else
                1 if reason == 'CI_WORKFLOW_UNSUPPORTED' else
                2 if code == 'AUTO_RECIPE_PREPARATION_UNRESOLVED' else
                3 if code == 'UNIT_SCOPE_UNRESOLVED' else 4)
    path = evidence.get('path', '')
    return (priority, path.encode('utf-8') if isinstance(path, str) else b'',
            evidence.get('line', 0), evidence.get('column', 0), code,
            json.dumps(issue, sort_keys=True, ensure_ascii=True))


def resolve_effective_recipe(repository, catalog, store, policy) -> tuple[dict, list]:
    """Retorna a resolução declarativa e seus diagnósticos internos."""
    repository_id = repository["repository_id"]
    repository_url = repository["repository_url"].lower()
    for recipe in catalog["recipes"].values():
        same_id = recipe["repository_id"] == repository_id
        same_url = recipe["repository_url"].lower() == repository_url
        if same_id != same_url:
            raise ValueError("Identidade da receita diverge da origem publicada.")

    envelope = resolve_recipe(repository, catalog)
    if (repository_id, repository['commit_sha']) in catalog['recipes']:
        return envelope, []

    _validate_policy(policy)
    context = repository.get('context')
    reference = context.get('snapshot') if isinstance(context, dict) else None
    if not _valid_snapshot(reference, repository):
        envelope['code'] = 'AUTO_RECIPE_INPUT_UNSUPPORTED'
        return envelope, [{'code': envelope['code'], 'message': 'Snapshot de outra origem.', 'evidence': {}}]

    analysis = analyze_recipe_inputs(repository, store, policy)
    snapshot = analysis['snapshot']
    if snapshot is not None and (not _valid_snapshot(snapshot, repository) or snapshot != reference):
        envelope['code'] = 'AUTO_RECIPE_INPUT_UNSUPPORTED'
        return envelope, [{'code': envelope['code'], 'message': 'Snapshot de outra origem.', 'evidence': {}}] + sorted(deepcopy(analysis['issues']), key=_issue_order)
    facts = analysis['facts']
    diagnostics = deepcopy(analysis['issues'])
    inputs = deepcopy(analysis['inputs'])
    input_paths = {item['path'] for item in inputs}
    evidence = deepcopy(facts.get('evidence', []))
    profile = facts.get('environment_profile')
    if profile not in policy['profiles']:
        profile = None
    if analysis['snapshot'] is not None and inputs and evidence and all(
            item['path'] in input_paths for item in evidence):
        envelope['provenance'] = {
            'kind': 'automatic', 'resolver_version': 'snapshot_npm_v1',
            'policy_sha256': policy['sha256'], 'snapshot': deepcopy(analysis['snapshot']),
            'inputs': inputs, 'rule': policy['rules']['scope'], 'evidence': deepcopy(evidence),
            'environment_profile': deepcopy(profile),
        }
    if not diagnostics:
        scope_evidence = [item['path'] for item in facts.get('scope_evidence', [])]
        config = facts.get('config') or {}
        if config.get('path') in input_paths and config['path'] not in scope_evidence:
            scope_evidence.append(config['path'])
        locks = facts.get('lockfiles', [])
        complete = (profile is not None and analysis['snapshot'] is not None and inputs and evidence
                    and 'provenance' in envelope and facts.get('scope_rule') == policy['rules']['scope']
                    and facts.get('package_path') and facts.get('package_name') and locks
                    and scope_evidence and set(scope_evidence) <= input_paths
                    and facts.get('vitest') == profile['vitest'] and facts.get('vite') == profile['vite']
                    and all(lock['path'] in input_paths and any(
                        item['path'] == lock['path'] and item['sha256'] == lock['sha256'] for item in inputs)
                            for lock in locks))
        if not complete:
            diagnostics.append({'code': 'AUTO_RECIPE_INPUT_UNSUPPORTED',
                                'message': 'Fatos insuficientes para gerar receita.', 'evidence': {}})
    if diagnostics:
        diagnostics.sort(key=_issue_order)
        envelope['code'] = diagnostics[0]['code']
        return envelope, diagnostics

    cwd = facts['package_path']
    references = [item['path'] for item in evidence]
    recipe = {
        **{key: repository[key] for key in ('repository_id', 'repository_url', 'commit_sha')},
        'recipe_version': 1, 'image': profile['image'], 'platform': profile['platform'],
        'tools': {key: profile[key] for key in ('node', 'npm', 'vitest')},
        'lockfiles': deepcopy(locks), 'environment': {},
        'phases': {'installation': [{'argv': ['npm', 'ci'], 'cwd': cwd}], 'preparation': [],
                   'unit_suite': [{'argv': ['npm', 'test'], 'cwd': cwd}]},
        'unit_scope': {'status': 'approved', 'code': None,
                       'packages': [{'path': cwd, 'name': facts['package_name'], 'scope': facts['scope_rule']}],
                       'evidence': scope_evidence,
                       'external_services': {'status': 'not_evaluated',
                                             'message': 'Serviços externos não foram avaliados.',
                                             'evidence': references}},
        'limits': deepcopy(DEFAULT_LIMITS), 'evidence': evidence, 'resolution_status': 'resolved',
        'resolution': {'code': None, 'message': 'Receita gerada a partir do snapshot verificado.',
                       'evidence': references},
    }
    _validate_recipe(recipe)
    envelope.update(resolution_status='resolved', code=None, recipe=recipe, recipe_sha256=_hash(recipe))
    return envelope, diagnostics
