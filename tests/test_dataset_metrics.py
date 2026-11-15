import json
import subprocess

import pytest

from robo_minerador import dataset_metrics as metrics


def candidate():
    code = 'function f() { if (true) {} }'
    return {'unit': {'instance_id': 'x', 'repository_id': 1, 'commit_sha': 'a',
                     'function': {'path': 'a.ts', 'kind': 'function_declaration', 'reference_code': code}},
            'files': {'a.ts': code},
            'validation_repository': {'unit_suite': {'plan': {'targets': {
                'x': {'id': 'x', 'path': 'a.ts', 'start': 0, 'end': len(code)}}}}}}


def test_empty_does_not_preflight(monkeypatch):
    monkeypatch.setattr(metrics, 'preflight', lambda: pytest.fail('unexpected preflight'))
    assert metrics.measure_instances([]) == {}


def test_real_helper():
    result = metrics.measure_instances([candidate()])['x']
    assert result['size']['physical_lines'] == 1
    assert result['cyclomatic_complexity']['value'] == 2


def test_protocol_and_exit_errors(monkeypatch):
    monkeypatch.setattr(metrics, 'preflight', lambda: {'node': 'node'})
    class Process:
        returncode = 0
        def communicate(self, payload=None, timeout=None):
            return (json.dumps({'policy': 'typescript_function_metrics_v1', 'typescript': '6.0.3',
                                'measurements': [{'instance_id': 'x', 'physical_lines': True, 'cyclomatic_complexity': 1}]}).encode(), b'secret')
    monkeypatch.setattr(metrics.subprocess, 'Popen', lambda *a, **k: Process())
    with pytest.raises(metrics.DatasetToolError):
        metrics.measure_instances([candidate()])
    Process.returncode = 2
    with pytest.raises(ValueError):
        metrics.measure_instances([candidate()])


def test_timeout_kills_and_reaps(monkeypatch):
    monkeypatch.setattr(metrics, 'preflight', lambda: {'node': 'node'})
    class Process:
        killed = False
        calls = 0
        def communicate(self, payload=None, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired('node', timeout)
            return b'', b''
        def kill(self):
            self.killed = True
    process = Process()
    monkeypatch.setattr(metrics.subprocess, 'Popen', lambda *a, **k: process)
    with pytest.raises(metrics.DatasetToolError):
        metrics.measure_instances([candidate()])
    assert process.killed and process.calls == 2


def test_keyboard_interrupt_kills_reaps_and_propagates(monkeypatch):
    monkeypatch.setattr(metrics, 'preflight', lambda: {'node': 'node'})
    class Process:
        killed = False
        calls = 0
        def communicate(self, payload=None, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise KeyboardInterrupt()
            return b'', b''
        def kill(self):
            self.killed = True
    process = Process()
    monkeypatch.setattr(metrics.subprocess, 'Popen', lambda *a, **k: process)
    with pytest.raises(KeyboardInterrupt):
        metrics.measure_instances([candidate()])
    assert process.killed and process.calls == 2


def test_strict_json_rejects_duplicate_keys(monkeypatch):
    monkeypatch.setattr(metrics, 'preflight', lambda: {'node': 'node'})
    class Process:
        returncode = 0
        def communicate(self, payload=None, timeout=None):
            return b'{"policy":"x","policy":"x"}', b''
    monkeypatch.setattr(metrics.subprocess, 'Popen', lambda *a, **k: Process())
    with pytest.raises(metrics.DatasetToolError):
        metrics.measure_instances([candidate()])
