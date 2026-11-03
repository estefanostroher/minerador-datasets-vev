"""Protege ordem, continuidade, fechamento e preservação da saída anterior."""

import json
import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock, get_ident

from github import GithubException
import pytest

from robo_minerador import cli, concurrency, config, extraction
from robo_minerador.extraction_analyzer import preflight
from test_extraction_input import candidate, save_rows, triage_rows

SOURCE = {"path": "triage.jsonl", "sha256": "a" * 64, "status": "completed", "input_count": 3}
SETTINGS = {"analyzer": {"name": "typescript_compiler_api", "version": "6.0.3"}}


class Client:
    """Registra propriedade por thread e fechamento pelo coordenador."""

    def __init__(self, fail_close=False):
        """Inicializa o cliente simulado sem conexão ou contexto de candidato."""
        self.owner = get_ident()
        self.closed_by = []
        self.fail_close = fail_close

    def close(self):
        """Registra fechamento mesmo quando há falha operacional simulada."""
        self.closed_by.append(get_ident())
        if self.fail_close:
            raise GithubException(503, {"message": "secret"})


class EmptyAnalyzer:
    """Simula análise útil sem declarações para testar coordenação isoladamente."""

    def __init__(self, settings):
        """Preserva os metadados previamente validados."""
        self.settings = settings
        self.cancelled = False

    def analyze(self, files, issues):
        """Retorna ausência comprovada de unidades no conjunto fornecido."""
        return {"instances": [], "issues": issues, "local_dependencies": [], "unresolved_references": []}

    def cancel(self):
        """Registra cancelamento para o protocolo de encerramento do coordenador."""
        self.cancelled = True


def test_bounded_order_and_thread_clients(monkeypatch):
    """Força conclusão inversa por eventos e impede iniciar candidato fora da janela."""
    clients, started, completion = [], [], []
    second_done = Event()
    lock = Lock()
    coordinator = get_ident()

    def factory(token, gate):
        """Registra clientes independentes com o mesmo gate fornecido pelo coordenador."""
        client = Client()
        clients.append(client)
        return client

    def collect(client, row):
        """Completa o segundo antes do primeiro sem usar esperas temporais arbitrárias."""
        assert client.owner == get_ident() and not client.closed_by
        identity = row["repository_id"]
        with lock:
            started.append(identity)
        if identity == 1:
            assert second_done.wait(5)
        completion.append(identity)
        if identity == 2:
            second_done.set()
        return [], []

    monkeypatch.setattr(extraction, "Analyzer", EmptyAnalyzer)
    monkeypatch.setattr(extraction, "create_client", factory)
    monkeypatch.setattr(extraction, "collect_context", collect)
    records = extraction.extract_repositories("token", [candidate(i) for i in range(1, 5)],
                                               {**SOURCE, "input_count": 4}, SETTINGS, workers=2)
    assert next(records)["event"] == "run_started"
    first = next(records)
    assert first["repository_id"] == 1
    assert sorted(started) == [1, 2]
    assert completion[:2] == [2, 1]
    remaining = list(records)
    repositories = [first, *(row for row in remaining if row["record_type"] == "repository")]
    assert [row["repository_id"] for row in repositories] == [1, 2, 3, 4]
    assert remaining[-1]["repository_count"] == 4
    assert remaining[-1]["status"] == "completed"
    assert len(clients) == 2
    assert all(client.closed_by == [coordinator] for client in clients)


@pytest.mark.parametrize("workers", [1, 3])
@pytest.mark.parametrize("close_error", [False, True])
def test_failed_middle_repository_preserves_following_success(monkeypatch, workers, close_error):
    """Mantém grupos seguintes e inclui falhas de fechamento antes do resumo final."""
    clients = []

    def factory(token, gate):
        """Cria clientes rastreáveis para fechamento centralizado."""
        client = Client(close_error)
        clients.append(client)
        return client

    def collect(client, row):
        """Falha somente no candidato intermediário com texto de credencial proibido."""
        if row["repository_id"] == 2:
            raise GithubException(503, {"message": "secret"}, {"Authorization": "secret"})
        return [], []

    monkeypatch.setattr(extraction, "Analyzer", EmptyAnalyzer)
    monkeypatch.setattr(extraction, "create_client", factory)
    monkeypatch.setattr(extraction, "collect_context", collect)
    rows = list(extraction.extract_repositories("token", [candidate(i) for i in range(1, 4)],
                                                SOURCE, SETTINGS, workers=workers))
    repositories = [row for row in rows if row["record_type"] == "repository"]
    assert [row["repository_id"] for row in repositories] == [1, 2, 3]
    assert [row["extraction_status"] for row in repositories] == ["completed", "failed", "completed"]
    assert rows[-1]["error_count"] == 1 + (len(clients) if close_error else 0)
    assert rows[-1]["status"] == "partial"
    assert all(client.closed_by == [get_ident()] for client in clients)
    assert "secret" not in json.dumps(rows)


