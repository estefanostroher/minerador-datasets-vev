"""Protege a cadeia de origem e a seleção local de representantes."""

import hashlib
import json

import pytest

from dataset_fixtures import dataset_chain, empty_dataset_chain  # noqa: F401
from test_validation_input import extraction_rows  # noqa: F401


NAMES = ('discovery', 'triage', 'extraction', 'validation', 'deduplication')


@pytest.mark.parametrize('fixture_name', ['dataset_chain', 'empty_dataset_chain'])
def test_fixture_publications_are_valid(request, fixture_name):
    """A própria construção relê cada publicação com seu leitor real."""
    paths, instance_id = request.getfixturevalue(fixture_name)
    assert all(paths[name].is_file() for name in NAMES)
    assert (instance_id is None) == (fixture_name == 'empty_dataset_chain')


def test_selects_locally_approved_representative(dataset_chain):
    """Seleciona a instância aprovada apesar da rejeição global e origem parcial."""
    from robo_minerador.dataset_input import load_dataset_inputs

    paths, instance_id = dataset_chain
    inputs = load_dataset_inputs(*(paths[name] for name in NAMES))
    assert [candidate['unit']['instance_id'] for candidate in inputs['candidates']] == [instance_id]
    assert inputs['candidates'][0]['validation_repository']['decision'] == 'rejected'
    assert inputs['candidates'][0]['validation_instance']['decision'] == 'approved'
    assert inputs['sources']['discovery']['status'] == 'partial'


def test_empty_chain_selects_no_candidates(empty_dataset_chain):
    """Aceita cadeia íntegra sem representantes."""
    from robo_minerador.dataset_input import load_dataset_inputs

    paths, _ = empty_dataset_chain
    assert load_dataset_inputs(*(paths[name] for name in NAMES))['candidates'] == []


def test_changed_discovery_bytes_invalidate_chain(dataset_chain, monkeypatch):
    """Recusa outra coleta antes de iniciar a conferência sintática em Node."""
    from robo_minerador import deduplication_input
    from robo_minerador.dataset_input import load_dataset_inputs

    paths, _ = dataset_chain
    paths['discovery'].write_bytes(paths['discovery'].read_bytes() + b' ')

    def forbidden():
        raise AssertionError('Node não deve ser iniciado para cadeia inválida.')

    monkeypatch.setattr(deduplication_input, 'preflight', forbidden)
    with pytest.raises(ValueError):
        load_dataset_inputs(*(paths[name] for name in NAMES))


def test_metadata_preserves_known_false_values(dataset_chain):
    """Estrelas zero e README ausente são observações disponíveis."""
    from robo_minerador.dataset_input import load_dataset_inputs

    paths, _ = dataset_chain
    inputs = load_dataset_inputs(*(paths[name] for name in NAMES))
    source = inputs['candidates'][0]['source']
    assert source['stars'] == {'status': 'available', 'value': 0, 'reason': None}
    assert source['readme'] == {'status': 'available', 'value': {'present': False, 'paths': [], 'total': 0}, 'reason': None}
    assert source['license']['spdx_id'] == 'MIT'
    assert set(inputs) == {'candidates', 'sources', 'deduplication_policy'}


@pytest.mark.parametrize('damage', ['id', 'url', 'sha', 'license'])
def test_rejects_inconsistent_triage_even_with_updated_hashes(empty_dataset_chain, damage):
    """Hashes atualizados não legitimam identidade ou licença incompatível."""
    from robo_minerador.dataset_input import load_dataset_inputs

    paths, _ = empty_dataset_chain
    rows = [json.loads(line) for line in paths['triage'].read_bytes().splitlines()]
    row = next(row for row in rows if row['record_type'] == 'repository')
    if damage == 'id':
        row['repository_id'] += 1
    elif damage == 'url':
        row['repository_url'] = 'https://github.com/other/repo'
    elif damage == 'sha':
        row['commit_sha'] = 'b' * 40
    else:
        row['criteria']['license']['evidence']['spdx_id'] = 'unverified'
    def publish(name, records):
        paths[name].write_bytes(b''.join(json.dumps(record).encode() + b'\n' for record in records))
        return hashlib.sha256(paths[name].read_bytes()).hexdigest()
    triage_hash = publish('triage', rows)
    extraction = [json.loads(line) for line in paths['extraction'].read_bytes().splitlines()]
    extraction[0]['input_sha256'] = triage_hash
    extraction_hash = publish('extraction', extraction)
    validation = [json.loads(line) for line in paths['validation'].read_bytes().splitlines()]
    validation[0]['source']['sha256'] = validation[-1]['source_sha256'] = extraction_hash
    validation_hash = publish('validation', validation)
    dedup = [json.loads(line) for line in paths['deduplication'].read_bytes().splitlines()]
    for name, digest in [('extraction', extraction_hash), ('validation', validation_hash)]:
        dedup[0]['sources'][name]['sha256'] = dedup[-1]['source_sha256'][name] = digest
    publish('deduplication', dedup)
    with pytest.raises(ValueError):
        load_dataset_inputs(*(paths[name] for name in NAMES))


