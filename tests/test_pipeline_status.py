import json

import pytest
from test_extraction_input import candidate, save_rows, triage_rows
from test_validation import published_fixture
from test_validation_input import extraction_rows, write_rows


@pytest.mark.parametrize('status, count, expected', [('partial', 0, True), ('failed', 0, False),
                                                     ('partial', 1, False)])
def test_discovery_reader_validates_closure(tmp_path, status, count, expected):
    from robo_minerador.pipeline_status import accept_partial
    rows = [{'record_type': 'diagnostic', 'stage': 'discovery', 'event': 'run_started'},
            {'record_type': 'diagnostic', 'stage': 'discovery', 'event': 'run_finished',
             'status': status, 'repository_count': count}]
    path = tmp_path / 'output'
    path.write_text('\n'.join(map(json.dumps, rows)), encoding='utf-8')
    assert accept_partial('discover', path) is expected


@pytest.mark.parametrize('stage', ['discover', 'triage', 'extract', 'validate', 'deduplicate'])
def test_invalid_publication_rejected(tmp_path, stage):
    from robo_minerador.pipeline_status import accept_partial
    path = tmp_path / 'output'
    path.write_text('{"status":"partial"}\n{', encoding='utf-8')
    assert not accept_partial(stage, path)


def test_real_triage_partial_and_inconsistent_count(tmp_path):
    from robo_minerador.pipeline_status import accept_partial
    rows = triage_rows([candidate(), candidate(2, 'not_evaluated')])
    rows[2]['commit_sha'] = None
    path = tmp_path / 'triage'
    save_rows(path, rows)
    assert accept_partial('triage', path)
    rows[-1]['repository_count'] += 1
    save_rows(path, rows)
    assert not accept_partial('triage', path)


def test_real_extraction_partial_and_missing_close(tmp_path, extraction_rows):
    from robo_minerador.pipeline_status import accept_partial
    path, _ = write_rows(tmp_path, extraction_rows)
    assert accept_partial('extract', path)
    path, _ = write_rows(tmp_path, extraction_rows[:-1])
    assert not accept_partial('extract', path)


def test_real_validation_partial_and_inconsistent_count(monkeypatch, tmp_path):
    from robo_minerador.pipeline_status import accept_partial
    rows, _, path = published_fixture(monkeypatch, tmp_path, fail_first=True)
    assert accept_partial('validate', path)
    rows[-1]['repository_count'] += 1
    path.write_text('\n'.join(map(json.dumps, rows)), encoding='utf-8')
    assert not accept_partial('validate', path)