def test_zero_eligible_has_no_network(monkeypatch):
    """Garante execução vazia completed, mesmo com origem parcial, sem criar cliente."""
    def forbidden(*args, **kwargs):
        """Falha se a execução vazia tentar criar recursos de rede."""
        pytest.fail("Execução vazia não deve criar clientes.")

    monkeypatch.setattr(extraction, "create_client", forbidden)
    rows = list(extraction.extract_repositories("token", [], {**SOURCE, "status": "partial"}, SETTINGS))
    assert len(rows) == 2
    assert rows[0]["input_count"] == 3 and rows[0]["eligible_count"] == 0
    assert rows[-1]["status"] == "completed" and rows[-1]["repository_count"] == 0


@pytest.mark.parametrize("failure", ["input", "preflight", "token", "exists"])
def test_output_sentinel_survives_before_open(tmp_path, monkeypatch, failure):
    """Preserva saída existente diante de entrada/configuração inválida ou recusa explícita."""
    input_path, output_path = tmp_path / "input.jsonl", tmp_path / "output.jsonl"
    save_rows(input_path, triage_rows([]))
    output_path.write_bytes(b"researcher sentinel\r\n")
    monkeypatch.setattr(config, "EXTRACTION_INPUT_PATH", input_path)
    monkeypatch.setattr(config, "EXTRACTION_OUTPUT_PATH", output_path)
    monkeypatch.setenv("GITHUB_TOKEN", "token")

    def settings():
        """Simula preflight inválido sem iniciar subprocessos ou consultas de candidatos."""
        if failure == "preflight":
            raise ValueError("Dependência confiável ausente.")
        return SETTINGS

    def forbidden(*args, **kwargs):
        """Impede início de rede quando a configuração não foi aceita."""
        pytest.fail("Não deveria iniciar extração.")

    monkeypatch.setattr(cli, "preflight", settings)
    monkeypatch.setattr(cli, "extract_repositories", forbidden)
    if failure == "input":
        input_path.write_bytes(b"invalid")
    elif failure == "token":
        monkeypatch.delenv("GITHUB_TOKEN")
    argv = ["extract"] + ([] if failure == "exists" else ["--overwrite"])
    assert cli.main(argv) == 2
    assert output_path.read_bytes() == b"researcher sentinel\r\n"


@pytest.mark.parametrize("test_name", ["double", r"\ud800"])
def test_cli_real_jsonl_and_origin(tmp_path, monkeypatch, test_name):
    """Exerce análise e escrita reais, preservando nomes inclusive com surrogate isolado."""
    try:
        settings = preflight()
    except ValueError as error:
        pytest.skip(str(error))
    texts = [{"path": "a.ts", "content": "/** Dobra a entrada. */\nexport function double(x:number) { return x * 2; }\n",
              "sha256": "unused"},
             {"path": "a.test.ts", "content": "import { test } from 'node:test';\nimport { double } from './a';\ntest('"
              + test_name + "', () => { double(2); });\n",
              "sha256": "unused"}]
    for item in texts:
        item["sha256"] = hashlib.sha256(item["content"].encode()).hexdigest()
    input_path, output_path = tmp_path / "input.jsonl", tmp_path / "output.jsonl"
    save_rows(input_path, triage_rows([candidate()]))
    monkeypatch.setattr(config, "EXTRACTION_INPUT_PATH", input_path)
    monkeypatch.setattr(config, "EXTRACTION_OUTPUT_PATH", output_path)
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    def configured():
        """Retorna o preflight real já concluído para a invocação CLI."""
        return settings

    def factory(token, gate):
        """Cria cliente local sem abrir rede."""
        return Client()

    def collect(client, row):
        """Entrega textos fixos preservados sem executar código candidato."""
        return texts, []

    monkeypatch.setattr(cli, "preflight", configured)
    monkeypatch.setattr(extraction, "create_client", factory)
    monkeypatch.setattr(extraction, "collect_context", collect)
    assert cli.main(["extract", "--workers", "1"]) == 0
    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert [row["record_type"] for row in rows] == ["diagnostic", "repository", "instance", "diagnostic"]
    assert rows[1]["context"]["files"] == texts
    assert rows[2]["repository_id"] == rows[1]["repository_id"] == 1
    assert rows[2]["commit_sha"] == candidate()["commit_sha"]
    assert rows[2]["validation"]["executed_test_cases"] is None
    assert rows[2]["tests"][0]["name"] == json.loads('"' + test_name + '"')
    assert rows[-1]["event"] == "run_finished" and rows[-1]["status"] == "completed"
    assert rows[-1]["instance_count"] == rows[-1]["approved"] == 1


