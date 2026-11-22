import json

import pytest
from test_extraction_input import candidate, save_rows, triage_rows
from test_validation import published_fixture
from test_validation_input import extraction_rows, write_rows


@pytest.mark.parametrize('status, count, expected', [('partial', 0, True), ('failed', 0, False),
                                                     ('partial', 1, False)])
def test_discovery_reader_validates_closure(tmp_path, status, count, expected):
    from robo_minerador.pipeline_status import accept_publication
    rows = [{'record_type': 'diagnostic', 'stage': 'discovery', 'event': 'run_started'},
            {'record_type': 'diagnostic', 'stage': 'discovery', 'event': 'run_finished',
             'status': status, 'repository_count': count}]
    path = tmp_path / 'output'
    path.write_text('\n'.join(map(json.dumps, rows)), encoding='utf-8')
    assert accept_publication('discover', path) is expected


@pytest.mark.parametrize('stage', ['discover', 'triage', 'extract', 'validate', 'deduplicate'])
def test_invalid_publication_rejected(tmp_path, stage):
    from robo_minerador.pipeline_status import accept_publication
    path = tmp_path / 'output'
    path.write_text('{"status":"partial"}\n{', encoding='utf-8')
    assert not accept_publication(stage, path)


def test_real_triage_partial_and_inconsistent_count(tmp_path):
    from robo_minerador.pipeline_status import accept_publication
    rows = triage_rows([candidate(), candidate(2, 'not_evaluated')])
    rows[2]['commit_sha'] = None
    path = tmp_path / 'triage'
    save_rows(path, rows)
    assert accept_publication('triage', path)
    rows[-1]['repository_count'] += 1
    save_rows(path, rows)
    assert not accept_publication('triage', path)


def test_real_extraction_partial_and_missing_close(tmp_path, extraction_rows):
    from robo_minerador.pipeline_status import accept_publication
    path, _ = write_rows(tmp_path, extraction_rows)
    assert accept_publication('extract', path)
    path, _ = write_rows(tmp_path, extraction_rows[:-1])
    assert not accept_publication('extract', path)


def test_real_validation_partial_and_inconsistent_count(monkeypatch, tmp_path):
    from robo_minerador.pipeline_status import accept_publication
    rows, _, path = published_fixture(monkeypatch, tmp_path, fail_first=True)
    assert accept_publication('validate', path)
    rows[-1]['repository_count'] += 1
    path.write_text('\n'.join(map(json.dumps, rows)), encoding='utf-8')
    assert not accept_publication('validate', path)


@pytest.mark.parametrize('damage', [None, 'closing', 'counts'])
def test_failed_validation_requires_integral_publication(monkeypatch, tmp_path, damage):
    from robo_minerador.pipeline_status import accept_publication
    rows, _, path = published_fixture(monkeypatch, tmp_path, fail_all=True, policy_version=2)
    assert rows[-1]['status'] == 'failed'
    if damage == 'closing':
        rows.pop()
    elif damage == 'counts':
        rows[-1]['instance_count'] += 1
    path.write_text('\n'.join(map(json.dumps, rows)), encoding='utf-8')
    assert accept_publication('validate', path) is (damage is None)
