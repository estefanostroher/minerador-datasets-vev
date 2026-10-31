"""Verifica integridade da entrada, decisões conservadoras e continuidade operacional."""

import hashlib
import json
from contextlib import contextmanager

from github import GithubException
import pytest
from requests.exceptions import ConnectionError

from robo_minerador import cli, config, triage


def candidate(repository_id=1):
    """Cria identidade canônica com SHA imutável para simulações sem rede."""
    return {"record_type": "repository", "repository_id": repository_id,
            "repository": f"owner/repo-{repository_id}",
            "repository_url": f"https://github.com/owner/repo-{repository_id}",
            "commit_sha": f"{repository_id:040x}", "sha_status": "resolved"}


def discovery_rows(candidates, status="completed"):
    """Envolve candidatos nos diagnósticos obrigatórios da descoberta."""
    return [{"record_type": "diagnostic", "stage": "discovery", "event": "run_started"},
            *candidates,
            {"record_type": "diagnostic", "stage": "discovery", "event": "run_finished",
             "repository_count": len(candidates), "status": status}]


def save_rows(path, rows):
    """Persiste uma entrada sintética em UTF-8 para exercer o leitor real."""
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def evaluation(statuses=None):
    """Fornece critérios controlados para verificar agregação sem repetir o avaliador."""
    statuses = statuses or {name: "approved" for name in triage.CRITERION_NAMES}
    return {"criteria": {name: {"status": status, "reason": "Motivo controlado.",
                                "evidence": {}} for name, status in statuses.items()},
            "readme": {"present": False}, "reasons": []}


@pytest.mark.parametrize("changes", [
    {"repository_id": True}, {"repository_id": 0}, {"repository_id": "1"},
    {"commit_sha": "abc"}, {"commit_sha": 12}, {"sha_status": "unavailable"},
    {"commit_sha": None}, {"repository_url": "https://github.com/other/repo-1"},
    {"repository_url": "https://github.com/owner/repo-1?token=secret"},
])
def test_invalid_identity_and_sha_rejected(tmp_path, changes):
    """Impede seleção de outro repositório ou snapshot por entrada inconsistente."""
    item = candidate()
    item.update(changes)
    path = tmp_path / "input.jsonl"
    save_rows(path, discovery_rows([item]))
    with pytest.raises(ValueError):
        triage.load_discovery(path)


@pytest.mark.parametrize("damage", ["duplicate", "truncated", "count", "stage", "type", "object", "utf8", "json", "nan"])
def test_invalid_discovery_structure_rejected(tmp_path, damage):
    """Evita aceitar coleta truncada, duplicada ou contagens/diagnósticos incompatíveis."""
    rows = discovery_rows([candidate()])
    if damage == "duplicate":
        rows = discovery_rows([candidate(), candidate()])
    elif damage == "truncated":
        rows.pop()
    elif damage == "count":
        rows[-1]["repository_count"] = True
    elif damage == "stage":
        rows[0]["stage"] = "triage"
    elif damage == "type":
        rows[1]["record_type"] = "unknown"
    elif damage == "object":
        rows[1] = []
    path = tmp_path / "input.jsonl"
    save_rows(path, rows)
    if damage == "utf8":
        path.write_bytes(b"\xff")
    elif damage == "json":
        path.write_bytes(b'{"record_type":')
    elif damage == "nan":
        path.write_text(path.read_text(encoding="utf-8").replace('"repository_id": 1', '"repository_id": NaN'), encoding="utf-8")
    with pytest.raises(ValueError):
        triage.load_discovery(path)


