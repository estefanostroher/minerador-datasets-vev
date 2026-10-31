"""Verifica ordem, isolamento e preservação dos resultados na CLI concorrente."""

import json
from threading import Event, Lock, get_ident

import pytest
from requests.exceptions import ConnectionError

from robo_minerador import cli, config, triage
from test_triage import candidate, discovery_rows, evaluation, save_rows


SOURCE = {"path": "input", "sha256": "hash", "status": "completed"}


class Client:
    """Cliente simulado que registra identidade e fechamento sem acessar a rede."""

    def __init__(self, close_error=None):
        """Captura a thread criadora e uma eventual falha tardia."""
        self.thread = get_ident()
        self.closed = False
        self.close_error = close_error

    def __enter__(self):
        """Disponibiliza o cliente principal da descoberta."""
        return self

    def __exit__(self, *args):
        """Fecha o cliente principal ao sair do contexto."""
        self.close()

    def close(self):
        """Registra a liberação mesmo quando o transporte falha ao fechar."""
        self.closed = True
        if self.close_error:
            raise self.close_error


@pytest.mark.parametrize("command", ["discover", "triage"])
@pytest.mark.parametrize("value", ["0", "-1", "17", "abc"])
def test_invalid_workers_preserve_outputs(tmp_path, monkeypatch, command, value):
    """Rejeita workers inválidos antes de rede ou sobrescrita, nas duas etapas."""
    paths = [tmp_path / "discovery.jsonl", tmp_path / "triage.jsonl"]
    for path in paths:
        path.write_bytes(b"sentinel\n")
    monkeypatch.setattr(config, "OUTPUT_PATH", paths[0])
    monkeypatch.setattr(config, "TRIAGE_OUTPUT_PATH", paths[1])
    monkeypatch.setenv("GITHUB_TOKEN", "fixture")

    def forbidden(*args, **kwargs):
        """Detecta acesso à rede ou à entrada antes da validação dos workers."""
        pytest.fail("Validação deve anteceder qualquer integração.")

    monkeypatch.setattr(cli, "create_client", forbidden)
    monkeypatch.setattr(cli, "load_discovery", forbidden)
    try:
        result = cli.main([command, "--workers", value, "--overwrite"])
    except SystemExit as error:
        result = error.code
    assert result == 2
    assert [path.read_bytes() for path in paths] == [b"sentinel\n"] * 2


def test_workers_validation_and_parser_defaults():
    """Mantém padrão quatro, limite dezesseis e rejeição explícita de booleanos."""
    for value in [True, False, 0, -1, 17, 1.5, "4"]:
        with pytest.raises(ValueError):
            config.validate_workers(value)
    assert config.validate_workers(16) == 16
    assert config.DEFAULT_WORKERS == 4
    assert config.MAX_WORKERS == 16
    for command in ["discover", "triage"]:
        assert cli._parse_arguments([command]).workers == 4
        assert cli._parse_arguments([command, "--workers", "1"]).workers == 1


