import json
import os
import signal
import subprocess
import sys
from types import SimpleNamespace

import pytest

from robo_minerador import config


def test_help_is_available():
    result = subprocess.run([sys.executable, '-m', 'robo_minerador.pipeline', '--help'], capture_output=True)
    assert result.returncode == 0


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    from robo_minerador import pipeline
    for name in ('OUTPUT_PATH', 'TRIAGE_OUTPUT_PATH', 'EXTRACTION_OUTPUT_PATH',
                 'VALIDATION_OUTPUT_PATH', 'DEDUPLICATION_OUTPUT_PATH', 'DATASET_OUTPUT_PATH'):
        monkeypatch.setattr(config, name, tmp_path / name)
    return pipeline


@pytest.mark.parametrize('args', [[], ['--discover', '0'], ['--all', '-1'], ['--discover', 'x'],
                                  ['--all', '--triage'], ['--workers', '2'], ['--limit', '2']])
def test_bad_arguments(pipeline, args):
    with pytest.raises(SystemExit) as exc:
        pipeline.parse_arguments(args)
    assert exc.value.code == 2


def test_canonical_order_and_defaults(pipeline, monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline, 'run_stage', lambda args: calls.append(args) or 0)
    assert pipeline.main(['--triage', '--discover', '--build-dataset']) == 0
    assert calls == [['discover', '--limit', str(config.MAX_REPOSITORIES)], ['triage'], ['build-dataset']]


def test_preflight_checks_last_output_before_starting(pipeline, monkeypatch):
    config.DATASET_OUTPUT_PATH.touch()
    monkeypatch.setattr(pipeline, 'run_stage', lambda args: pytest.fail('started'))
    assert pipeline.main(['--all']) == 2


@pytest.mark.parametrize('second, expected', [(0, 1), (2, 2), (-9, 1)])
def test_partial_then_result(pipeline, monkeypatch, second, expected):
    calls = []
    def run(args):
        calls.append(args)
        if len(calls) == 1:
            config.OUTPUT_PATH.write_text('new')
            return 1
        return second
    monkeypatch.setattr(pipeline, 'run_stage', run)
    monkeypatch.setattr(pipeline.pipeline_status, 'accept_partial', lambda stage, path: True)
    assert pipeline.main(['--discover', '--triage']) == expected
    assert len(calls) == 2


def test_stale_partial_stops(pipeline, monkeypatch):
    config.OUTPUT_PATH.write_text('old')
    calls = []
    monkeypatch.setattr(pipeline, 'run_stage', lambda args: calls.append(args) or 1)
    monkeypatch.setattr(pipeline.pipeline_status, 'accept_partial', lambda stage, path: True)
    assert pipeline.main(['--discover', '--triage', '--overwrite']) == 1
    assert len(calls) == 1


