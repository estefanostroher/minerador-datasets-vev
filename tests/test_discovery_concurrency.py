"""Verifica seleção e integridade dos SHAs sob resolução concorrente injetada."""

from datetime import datetime, timedelta, timezone
from threading import Event, get_ident
from types import SimpleNamespace

from github import GithubException
import pytest

from robo_minerador import discovery
from robo_minerador.concurrency import WorkerPool
from robo_minerador.search import build_query


START = datetime(2025, 1, 1, tzinfo=timezone.utc)


def repository(repository_id):
    """Monta um item de busca local com identidade e branch explícitas."""
    return {
        "id": repository_id, "html_url": f"https://github.com/fixture/{repository_id}",
        "full_name": f"fixture/{repository_id}", "created_at": "2025-01-01T00:00:00Z",
        "stargazers_count": 0, "default_branch": "main",
    }


def page(ids, *, total=None, incomplete=False):
    """Monta uma página que pode forçar paginação ou fallback sem rede."""
    return {
        "items": [repository(value) for value in ids],
        "total_count": len(ids) if total is None else total,
        "incomplete_results": incomplete,
    }


@pytest.fixture
def network(monkeypatch):
    """Isola a busca e exige clientes exclusivos de cada worker na resolução."""
    state = SimpleNamespace(client=object(), pages={}, searches=[], calls=[], clients=[],
                            errors={}, operation=None)

    def fetch(client, query, number):
        """Entrega páginas previstas apenas ao coordenador da descoberta."""
        assert client is state.client
        state.searches.append((query, number))
        return state.pages[query, number]

    def factory():
        """Cria um cliente fictício vinculado à thread e registra seu fechamento."""
        client = SimpleNamespace(owner=get_ident(), closed=False)

        def close():
            """Registra o fechamento efetuado pelo pool real."""
            client.closed = True

        client.close = close
        state.clients.append(client)
        return client

    def resolve(client, name, branch):
        """Simula respostas por ID e detecta compartilhamento indevido do cliente."""
        assert client.owner == get_ident()
        assert branch == "main"
        repository_id = int(name.rsplit("/", 1)[1])
        state.calls.append(repository_id)
        if state.operation is not None:
            state.operation(repository_id)
        if repository_id in state.errors:
            raise state.errors[repository_id]
        return f"{repository_id:040x}"

    state.factory = factory
    monkeypatch.setattr(discovery, "fetch_search_page", fetch)
    monkeypatch.setattr(discovery, "resolve_commit_sha", resolve)
    return state


def resolver_for(pool, batches):
    """Adapta o pool real ao contrato de lotes ordenados esperado pela CLI."""
    def resolve(record):
        """Obtém o cliente no try e captura somente falhas operacionais esperadas."""
        try:
            return discovery.resolve_repository_sha(pool.get_client(), record)
        except discovery.EXPECTED_API_ERRORS as error:
            return None, error

    def resolve_many(records):
        """Registra os IDs selecionados e devolve os resultados na ordem de entrada."""
        assert isinstance(records, list)
        batches.append([record["repository_id"] for record in records])
        return pool.map_ordered(resolve, records)

    return resolve_many


def test_overlap_preserves_page_order_and_limit(network, monkeypatch):
    """Prova sobreposição sem relógio e impede paginação antes de concluir os SHAs."""
    first_started, second_finished = Event(), Event()
    completed = []
    fetch = discovery.fetch_search_page

    def fetch_after_resolution(client, query, number):
        """Impede buscar a próxima página antes de terminar todas as resoluções atuais."""
        if number == 2:
            assert completed == [2, 1]
        return fetch(client, query, number)

    monkeypatch.setattr(discovery, "fetch_search_page", fetch_after_resolution)

    def operation(repository_id):
        """Força o segundo SHA a terminar antes do primeiro usando eventos."""
        if repository_id == 1:
            first_started.set()
            assert second_finished.wait(5)
        elif repository_id == 2:
            assert first_started.wait(5)
            completed.append(2)
            second_finished.set()
            return
        else:
            assert completed == [2, 1]
        completed.append(repository_id)

    network.operation = operation
    query = build_query(START, START + timedelta(seconds=1))
    network.pages[query, 1] = page([1, 1, 2], total=200)
    network.pages[query, 2] = page([2, 3, 4], total=200)
    batches = []
    with WorkerPool(2, network.factory) as pool:
        records = discovery.collect_repositories(
            network.client, START, START + timedelta(seconds=1), 3,
            resolve_many=resolver_for(pool, batches), workers=2,
        )

    candidates = [row for row in records if row["record_type"] == "repository"]
    assert completed == [2, 1, 3]
    assert batches == [[1, 2], [3]]
    assert sorted(network.calls) == [1, 2, 3]
    assert [row["repository_id"] for row in candidates] == [1, 2, 3]
    assert [row["commit_sha"] for row in candidates] == [f"{i:040x}" for i in (1, 2, 3)]
    assert network.searches == [(query, 1), (query, 2)]
    assert records[0]["workers"] == 2
    assert records[-1]["stop_reason"] == "limit_reached"
    assert len(network.clients) == 2
    assert all(client.closed for client in network.clients)