def test_triage_overlap_order_and_client_reuse(monkeypatch):
    """Força conclusão invertida preservando IDs, SHAs, critérios e cliente por thread."""
    second_finished = Event()
    first_started = Event()
    clients, completed = [], []
    lock = Lock()

    def factory(token, *, gate):
        """Cria clientes reais do pool com o mesmo controle de pausa."""
        client = Client()
        with lock:
            clients.append((client, gate))
        return client

    def snapshot(client, row):
        """Impõe sobreposição observável, sem comparar durações de execução."""
        assert client.thread == get_ident()
        if row["repository_id"] == 1:
            first_started.set()
            assert second_finished.wait(5)
        if row["repository_id"] == 2:
            assert first_started.wait(5)
        with lock:
            completed.append(row["repository_id"])
        if row["repository_id"] == 2:
            second_finished.set()
        return row

    def evaluate(row):
        """Distingue rejeição e aprovação para verificar agregação científica."""
        return evaluation({"access": "rejected"}) if row["repository_id"] == 2 else evaluation()

    monkeypatch.setattr(cli, "create_client", factory)
    monkeypatch.setattr(triage, "Snapshot", snapshot)
    monkeypatch.setattr(triage, "evaluate_snapshot", evaluate)
    candidates = [candidate(i) for i in range(1, 6)]
    records = list(cli._triage_records("fixture", candidates, SOURCE, workers=2))
    rows = [row for row in records if row["record_type"] == "repository"]
    assert completed.index(2) < completed.index(1)
    assert [row["repository_id"] for row in rows] == list(range(1, 6))
    assert [row["commit_sha"] for row in rows] == [row["commit_sha"] for row in candidates]
    assert all(set(row["criteria"]) == set(triage.CRITERION_NAMES) for row in rows)
    assert records[0]["workers"] == 2
    assert records[-1]["approved"] == 4
    assert records[-1]["rejected"] == 1
    assert records[-1]["error_count"] == 0
    assert len(clients) == 2
    assert clients[0][1] is clients[1][1]
    assert all(client.closed for client, gate in clients)
    sequential = list(triage.screen_repositories(Client(), candidates, SOURCE))
    expected_rows = [row for row in sequential if row["record_type"] == "repository"]
    assert [{key: value for key, value in row.items() if key != "screened_at"} for row in rows] == [
        {key: value for key, value in row.items() if key != "screened_at"} for row in expected_rows
    ]
    assert {key: value for key, value in records[-1].items() if key != "observed_at"} == {
        key: value for key, value in sequential[-1].items() if key != "observed_at"
    }


def test_missing_sha_never_creates_client(monkeypatch):
    """Candidatos sem SHA permanecem pendentes sem abrir conexões."""
    def forbidden(*args, **kwargs):
        """Detecta abertura indevida de cliente para SHA ausente."""
        pytest.fail("SHA ausente não deve criar cliente.")

    monkeypatch.setattr(cli, "create_client", forbidden)
    item = candidate()
    item.update(commit_sha=None, sha_status="unavailable")
    records = list(cli._triage_records("fixture", [item], SOURCE, workers=4))
    assert records[1]["decision"] == "not_evaluated"
    assert records[-1]["status"] == "failed"
    assert records[-1]["error_count"] == 0


@pytest.mark.parametrize("failure", ["setup", "task", "programming", "close"])
def test_triage_failures_are_local_and_clients_close(monkeypatch, failure):
    """Preserva candidatos após falha esperada e propaga defeitos com limpeza do pool."""
    clients = []
    second_started = Event()
    first_started = Event()
    factory_lock = Lock()
    calls = 0

    def factory(token, *, gate):
        """Falha na abertura da segunda thread sem reiniciar a execução inteira."""
        nonlocal calls
        with factory_lock:
            calls += 1
            index = calls
        if index == 2 and failure == "setup":
            assert first_started.wait(5)
            second_started.set()
            raise ConnectionError("secret")
        client = Client(ConnectionError("secret") if failure == "close" else None)
        clients.append(client)
        return client

    def snapshot(client, row):
        """Garante duas tarefas em voo e injeta falha associada ao segundo ID."""
        if row["repository_id"] == 1:
            first_started.set()
            assert second_started.wait(5)
        elif row["repository_id"] == 2:
            second_started.set()
            if failure == "task":
                raise ConnectionError("secret")
            if failure == "programming":
                raise RuntimeError("Defeito interno.")
        return object()

    def evaluate(snapshot):
        """Aprova snapshots recuperados para distinguir pendência operacional."""
        return evaluation()

    monkeypatch.setattr(cli, "create_client", factory)
    monkeypatch.setattr(triage, "Snapshot", snapshot)
    monkeypatch.setattr(triage, "evaluate_snapshot", evaluate)

    class Candidates(list):
        """Garante primeira abertura concluída antes de fornecer o segundo candidato."""

        def __iter__(self):
            """Sincroniza a alimentação sem depender da ordem de escalonamento das threads."""
            for index, row in enumerate(super().__iter__()):
                if index == 1:
                    assert first_started.wait(5)
                yield row

    candidates = Candidates(candidate(i) for i in range(1, 4))
    stream = cli._triage_records("fixture", candidates, SOURCE, workers=2)
    if failure == "programming":
        with pytest.raises(RuntimeError, match="Defeito interno"):
            list(stream)
    else:
        records = list(stream)
        rows = [row for row in records if row["record_type"] == "repository"]
        assert [row["repository_id"] for row in rows] == [1, 2, 3]
        assert rows[0]["decision"] == "approved"
        assert rows[1]["decision"] == ("approved" if failure == "close" else "not_evaluated")
        assert rows[2]["decision"] == "approved"
        assert sum(row.get("event") == "run_started" for row in records) == 1
        assert sum(row.get("event") == "run_finished" for row in records) == 1
        assert records[-1]["status"] == "partial"
        assert records[-1]["error_count"] == (len(clients) if failure == "close" else 1)
        errors = [row for row in records if row.get("event") == "repository_error"]
        assert [row["repository_id"] for row in errors] == (
            [None] * len(clients) if failure == "close" else [2]
        )
        assert "secret" not in json.dumps(records)
    assert clients and all(client.closed for client in clients)


