"""Verifica os riscos de perda, duplicação e falsa completude da descoberta."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from github import GithubException
import pytest

from robo_minerador import discovery, github_api


START = datetime(2025, 1, 1, tzinfo=timezone.utc)
PREFIX = "language:TypeScript is:public created:2025-01-01T00:00:"
SECOND_QUERY = PREFIX + "00Z..2025-01-01T00:00:00Z"
ROOT_QUERY = PREFIX + "00Z..2025-01-01T00:00:03Z"
NEWER_QUERY = PREFIX + "02Z..2025-01-01T00:00:03Z"
OLDER_QUERY = PREFIX + "00Z..2025-01-01T00:00:01Z"


def repository(repository_id, second=0):
    """Cria um candidato fictício sem licença aprovada e com criação controlada."""
    return {
        "id": repository_id,
        "html_url": f"https://github.com/test-fixture/repo-{repository_id}",
        "full_name": f"test-fixture/repo-{repository_id}",
        "language": "TypeScript",
        "created_at": f"2025-01-01T00:00:{second:02d}Z",
        "license": None,
        "stargazers_count": 0,
        "default_branch": "main",
    }


def page(items, total_count, incomplete=False):
    """Monta uma resposta sintética com os metadados originais de paginação."""
    return {
        "items": items,
        "total_count": total_count,
        "incomplete_results": incomplete,
    }


@pytest.fixture
def network(monkeypatch):
    """Substitui as duas operações de rede por respostas e falhas locais rastreáveis."""
    state = SimpleNamespace(
        client=object(), pages={}, searches=[], shas=[], sha_errors={},
    )

    def fetch_search_page(client, query, page):
        """Entrega a página prevista e rejeita consultas inesperadas sem acessar rede."""
        assert client is state.client
        state.searches.append((query, page))
        result = state.pages[query, page]
        if isinstance(result, Exception):
            raise result
        return result

    def resolve_commit_sha(client, full_name, branch):
        """Registra a resolução e retorna um SHA fictício estável ou a falha prevista."""
        assert client is state.client
        assert branch == "main"
        repository_id = int(full_name.rsplit("-", 1)[1])
        state.shas.append(repository_id)
        if repository_id in state.sha_errors:
            raise state.sha_errors[repository_id]
        return f"{repository_id:040x}"

    monkeypatch.setattr(discovery, "fetch_search_page", fetch_search_page)
    monkeypatch.setattr(discovery, "resolve_commit_sha", resolve_commit_sha)
    return state


def test_pagination_counts_unique_repositories_and_preserves_sha(network):
    """Impede que duplicatas ou diagnósticos consumam o limite e alterem o SHA."""
    network.pages.update({
        (SECOND_QUERY, 1): page([repository(1), repository(2)], 300),
        (SECOND_QUERY, 2): page([repository(2), repository(3)], 300),
        (SECOND_QUERY, 3): page([repository(4)], 300),
    })

    records = discovery.collect_repositories(
        network.client, START, START + timedelta(seconds=1), 3,
    )
    candidates = [row for row in records if row["record_type"] == "repository"]

    assert [row["repository_id"] for row in candidates] == [1, 2, 3]
    assert [row["commit_sha"] for row in candidates] == [f"{i:040x}" for i in (1, 2, 3)]
    assert all(row["search_queries"] == [SECOND_QUERY] for row in candidates)
    assert network.shas == [1, 2, 3]
    assert network.searches == [(SECOND_QUERY, 1), (SECOND_QUERY, 2)]
    assert records[0]["event"] == "run_started"
    assert records[-1]["event"] == "run_finished"
    assert records[-1]["repository_count"] == 3
    assert records[-1]["status"] == "completed"
    assert records[-1]["stop_reason"] == "limit_reached"


@pytest.mark.parametrize("seconds", [4, 1], ids=["divisible", "indivisible"])
def test_direct_high_count_stops_on_first_page(network, seconds):
    """Evita subdivisão e falsa saturação quando a primeira página já atende ao alvo."""
    query = ROOT_QUERY if seconds == 4 else SECOND_QUERY
    network.pages[query, 1] = page([repository(i) for i in range(1, 101)], 12_225_986)

    records = discovery.collect_repositories(
        network.client, START, START + timedelta(seconds=seconds), 5,
    )
    candidates = [row for row in records if row["record_type"] == "repository"]

    assert network.searches == [(query, 1)]
    assert [row["repository_id"] for row in candidates] == list(range(1, 6))
    assert network.shas == list(range(1, 6))
    assert [row["commit_sha"] for row in candidates] == [f"{i:040x}" for i in range(1, 6)]
    assert [row["event"] for row in records if row["record_type"] == "diagnostic"] == [
        "run_started", "run_finished",
    ]
    assert records[0]["strategy"] == "direct_search_then_partition"
    assert records[0]["ordering"] == "updated_desc"
    assert records[0]["upper_timestamp"] == f"2025-01-01T00:00:{seconds - 1:02d}Z"
    assert records[-1]["status"] == "completed"
    assert records[-1]["stop_reason"] == "limit_reached"


@pytest.mark.parametrize("count", [1000, 1001], ids=["stop-at-cap", "fallback-over-cap"])
def test_saturated_interval_preserves_boundaries_and_global_limit(network, count):
    """Exige aproveitar mil IDs antes do fallback, preservando fronteiras, fontes e SHA."""
    items = [repository(i, second=2 if i < 1000 else 1) for i in range(1, 1001)]
    expected_searches = []
    for number in range(1, 11):
        network.pages[ROOT_QUERY, number] = page(items[(number - 1) * 100:number * 100], 12_225_986)
        expected_searches.append((ROOT_QUERY, number))
    network.pages[NEWER_QUERY, 1] = page([repository(1, 2)], 1)
    network.pages[OLDER_QUERY, 1] = page([repository(1000, 1), repository(1001, 0)], 2)
    if count == 1001:
        expected_searches += [(NEWER_QUERY, 1), (OLDER_QUERY, 1)]

    records = discovery.collect_repositories(
        network.client, START, START + timedelta(seconds=4), count,
    )
    candidates = [row for row in records if row["record_type"] == "repository"]

    # As consultas literais exigem fronteiras adjacentes, inclusivas e sem sobreposição.
    assert network.searches == expected_searches
    assert [row["repository_id"] for row in candidates] == list(range(1, count + 1))
    assert candidates[0]["created_at"] == "2025-01-01T00:00:02Z"
    assert candidates[999]["created_at"] == "2025-01-01T00:00:01Z"
    assert candidates[0]["search_queries"] == ([ROOT_QUERY, NEWER_QUERY] if count == 1001 else [ROOT_QUERY])
    assert candidates[999]["search_queries"] == ([ROOT_QUERY, OLDER_QUERY] if count == 1001 else [ROOT_QUERY])
    assert all(row["search_queries"] == [ROOT_QUERY] for row in candidates[1:999])
    if count == 1001:
        assert candidates[1000]["search_queries"] == [OLDER_QUERY]
    assert network.shas == list(range(1, count + 1))
    assert [row["commit_sha"] for row in candidates] == [f"{i:040x}" for i in range(1, count + 1)]
    assert not any(row.get("event") == "search_saturated" for row in records)
    assert records[-1]["repository_count"] == count
    assert records[-1]["status"] == "completed"
    assert records[-1]["stop_reason"] == "limit_reached"


@pytest.mark.parametrize(
    "case,event,reason",
    [
        ("incomplete", "incomplete_results", "search_exhausted"),
        ("saturated", "search_saturated", "search_exhausted"),
        ("sha-failure", "sha_unavailable", "search_exhausted"),
        ("search-failure", "api_error", "api_error"),
    ],
    ids=["incomplete-second", "saturated-second", "sha-failure", "later-search-failure"],
)
def test_operational_limitations_preserve_candidates(network, case, event, reason):
    """Evita perda de candidatos e falsa completude diante de limitações operacionais."""
    total = 1001 if case == "saturated" else 200 if case in {"incomplete", "search-failure"} else 1
    network.pages[SECOND_QUERY, 1] = page([repository(1)], total, case == "incomplete")
    expected_searches = [(SECOND_QUERY, 1)]
    if case == "saturated":
        # Dez páginas completas não autorizam consultar a página onze.
        for number in range(1, 11):
            items = [repository(i) for i in range((number - 1) * 100 + 1, number * 100 + 1)]
            network.pages[SECOND_QUERY, number] = page(items, total)
            if number > 1:
                expected_searches.append((SECOND_QUERY, number))
    elif case == "incomplete":
        network.pages[SECOND_QUERY, 2] = page([repository(1)], total)
        expected_searches.append((SECOND_QUERY, 2))
    elif case == "sha-failure":
        network.sha_errors[1] = GithubException(status=503)
    elif case == "search-failure":
        network.pages[SECOND_QUERY, 2] = GithubException(status=503)
        expected_searches.append((SECOND_QUERY, 2))

    records = discovery.collect_repositories(
        network.client, START, START + timedelta(seconds=1), 1001,
    )
    candidates = [row for row in records if row["record_type"] == "repository"]
    diagnostics = [row for row in records if row.get("event") == event]
    expected_ids = list(range(1, 1001)) if case == "saturated" else [1]

    assert [row["repository_id"] for row in candidates] == expected_ids
    assert network.shas == expected_ids
    assert network.searches == expected_searches
    assert len(diagnostics) == 1
    assert diagnostics[0]["record_type"] == "diagnostic"
    assert diagnostics[0]["query"] == SECOND_QUERY
    assert records[0]["event"] == "run_started"
    assert records[-1]["event"] == "run_finished"
    assert records[-1]["repository_count"] == len(expected_ids)
    assert records[-1]["status"] == "partial"
    assert records[-1]["stop_reason"] == reason
    assert all(row["license_reported"] is None for row in candidates)
    if case == "sha-failure":
        assert candidates[0]["commit_sha"] is None
        assert candidates[0]["sha_status"] == "unavailable"
        assert diagnostics[0]["repository_id"] == 1
    else:
        assert candidates[0]["commit_sha"] == f"{1:040x}"
        assert candidates[0]["sha_status"] == "resolved"
    if case in {"sha-failure", "search-failure"}:
        assert diagnostics[0]["http_status"] == 503
        # Uma falha de programação no mesmo ponto deve escapar, nunca virar diagnóstico.
        if case == "sha-failure":
            network.sha_errors[1] = RuntimeError("synthetic programming error")
        else:
            network.pages[SECOND_QUERY, 2] = RuntimeError("synthetic programming error")
        with pytest.raises(RuntimeError, match="synthetic programming error"):
            discovery.collect_repositories(
                network.client, START, START + timedelta(seconds=1), 1001,
            )


@pytest.mark.parametrize("seconds,incomplete_page,limit", [
    (4, 1, 1), (4, 1, 4), (4, 2, 2), (4, 2, 4), (1, 1, 1), (1, 2, 4),
], ids=["first-limit", "first-fallback", "later-limit", "later-fallback",
        "indivisible-first-limit", "indivisible-later-paging"])
def test_incomplete_page_preserves_sha_and_sources(network, seconds, incomplete_page, limit):
    """Preserva páginas incompletas e só subdivide quando ainda faltam candidatos."""
    query = ROOT_QUERY if seconds == 4 else SECOND_QUERY
    network.pages.update({
        (query, 1): page([repository(1, 2 if seconds == 4 else 0)], 200,
                         incomplete=incomplete_page == 1),
        (query, 2): page([repository(2, 1 if seconds == 4 else 0)], 200,
                         incomplete=incomplete_page == 2),
        (NEWER_QUERY, 1): page([repository(1, 2), repository(3, 3)], 2),
        (OLDER_QUERY, 1): page([repository(2, 1), repository(4, 0)], 2),
    })

    records = discovery.collect_repositories(
        network.client, START, START + timedelta(seconds=seconds), limit,
    )
    candidates = [row for row in records if row["record_type"] == "repository"]
    expected_searches = [(query, 1)]
    expected_ids = [1]
    if incomplete_page == 2:
        expected_searches.append((query, 2))
        expected_ids.append(2)
    fallback = seconds == 4 and limit == 4
    if fallback:
        expected_searches += [(NEWER_QUERY, 1), (OLDER_QUERY, 1)]
        expected_ids = [1, 3, 2, 4] if incomplete_page == 1 else [1, 2, 3, 4]

    assert network.searches == expected_searches
    assert [row["repository_id"] for row in candidates] == expected_ids
    assert network.shas == expected_ids
    assert [row["commit_sha"] for row in candidates] == [f"{i:040x}" for i in expected_ids]
    sources = {row["repository_id"]: row["search_queries"] for row in candidates}
    assert sources[1] == ([query, NEWER_QUERY] if fallback else [query])
    if fallback:
        assert sources[2] == ([query, OLDER_QUERY] if incomplete_page == 2 else [OLDER_QUERY])
        assert sources[3] == [NEWER_QUERY]
        assert sources[4] == [OLDER_QUERY]
    elif incomplete_page == 2:
        assert sources[2] == [query]
    assert [row["query"] for row in records if row.get("event") == "incomplete_results"] == [query]
    assert records[-1]["event"] == "run_finished"
    assert records[-1]["repository_count"] == len(expected_ids)
    assert records[-1]["status"] == "partial"
    assert records[-1]["stop_reason"] == ("limit_reached" if len(expected_ids) == limit else "search_exhausted")


def test_search_api_orders_by_updated_and_preserves_payload():
    """Confere a seleção e ordenação reais da busca sem perder o corpo original."""
    payload = page([repository(1)], 12_225_986, incomplete=True)
    payload["additional_metadata"] = "preserved"
    calls = []

    def request_json_and_check(method, path, parameters):
        """Registra os argumentos do requester e entrega o corpo original sem rede."""
        calls.append((method, path, parameters))
        return {}, payload

    client = SimpleNamespace(requester=SimpleNamespace(requestJsonAndCheck=request_json_and_check))

    assert github_api.fetch_search_page(client, ROOT_QUERY, 3) is payload
    assert calls == [("GET", "/search/repositories", {
        "q": ROOT_QUERY, "per_page": 100, "page": 3, "sort": "updated", "order": "desc",
    })]