@pytest.mark.parametrize("mode", ["missing_sha", "pending", "rejected", "approved", "unavailable", "programming"])
def test_decisions_and_missing_sha_preserve_provenance(tmp_path, monkeypatch, mode):
    """Distingue rejeição de pendência, mantém SHA/hash e propaga erro de programação."""
    item = candidate()
    if mode == "missing_sha":
        item.update(commit_sha=None, sha_status="unavailable")
    path = tmp_path / "input.jsonl"
    save_rows(path, discovery_rows([item], "partial"))
    candidates, source = triage.load_discovery(path)
    assert source == {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      "status": "partial"}
    calls = []

    def snapshot(client, row):
        """Registra uso do SHA e simula limitações e falhas internas distintas."""
        calls.append(row["commit_sha"])
        if mode == "unavailable":
            raise triage.SnapshotUnavailable("truncated_tree", "Árvore truncada.")
        if mode == "programming":
            raise RuntimeError("Defeito interno.")
        return object()

    def evaluate(snapshot):
        """Retorna rejeição parcial ou pendência sem inventar os critérios restantes."""
        if mode == "rejected":
            return evaluation({"license": "rejected"})
        if mode == "pending":
            return evaluation({"access": "approved", "license": "not_evaluated"})
        return evaluation()

    monkeypatch.setattr(triage, "Snapshot", snapshot)
    monkeypatch.setattr(triage, "evaluate_snapshot", evaluate)
    if mode == "programming":
        with pytest.raises(RuntimeError):
            list(triage.screen_repositories(object(), candidates, source))
        return
    records = list(triage.screen_repositories(object(), candidates, source))
    row, finished = records[1:]
    expected = mode if mode in {"approved", "rejected"} else "not_evaluated"
    assert row["decision"] == expected
    assert row["commit_sha"] == item["commit_sha"]
    assert set(row["criteria"]) == set(triage.CRITERION_NAMES)
    assert finished[expected] == 1
    expected_status = "completed" if expected != "not_evaluated" else "partial"
    if mode in {"missing_sha", "unavailable"}:
        expected_status = "failed"
    assert finished["status"] == expected_status
    assert records[0]["input_status"] == "partial"
    if mode in {"missing_sha", "unavailable"}:
        assert row["readme"] is None
    assert calls == ([] if mode == "missing_sha" else [item["commit_sha"]])


@pytest.mark.parametrize("mode", ["truncated", "missing_sha", "mixed"])
def test_no_evaluated_criteria_marks_run_failed(monkeypatch, mode):
    """Impede declarar execução parcial quando nenhum critério pôde ser avaliado."""
    candidates = [candidate(i) for i in range(1, 4)]
    for index, item in enumerate(candidates):
        if mode == "missing_sha" or (mode == "mixed" and index == 0):
            item.update(commit_sha=None, sha_status="unavailable")
    calls = []

    def snapshot(client, row):
        """Simula árvore truncada sem rede e registra somente candidatos com SHA."""
        calls.append(row["repository_id"])
        raise triage.SnapshotUnavailable("truncated_tree", "Árvore truncada.")

    def forbidden_evaluation(snapshot):
        """Rejeita avaliação de um snapshot que não foi recuperado integralmente."""
        pytest.fail("Snapshot indisponível não deve chegar ao avaliador.")

    monkeypatch.setattr(triage, "Snapshot", snapshot)
    monkeypatch.setattr(triage, "evaluate_snapshot", forbidden_evaluation)
    source = {"path": "input", "sha256": "hash", "status": "partial"}
    records = list(triage.screen_repositories(None, candidates, source))
    rows = [row for row in records if row["record_type"] == "repository"]
    assert len(rows) == 3
    assert all(criterion["status"] == "not_evaluated"
               for row in rows for criterion in row["criteria"].values())
    assert records[-1]["status"] == "failed"
    assert records[-1]["repository_count"] == records[-1]["not_evaluated"] == 3
    assert records[-1]["error_count"] == 0
    assert calls == {"truncated": [1, 2, 3], "missing_sha": [], "mixed": [2, 3]}[mode]