def test_generator_close_releases_worker_clients(monkeypatch):
    """Abandonar a escrita após um candidato fecha todos os clientes já abertos."""
    clients = []

    def factory(token, *, gate):
        """Registra recursos que devem ser liberados pelo fechamento do gerador."""
        client = Client()
        clients.append(client)
        return client

    def snapshot(client, row):
        """Fornece snapshots sem rede para testar o abandono do consumidor."""
        return object()

    def evaluate(snapshot):
        """Fornece critérios completos para publicar o primeiro resultado."""
        return evaluation()

    monkeypatch.setattr(cli, "create_client", factory)
    monkeypatch.setattr(triage, "Snapshot", snapshot)
    monkeypatch.setattr(triage, "evaluate_snapshot", evaluate)
    stream = cli._triage_records("fixture", [candidate(i) for i in range(1, 8)], SOURCE, workers=2)
    assert next(stream)["event"] == "run_started"
    assert next(stream)["repository_id"] == 1
    stream.close()
    assert clients and all(client.closed for client in clients)


def test_screen_callback_requires_one_result_per_candidate():
    """Impede publicar contagem completa quando o callback omite um candidato."""
    def incomplete(rows):
        """Simula contrato quebrado sem fornecer resultados para o candidato."""
        return iter(())

    with pytest.raises(ValueError):
        list(triage.screen_repositories(None, [candidate()], SOURCE,
                                       screen_many=incomplete, workers=2))


def test_failed_run_keeps_failed_status_after_close_error(monkeypatch):
    """Não promove execução sem critérios avaliados a parcial após falha no fechamento."""
    def factory(token, *, gate):
        """Abre cliente cujo fechamento também falha operacionalmente."""
        return Client(ConnectionError("secret"))

    def snapshot(client, row):
        """Simula indisponibilidade de todos os snapshots consultados."""
        raise ConnectionError("secret")

    monkeypatch.setattr(cli, "create_client", factory)
    monkeypatch.setattr(triage, "Snapshot", snapshot)
    records = list(cli._triage_records("fixture", [candidate()], SOURCE, workers=2))
    assert records[0]["workers"] == 2
    assert records[-1]["status"] == "failed"
    assert records[-1]["not_evaluated"] == 1
    assert records[-1]["error_count"] == 2
    assert records[-2]["repository_id"] is None