@pytest.mark.parametrize('change_content', [False, True])
def test_partial_requires_changed_bytes(pipeline, monkeypatch, change_content):
    def publication(message):
        rows = [
            {'record_type': 'diagnostic', 'stage': 'discovery', 'event': 'run_started', 'message': message},
            {'record_type': 'diagnostic', 'stage': 'discovery', 'event': 'run_finished',
             'repository_count': 0, 'status': 'partial'},
        ]
        return '\n'.join(map(json.dumps, rows)) + '\n'

    config.OUTPUT_PATH.write_text(publication('old'), encoding='utf-8')
    calls = []

    def run(args):
        calls.append(args)
        if len(calls) == 1:
            if change_content:
                config.OUTPUT_PATH.write_text(publication('new'), encoding='utf-8')
            else:
                stat = config.OUTPUT_PATH.stat()
                os.utime(config.OUTPUT_PATH, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
            return 1
        return 0

    monkeypatch.setattr(pipeline, 'run_stage', run)
    assert pipeline.main(['--discover', '--triage', '--overwrite']) == 1
    assert len(calls) == (2 if change_content else 1)


def test_cancel_reaps_child(pipeline, monkeypatch):
    class Child:
        def __init__(self): self.calls = []
        def wait(self, timeout=None):
            self.calls.append(('wait', timeout))
            if len(self.calls) == 1: raise KeyboardInterrupt
            return 0
        def send_signal(self, sig): self.calls.append(('signal', sig))
    child = Child()
    monkeypatch.setattr(pipeline.subprocess, 'Popen', lambda *args, **kwargs: child)
    assert pipeline.main(['--triage', '--extract']) == 1
    assert len(child.calls) == 3


def test_termination_signal_triggers_cancellation(pipeline):
    with pytest.raises(KeyboardInterrupt):
        pipeline._interrupt(None, None)


def test_cancel_waits_without_deadline_for_cleanup(pipeline, monkeypatch):
    monkeypatch.setattr(pipeline, 'os', SimpleNamespace(name='posix'))
    class Child:
        def __init__(self): self.calls = []
        def send_signal(self, sig): self.calls.append(('signal', sig))
        def wait(self, timeout=None):
            self.calls.append(('wait', timeout))
            return 0
        def terminate(self): pytest.fail('cleanup was forcibly terminated')
    child = Child()
    pipeline._cancel(child)
    assert child.calls == [('signal', signal.SIGINT), ('wait', None)]


@pytest.mark.skipif(os.name != 'nt', reason='CTRL_BREAK_EVENT existe somente no Windows')
def test_windows_ctrl_break_runs_entrypoint_cleanup():
    script = '''
import runpy, time
import robo_minerador.cli
def main():
    try:
        print("ready", flush=True)
        while True:
            time.sleep(0.1)
    finally:
        print("cleanup", flush=True)
robo_minerador.cli.main = main
runpy.run_module("robo_minerador", run_name="__main__")
'''
    child = subprocess.Popen([sys.executable, '-u', '-c', script], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True,
                             creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    try:
        assert child.stdout.readline().strip() == 'ready'
        child.send_signal(signal.CTRL_BREAK_EVENT)
        stdout, stderr = child.communicate(timeout=15)
        assert 'cleanup' in stdout, (child.returncode, stderr)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate()


def test_cancel_escalates_to_kill_and_reaps(pipeline):
    class Child:
        def __init__(self): self.calls = []
        def send_signal(self, sig): self.calls.append('signal')
        def terminate(self): self.calls.append('terminate')
        def kill(self): self.calls.append('kill')
        def wait(self, timeout=None):
            self.calls.append('wait')
            if self.calls == ['signal', 'wait']:
                raise KeyboardInterrupt
            if timeout is not None:
                raise subprocess.TimeoutExpired('child', timeout)
            return -1
    child = Child()
    pipeline._cancel(child)
    assert child.calls == ['signal', 'wait', 'terminate', 'wait', 'kill', 'wait']


def test_all_forwards_limit_and_overwrite(pipeline, monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline, 'run_stage', lambda args: calls.append(args) or 0)
    assert pipeline.main(['--all', '17', '--overwrite']) == 0
    assert calls == [['discover', '--limit', '17', '--overwrite'],
                     *[[stage, '--overwrite'] for stage in pipeline.STAGES[1:]]]


def test_invalid_new_partial_stops(pipeline, monkeypatch):
    calls = []
    def run(args):
        calls.append(args)
        config.OUTPUT_PATH.write_text('invalid')
        return 1
    monkeypatch.setattr(pipeline, 'run_stage', run)
    assert pipeline.main(['--discover', '--triage']) == 1
    assert len(calls) == 1


@pytest.mark.parametrize('stage', ['--deduplicate', '--build-dataset'])
def test_final_stages_never_continue_on_one(pipeline, monkeypatch, stage):
    monkeypatch.setattr(pipeline, 'run_stage', lambda args: 1)
    monkeypatch.setattr(pipeline.pipeline_status, 'accept_partial', lambda *args: pytest.fail('checked'))
    assert pipeline.main([stage]) == 1