def test_write_failure_closes_generator_pool_and_clients(tmp_path, monkeypatch):
    """Garante limpeza integral quando o consumidor falha durante a publicação."""
    input_path = tmp_path / "input.jsonl"
    save_rows(input_path, triage_rows([candidate(), candidate(2)]))
    clients = []
    analyzers = []

    def factory(token, gate):
        """Registra clientes abertos durante a tentativa de escrita."""
        client = Client()
        clients.append(client)
        return client

    def configured():
        """Fornece configuração confiável simulada antes de abrir a saída."""
        return SETTINGS

    def analyzer_factory(settings):
        """Registra o cancelamento obrigatório do auxiliar compartilhado."""
        analyzer = EmptyAnalyzer(settings)
        analyzers.append(analyzer)
        return analyzer

    def collect(client, row):
        """Fornece snapshot vazio sem conexão com o GitHub."""
        return [], []

    def fail_write(path, records, *, ensure_ascii=False):
        """Interrompe após abrir o pool e publicar o primeiro contexto."""
        for record in records:
            if record["record_type"] == "repository":
                raise OSError("Disco indisponível.")

    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setattr(config, "EXTRACTION_INPUT_PATH", input_path)
    monkeypatch.setattr(config, "EXTRACTION_OUTPUT_PATH", tmp_path / "output.jsonl")
    monkeypatch.setattr(cli, "preflight", configured)
    monkeypatch.setattr(cli, "write_jsonl", fail_write)
    monkeypatch.setattr(extraction, "Analyzer", analyzer_factory)
    monkeypatch.setattr(extraction, "create_client", factory)
    monkeypatch.setattr(extraction, "collect_context", collect)
    assert cli.main(["extract", "--workers", "2"]) == 1
    assert clients and all(client.closed_by == [get_ident()] for client in clients)
    assert analyzers[0].cancelled


def test_interrupt_cancels_analyzer_before_pool_wait(monkeypatch):
    """Interrompe result em voo e exige cancelamento do auxiliar antes de shutdown esperar."""
    entered, cancelled = Event(), Event()
    order, clients = [], []
    interruption = KeyboardInterrupt()
    executor = ThreadPoolExecutor(max_workers=1)
    original_submit, original_shutdown = executor.submit, executor.shutdown

    class BlockingAnalyzer(EmptyAnalyzer):
        """Simula auxiliar em voo cujo encerramento depende do callback do pool."""

        def analyze(self, files, issues):
            """Sinaliza início e permanece em voo até o cancelamento explícito."""
            entered.set()
            assert cancelled.wait(5)
            return super().analyze(files, issues)

        def cancel(self):
            """Libera o auxiliar e registra cada chamada para detectar duplicações."""
            order.append("cancel")
            cancelled.set()

    def submit(*args, **kwargs):
        """Injeta interrupção no coordenador enquanto a tarefa continua ativa."""
        future = original_submit(*args, **kwargs)

        def interrupt_result(timeout=None):
            """Espera o auxiliar iniciar e interrompe a obtenção do primeiro resultado."""
            assert entered.wait(5)
            raise interruption

        future.result = interrupt_result
        return future

    def shutdown(*args, **kwargs):
        """Verifica a ordem sem medir duração e recolhe o executor mesmo em falha."""
        try:
            assert cancelled.is_set()
            order.append("shutdown")
        finally:
            cancelled.set()
            original_shutdown(*args, **kwargs)

    def executor_factory(**kwargs):
        """Entrega executor real instrumentado para verificar a ordem de fechamento."""
        return executor

    def factory(token, gate):
        """Registra o cliente aberto pelo worker que será interrompido."""
        client = Client()
        clients.append(client)
        return client

    def collect(client, row):
        """Entrega contexto vazio sem bloquear em rede."""
        return [], []

    monkeypatch.setattr(executor, "submit", submit)
    monkeypatch.setattr(executor, "shutdown", shutdown)
    monkeypatch.setattr(concurrency, "ThreadPoolExecutor", executor_factory)
    monkeypatch.setattr(extraction, "Analyzer", BlockingAnalyzer)
    monkeypatch.setattr(extraction, "create_client", factory)
    monkeypatch.setattr(extraction, "collect_context", collect)
    records = extraction.extract_repositories("token", [candidate()], SOURCE, SETTINGS, workers=1)
    try:
        assert next(records)["event"] == "run_started"
        with pytest.raises(KeyboardInterrupt) as caught:
            next(records)
        assert caught.value is interruption
        assert order == ["cancel", "shutdown"]
        assert clients and all(client.closed_by == [get_ident()] for client in clients)
    finally:
        cancelled.set()
        records.close()
        original_shutdown(wait=True)