def test_fallback_retains_first_sha_and_merges_duplicate_sources(network):
    """Impede nova resolução de duplicatas locais ou das consultas de fallback."""
    end = START + timedelta(seconds=4)
    root = build_query(START, end)
    newer = build_query(START + timedelta(seconds=2), end)
    older = build_query(START, START + timedelta(seconds=2))
    network.pages.update({
        (root, 1): page([1, 1], incomplete=True),
        (newer, 1): page([1, 2]),
        (older, 1): page([1, 2, 3, 4]),
    })
    batches = []
    with WorkerPool(2, network.factory) as pool:
        records = discovery.collect_repositories(
            network.client, START, end, 3,
            resolve_many=resolver_for(pool, batches), workers=2,
        )

    candidates = [row for row in records if row["record_type"] == "repository"]
    assert batches == [[1], [2], [3]]
    assert network.calls == [1, 2, 3]
    assert [row["commit_sha"] for row in candidates] == [f"{i:040x}" for i in (1, 2, 3)]
    assert candidates[0]["search_queries"] == [root, newer, older]
    assert candidates[1]["search_queries"] == [newer, older]
    assert network.searches == [(root, 1), (newer, 1), (older, 1)]
    assert [row["query"] for row in records if row.get("event") == "incomplete_results"] == [root]
    assert records[-1]["status"] == "partial"


def test_expected_sha_failure_keeps_record_and_next_success(network):
    """Preserva candidato sem SHA e intercala seu diagnóstico antes do próximo ID."""
    end = START + timedelta(seconds=1)
    query = build_query(START, end)
    network.pages[query, 1] = page([1, 2])
    network.errors[1] = GithubException(status=503)
    with WorkerPool(2, network.factory) as pool:
        records = discovery.collect_repositories(
            network.client, START, end, 2,
            resolve_many=resolver_for(pool, []), workers=2,
        )

    assert [row.get("event", row.get("repository_id")) for row in records] == [
        "run_started", 1, "sha_unavailable", 2, "run_finished",
    ]
    assert records[1]["commit_sha"] is None
    assert records[1]["sha_status"] == "unavailable"
    assert records[2]["repository_id"] == 1
    assert records[2]["http_status"] == 503
    assert records[3]["commit_sha"] == f"{2:040x}"
    assert records[3]["sha_status"] == "resolved"
    assert records[-1]["repository_count"] == 2
    assert records[-1]["status"] == "partial"


@pytest.mark.parametrize("source", ["worker", "callback"])
def test_programming_error_propagates(network, source):
    """Não converte defeitos do resolvedor ou callback em ausência de SHA."""
    end = START + timedelta(seconds=1)
    network.pages[build_query(START, end), 1] = page([1])
    network.errors[1] = RuntimeError("synthetic programming error")

    def broken_callback(records):
        """Simula um defeito interno antes de produzir o iterável de resultados."""
        raise RuntimeError("synthetic programming error")

    with WorkerPool(2, network.factory) as pool:
        callback = resolver_for(pool, []) if source == "worker" else broken_callback
        with pytest.raises(RuntimeError, match="synthetic programming error"):
            discovery.collect_repositories(
                network.client, START, end, 1, resolve_many=callback, workers=2,
            )


@pytest.mark.parametrize("results", [[], [("sha", None), ("extra", None)], [("sha",)]])
def test_malformed_results_cannot_silently_drop_or_misassociate_records(network, results):
    """Rejeita naturalmente contagem ou pares inválidos em vez de perder integridade."""
    end = START + timedelta(seconds=1)
    network.pages[build_query(START, end), 1] = page([1])

    def resolve_many(records):
        """Entrega um lote inválido para verificar o contrato de associação por ordem."""
        return iter(results)

    with pytest.raises(ValueError):
        discovery.collect_repositories(
            network.client, START, end, 1, resolve_many=resolve_many, workers=2,
        )


def test_failed_run_records_preserve_worker_metadata():
    """Mantém a configuração de execução mesmo quando a conexão inicial falha."""
    end = START + timedelta(seconds=1)
    error = GithubException(status=503)
    assert discovery.failed_run_records(START, end, 3, error)[0]["workers"] == 1
    records = discovery.failed_run_records(START, end, 3, error, workers=4)
    assert records[0]["workers"] == 4
    assert records[-1]["status"] == "failed"
