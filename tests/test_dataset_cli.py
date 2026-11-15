import json
import os

import pytest

from dataset_fixtures import dataset_chain, empty_dataset_chain  # noqa: F401
from test_validation_input import extraction_rows  # noqa: F401
from robo_minerador import cli, config


NAMES = ('discovery', 'triage', 'extraction', 'validation', 'deduplication')


@pytest.fixture
def configure(monkeypatch, tmp_path):
    def apply(paths):
        for name in NAMES:
            monkeypatch.setattr(config, f'DATASET_{name.upper()}_PATH', paths[name])
        monkeypatch.setattr(config, 'DATASET_OUTPUT_PATH', tmp_path / 'dataset.jsonl')
        monkeypatch.delenv('GITHUB_TOKEN', raising=False)
        return config.DATASET_OUTPUT_PATH
    return apply


def forbidden(*args, **kwargs):
    raise AssertionError('Chamada inesperada')


def test_existing_output_precedes_loading(configure, empty_dataset_chain, monkeypatch):
    from robo_minerador import dataset_cli
    output = configure(empty_dataset_chain[0])
    output.write_bytes(b'original')
    monkeypatch.setattr(dataset_cli, 'load_dataset_inputs', forbidden)
    assert cli.main(['build-dataset']) == 2
    assert output.read_bytes() == b'original'


@pytest.mark.parametrize('kind', ['same', 'hardlink', 'directory', 'parent'])
def test_invalid_output_paths_are_rejected_before_loading(configure, empty_dataset_chain, monkeypatch, tmp_path, kind):
    from robo_minerador import dataset_cli
    paths, _ = empty_dataset_chain
    configure(paths)
    if kind == 'same':
        output = paths['discovery'].parent / '.' / paths['discovery'].name
    elif kind == 'hardlink':
        output = tmp_path / 'alias.jsonl'
        os.link(paths['discovery'], output)
    elif kind == 'directory':
        output = tmp_path
    else:
        output = paths['discovery'] / 'output.jsonl'
    original = paths['discovery'].read_bytes()
    monkeypatch.setattr(config, 'DATASET_OUTPUT_PATH', output)
    monkeypatch.setattr(dataset_cli, 'load_dataset_inputs', forbidden)
    assert cli.main(['build-dataset', '--overwrite']) == 2
    assert paths['discovery'].read_bytes() == original


def test_invalid_input_preserves_overwritten_output(configure, empty_dataset_chain):
    paths, _ = empty_dataset_chain
    output = configure(paths)
    output.write_bytes(b'original')
    paths['discovery'].write_bytes(b'invalid')
    assert cli.main(['build-dataset', '--overwrite']) == 2
    assert output.read_bytes() == b'original'


@pytest.mark.parametrize(('error', 'expected'), [(ValueError('secret'), 2), (OSError('secret'), 2),
                                                (KeyboardInterrupt(), 1)])
def test_input_failures_are_sanitized_and_preserve_output(configure, empty_dataset_chain, monkeypatch, capsys, error, expected):
    from robo_minerador import dataset_cli
    output = configure(empty_dataset_chain[0])
    output.write_bytes(b'original')
    def fail(*args):
        raise error
    monkeypatch.setattr(dataset_cli, 'load_dataset_inputs', fail)
    assert cli.main(['build-dataset', '--overwrite']) == expected
    assert output.read_bytes() == b'original'
    assert 'secret' not in capsys.readouterr().out


def test_operational_and_write_cancellation(configure, empty_dataset_chain, monkeypatch, capsys):
    from robo_minerador import dataset_cli
    output = configure(empty_dataset_chain[0])
    output.write_bytes(b'original')
    def operational(*args):
        raise dataset_cli.DatasetToolError('secret')
    monkeypatch.setattr(dataset_cli, 'measure_instances', operational)
    assert cli.main(['build-dataset', '--overwrite']) == 1
    assert output.read_bytes() == b'original'
    assert 'secret' not in capsys.readouterr().out
    monkeypatch.setattr(dataset_cli, 'measure_instances', lambda candidates: {})
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(dataset_cli, 'write_jsonl', interrupted)
    assert cli.main(['build-dataset', '--overwrite']) == 1
    assert 'incompleto' in capsys.readouterr().out