@pytest.mark.parametrize("command", ["discover", "triage"])
def test_cli_sequential_uses_one_argument_factory(tmp_path, monkeypatch, command):
    """Mantém o caminho sequencial compatível sem pool ou parâmetro de pausa."""
    input_path, output_path = tmp_path / "input.jsonl", tmp_path / "output.jsonl"
    save_rows(input_path, discovery_rows([]))
    monkeypatch.setenv("GITHUB_TOKEN", "fixture")
    monkeypatch.setattr(config, "TRIAGE_INPUT_PATH", input_path)
    monkeypatch.setattr(config, "TRIAGE_OUTPUT_PATH", output_path)
    monkeypatch.setattr(config, "OUTPUT_PATH", output_path)
    clients = []

    def factory(token):
        """Aceita somente o contrato original do cliente sequencial."""
        client = Client()
        clients.append(client)
        return client

    def collect(client, start, end, limit):
        """Substitui apenas rede da descoberta mantendo o metadado padrão sequencial."""
        return [{"event": "run_started", "workers": 1},
                {"event": "run_finished", "repository_count": 0, "status": "completed"}]

    def forbidden(*args, **kwargs):
        """Detecta criação indevida de pool no modo sequencial."""
        pytest.fail("Workers 1 não deve criar pool.")

    monkeypatch.setattr(cli, "create_client", factory)
    monkeypatch.setattr(cli, "collect_repositories", collect)
    monkeypatch.setattr(cli, "WorkerPool", forbidden)
    assert cli.main([command, "--workers", "1"]) == 0
    records = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["workers"] == 1
    assert len(clients) == 1 and clients[0].closed


@pytest.mark.parametrize("command", ["discover", "triage"])
def test_cli_dispatches_parallel_clients_and_late_close(tmp_path, monkeypatch, command):
    """Exerce despacho real, pausa compartilhada e diagnóstico tardio antes do fim."""
    input_path, output_path = tmp_path / "input.jsonl", tmp_path / "output.jsonl"
    save_rows(input_path, discovery_rows([candidate(), candidate(2)]))
    monkeypatch.setenv("GITHUB_TOKEN", "fixture")
    monkeypatch.setattr(config, "TRIAGE_INPUT_PATH", input_path)
    monkeypatch.setattr(config, "TRIAGE_OUTPUT_PATH", output_path)
    monkeypatch.setattr(config, "OUTPUT_PATH", output_path)
    clients = []
    both_running = Event()
    first_running = Event()

    def factory(token, *, gate):
        """Registra clientes principais e de worker sem aceitar chamadas sequenciais."""
        client = Client(ConnectionError("secret"))
        clients.append((client, gate))
        return client

    def synchronize(client, row):
        """Força clientes distintos em duas threads e preserva identidade do candidato."""
        assert client.thread == get_ident()
        if row["repository_id"] == 1:
            first_running.set()
            assert both_running.wait(5)
        else:
            assert first_running.wait(5)
            both_running.set()
        return row

    def collect(client, start, end, limit, *, resolve_many, workers):
        """Exerce o callback de descoberta mantendo a busca no coordenador."""
        assert client.thread == get_ident()
        rows = [candidate(), candidate(2)]
        for row, (sha, error) in zip(rows, resolve_many(rows), strict=True):
            assert error is None
            assert sha == row["commit_sha"]
        return [{"record_type": "diagnostic", "event": "run_started", "workers": workers},
                *rows, {"record_type": "diagnostic", "event": "run_finished",
                        "repository_count": 2, "status": "completed"}]

    def resolve(client, row):
        """Resolve o SHA simulado no worker sem alterar o registro científico."""
        return synchronize(client, row)["commit_sha"], None

    def evaluate(snapshot):
        """Fornece avaliação completa para verificar diagnóstico de fechamento."""
        return evaluation()

    monkeypatch.setattr(cli, "create_client", factory)
    monkeypatch.setattr(cli, "collect_repositories", collect)
    monkeypatch.setattr(cli, "resolve_repository_sha", resolve)
    monkeypatch.setattr(triage, "Snapshot", synchronize)
    monkeypatch.setattr(triage, "evaluate_snapshot", evaluate)
    assert cli.main([command]) == 1
    records = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["workers"] == 4
    assert records[-1]["status"] == "partial"
    assert records[-1]["event"] == "run_finished"
    assert records[-1]["repository_count"] == 2
    expected_event = "api_error" if command == "discover" else "repository_error"
    assert sum(row.get("event") == expected_event for row in records) == len(clients)
    assert len(clients) == (3 if command == "discover" else 2)
    assert len({id(gate) for client, gate in clients}) == 1
    assert all(client.closed for client, gate in clients)
    assert "secret" not in json.dumps(records)