@pytest.mark.parametrize('damage', ['duplicate_key', 'nan', 'unknown_event', 'policy', 'missing_finish'])
def test_rejects_invalid_publications(empty_dataset_chain, damage):
    """Recusa JSON ambíguo, eventos desconhecidos e protocolo 05 inválido."""
    from robo_minerador.dataset_input import load_dataset_inputs

    paths, _ = empty_dataset_chain
    if damage in ('duplicate_key', 'nan', 'unknown_event'):
        path = paths['discovery']
        raw = path.read_bytes()
        if damage == 'duplicate_key':
            raw = raw.replace(b'"record_type":', b'"record_type":"diagnostic","record_type":', 1)
        elif damage == 'nan':
            raw = raw.replace(b'"stars":0', b'"stars":NaN')
        else:
            raw = raw.replace(b'run_started', b'unknown_event', 1)
        path.write_bytes(raw)
    else:
        path = paths['deduplication']
        rows = [json.loads(line) for line in path.read_bytes().splitlines()]
        if damage == 'policy':
            rows[0]['policy']['name'] = 'other'
        else:
            rows.pop()
        path.write_bytes(b''.join(json.dumps(row).encode() + b'\n' for row in rows))
    with pytest.raises(ValueError):
        load_dataset_inputs(*(paths[name] for name in NAMES))


def test_empty_chain_needs_no_binding_tool(empty_dataset_chain, monkeypatch):
    """Zero aprovadas não exige preflight do compilador."""
    from robo_minerador import deduplication_input
    from robo_minerador.dataset_input import load_dataset_inputs

    def forbidden():
        raise AssertionError('Ferramenta não deve ser iniciada.')
    monkeypatch.setattr(deduplication_input, 'preflight', forbidden)
    paths, _ = empty_dataset_chain
    assert load_dataset_inputs(*(paths[name] for name in NAMES))['candidates'] == []


def test_binding_tool_failure_is_translated(dataset_chain, monkeypatch):
    """Distingue indisponibilidade operacional de dados inconsistentes."""
    from robo_minerador import deduplication_input
    from robo_minerador.dataset_input import load_dataset_inputs
    from robo_minerador.dataset_metrics import DatasetToolError

    def unavailable():
        raise OSError('indisponível')
    monkeypatch.setattr(deduplication_input, 'preflight', unavailable)
    paths, _ = dataset_chain
    with pytest.raises(DatasetToolError):
        load_dataset_inputs(*(paths[name] for name in NAMES))


@pytest.mark.parametrize('value', [-1, True, '10'])
def test_invalid_stars_rejected(value):
    """Estrelas disponíveis representam uma contagem inteira não negativa."""
    from robo_minerador.dataset_input import _stars

    with pytest.raises(ValueError):
        _stars(value)


@pytest.mark.parametrize('value', [
    {'present': False, 'paths': ['README.md'], 'count': 1},
    {'present': True, 'paths': [], 'count': 0},
    {'present': True, 'paths': ['README.md'], 'count': False},
    {'present': True, 'paths': ['README.md', 'README.md'], 'count': 2},
])
def test_inconsistent_readme_rejected(value):
    """Presença, amostra e contagem do README devem ser coerentes."""
    from robo_minerador.dataset_input import _readme

    with pytest.raises(ValueError):
        _readme(value)


def test_license_sample_does_not_imply_complete_tree():
    """Amostra truncada e nome fora da heurística não negam prova do endpoint."""
    from robo_minerador.dataset_input import _license

    criterion = {'status': 'approved', 'evidence': {'spdx_id': 'MIT', 'path': 'LICENSE',
                 'blob_sha': 'a' * 40, 'candidates': {'paths': ['OTHER-LICENSE'], 'count': 1}}}
    row = {'criteria': {'license': criterion}}
    with pytest.raises(ValueError):
        _license(row)
    criterion['evidence']['candidates']['count'] = 21
    criterion['evidence']['candidates']['paths'] = [f'{i:02}-LICENSE' for i in range(20)]
    assert _license(row)['spdx_id'] == 'MIT'
    criterion['evidence']['path'] = 'LEGAL.txt'
    criterion['evidence']['candidates'] = {'paths': [], 'count': 0}
    assert _license(row)['spdx_id'] == 'MIT'


def test_changed_read_during_discovery_cannot_mix_provenance(dataset_chain, monkeypatch):
    """Reabertura alterada e restaurada não pode vincular estrelas a outro hash."""
    from robo_minerador import dataset_input

    paths, _ = dataset_chain
    original = dataset_input.load_discovery

    def changed(path):
        raw = path.read_bytes()
        try:
            path.write_bytes(raw.replace(b'"stars":0', b'"stars":9'))
            return original(path)
        finally:
            path.write_bytes(raw)

    monkeypatch.setattr(dataset_input, 'load_discovery', changed)
    with pytest.raises(ValueError):
        dataset_input.load_dataset_inputs(*(paths[name] for name in NAMES))


def test_published_deduplication_message_is_preserved(dataset_chain):
    """Texto explicativo não altera identidade científica do representante."""
    from robo_minerador.dataset_input import load_dataset_inputs

    paths, _ = dataset_chain
    rows = [json.loads(line) for line in paths['deduplication'].read_bytes().splitlines()]
    message = 'Descrição histórica do representante.'
    rows[1]['criteria']['exact_uniqueness']['message'] = message
    paths['deduplication'].write_bytes(b''.join(json.dumps(row).encode('utf-8') + b'\n' for row in rows))
    inputs = load_dataset_inputs(*(paths[name] for name in NAMES))
    assert inputs['candidates'][0]['representative']['criteria']['exact_uniqueness']['message'] == message


def test_changed_validation_during_binding_is_rejected(empty_dataset_chain, monkeypatch):
    """O hash do leitor de vínculos também deve corresponder à leitura inicial."""
    from robo_minerador import dataset_input

    paths, _ = empty_dataset_chain
    original = dataset_input.load_deduplication_inputs

    def changed(validation, extraction):
        raw = validation.read_bytes()
        try:
            validation.write_bytes(b' ' + raw)
            return original(validation, extraction)
        finally:
            validation.write_bytes(raw)

    monkeypatch.setattr(dataset_input, 'load_deduplication_inputs', changed)
    with pytest.raises(ValueError):
        dataset_input.load_dataset_inputs(*(paths[name] for name in NAMES))