def test_version_and_write_failures(configure, empty_dataset_chain, monkeypatch, capsys):
    from robo_minerador import dataset_cli
    output = configure(empty_dataset_chain[0])
    output.write_bytes(b'original')
    def fail(*args, **kwargs):
        raise OSError('secret')
    with monkeypatch.context() as patch:
        patch.setattr(dataset_cli.metadata, 'version', fail)
        assert cli.main(['build-dataset', '--overwrite']) == 1
    assert output.read_bytes() == b'original'
    monkeypatch.setattr(dataset_cli, 'write_jsonl', fail)
    assert cli.main(['build-dataset', '--overwrite']) == 1
    assert 'secret' not in capsys.readouterr().out


def test_count_mismatch_preserves_output(configure, empty_dataset_chain, monkeypatch):
    from robo_minerador import dataset_cli
    output = configure(empty_dataset_chain[0])
    output.write_bytes(b'original')
    monkeypatch.setattr(dataset_cli, 'validate_dataset_records', lambda records: 1)
    assert cli.main(['build-dataset', '--overwrite']) == 2
    assert output.read_bytes() == b'original'


def test_empty_without_external_tools(configure, empty_dataset_chain, monkeypatch):
    from robo_minerador import dataset_metrics
    output = configure(empty_dataset_chain[0])
    monkeypatch.setattr(dataset_metrics, 'preflight', forbidden)
    assert cli.main(['build-dataset']) == 0
    assert output.read_bytes() == b''


def test_help_and_workers(capsys):
    with pytest.raises(SystemExit) as help_exit:
        cli.main(['build-dataset', '--help'])
    assert help_exit.value.code == 0
    assert '--overwrite' in capsys.readouterr().out
    with pytest.raises(SystemExit) as error:
        cli.main(['build-dataset', '--workers', '1'])
    assert error.value.code == 2


def test_jsonl_default_keeps_legacy_key_order(tmp_path):
    from robo_minerador.jsonl import write_jsonl
    output = tmp_path / 'legacy.jsonl'
    write_jsonl(output, [{'z': 1, 'a': 'ç'}])
    assert output.read_bytes() == '{"z": 1, "a": "ç"}\n'.encode('utf-8')


def test_real_writes_are_sorted_and_deterministic(configure, dataset_chain):
    from robo_minerador.dataset_format import load_dataset
    from robo_minerador.dataset_input import load_dataset_inputs
    from robo_minerador.deduplication_representation import _snippet
    paths, identity = dataset_chain
    inputs = load_dataset_inputs(*(paths[name] for name in NAMES))
    candidate = inputs['candidates'][0]
    output = configure(dataset_chain[0])
    assert cli.main(['build-dataset']) == 0
    first = output.read_bytes()
    row = json.loads(first)
    assert row['instance_id'] == dataset_chain[1]
    assert load_dataset(output) == [row]
    read_rows = lambda name: [json.loads(line) for line in paths[name].read_text(encoding='utf-8').splitlines()]
    dedup = read_rows('deduplication')
    assert [row['instance_id']] == [r['instance_id'] for r in dedup if r.get('disposition') == 'representative'] == [identity]
    unit = candidate['unit']
    assert row['function'] == unit['function']
    assert row['documentation'] == unit['documentation']
    assert row['tests'] == [{**test, 'original_text': _snippet(test, candidate['files'])} for test in unit['tests']]
    assert {f['path']: f['content'] for f in row['context']['files']} == candidate['files']
    validation = next(r for r in read_rows('validation') if r.get('instance_id') == identity)
    assert row['metrics']['test_cases'] == validation['test_cases']
    assert row['metrics']['coverage'] == validation['coverage']
    assert row['validation']['instance'] == validation
    assert row['validation']['repository_evidence']['decision'] == 'rejected'
    triage = next(r for r in read_rows('triage') if r['record_type'] == 'repository')
    discovery = next(r for r in read_rows('discovery') if r['record_type'] == 'repository')
    assert row['source']['license']['criterion'] == triage['criteria']['license']
    assert row['source']['license']['spdx_id'] == 'MIT'
    assert row['source']['stars']['value'] == discovery['stars'] == 0
    assert first == (json.dumps(row, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(',', ':')) + '\n').encode()
    assert cli.main(['build-dataset', '--overwrite']) == 0
    assert output.read_bytes() == first