@pytest.mark.parametrize("all_fail", [False, True])
def test_api_failure_continues_and_sanitizes(monkeypatch, all_fail):
    """Impede perda dos candidatos seguintes, contagem incorreta e vazamento de credencial."""
    candidates = [candidate(i) for i in range(1, 4)]

    def snapshot(client, row):
        """Falha no candidato intermediário ou em todos para verificar o estado final."""
        if all_fail or row["repository_id"] == 2:
            raise GithubException(503, {"message": "token=secret"},
                                  {"authorization": "secret", "x-ratelimit-remaining": "0"})
        return object()

    def evaluate(snapshot):
        """Simula avaliação científica completa nos candidatos acessíveis."""
        return evaluation()

    monkeypatch.setattr(triage, "Snapshot", snapshot)
    monkeypatch.setattr(triage, "evaluate_snapshot", evaluate)
    records = list(triage.screen_repositories(None, candidates, {"path": "input", "sha256": "hash", "status": "completed"}))
    rows = [row for row in records if row["record_type"] == "repository"]
    assert [row["repository_id"] for row in rows] == [1, 2, 3]
    assert records[-1]["repository_count"] == 3
    assert records[-1]["approved"] == (0 if all_fail else 2)
    assert records[-1]["not_evaluated"] == (3 if all_fail else 1)
    assert records[-1]["status"] == ("failed" if all_fail else "partial")
    assert "secret" not in json.dumps(records)
    errors = [row for row in records if row.get("event") == "repository_error"]
    assert errors[0]["http_status"] == 503
    assert errors[0]["rate_limit_headers"] == {"x-ratelimit-remaining": "0"}


@pytest.mark.parametrize("invalid", ["token", "input", "existing"])
def test_cli_preserves_output_before_network(tmp_path, monkeypatch, capsys, invalid):
    """Protege saída existente contra token/entrada inválidos e sobrescrita não autorizada."""
    path, output = tmp_path / "input.jsonl", tmp_path / "output.jsonl"
    save_rows(path, discovery_rows([candidate()]))
    output.write_bytes(b"previous result\n")
    monkeypatch.setattr(config, "TRIAGE_INPUT_PATH", path)
    monkeypatch.setattr(config, "TRIAGE_OUTPUT_PATH", output)
    monkeypatch.setenv("GITHUB_TOKEN", "secret" if invalid != "token" else "")
    if invalid == "input":
        path.write_bytes(b"invalid token=secret")

    def forbidden_client(token):
        """Detecta qualquer acesso prematuro à integração GitHub."""
        pytest.fail("Cliente não deveria ser criado.")

    monkeypatch.setattr(cli, "create_client", forbidden_client)
    assert cli.main(["triage"] + ([] if invalid == "existing" else ["--overwrite"])) == 2
    assert output.read_bytes() == b"previous result\n"
    assert "secret" not in capsys.readouterr().out


@pytest.mark.parametrize("failure", ["setup", "close", "none"])
def test_cli_streams_results_and_closes_client(tmp_path, monkeypatch, failure):
    """Garante fim persistido e fechamento do cliente, inclusive em falhas operacionais."""
    path, output = tmp_path / "input.jsonl", tmp_path / "output.jsonl"
    save_rows(path, discovery_rows([candidate(), candidate(2)]))
    monkeypatch.setattr(config, "TRIAGE_INPUT_PATH", path)
    monkeypatch.setattr(config, "TRIAGE_OUTPUT_PATH", output)
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    closed = []

    @contextmanager
    def client(token):
        """Simula abertura e fechamento sem realizar requisições reais."""
        if failure == "setup":
            raise ConnectionError("secret")
        try:
            yield object()
        finally:
            closed.append(True)
        if failure == "close":
            raise ConnectionError("secret")

    def snapshot(client, row):
        """Retorna snapshot simulado para testar somente o ciclo de vida da CLI."""
        return object()

    def evaluate(snapshot):
        """Produz decisões científicas completas para distinguir falha no fechamento."""
        return evaluation()

    monkeypatch.setattr(cli, "create_client", client)
    monkeypatch.setattr(triage, "Snapshot", snapshot)
    monkeypatch.setattr(triage, "evaluate_snapshot", evaluate)
    assert cli.main(["triage"]) == (0 if failure == "none" else 1)
    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert records[0]["event"] == "run_started"
    assert records[-1]["event"] == "run_finished"
    assert records[-1]["repository_count"] == 2
    assert records[-1]["status"] == {"setup": "failed", "close": "partial", "none": "completed"}[failure]
    assert closed == ([] if failure == "setup" else [True])
    assert "secret" not in output.read_text(encoding="utf-8")
