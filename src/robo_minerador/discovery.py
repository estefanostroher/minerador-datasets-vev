"""Coleta de candidatos e diagnósticos da descoberta, sem persistência."""

from datetime import datetime, timedelta, timezone

from github import GithubException
from requests.exceptions import RequestException
from urllib3.exceptions import HTTPError

from .github_api import fetch_search_page, resolve_commit_sha
from .search import build_query, split_interval

EXPECTED_API_ERRORS = (GithubException, RequestException, HTTPError)
RATE_HEADERS = {
    "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset",
    "x-ratelimit-used", "retry-after",
}


def _utc_timestamp(value: datetime) -> str:
    """Formata um instante em UTC com precisão de segundos."""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _diagnostic(event: str, message: str, **fields) -> dict:
    """Monta um diagnóstico com contexto explícito e instante de observação."""
    return {
        "record_type": "diagnostic",
        "stage": "discovery",
        "event": event,
        "query": None,
        "repository_id": None,
        "message": message,
        "observed_at": _utc_timestamp(datetime.now(timezone.utc)),
        **fields,
    }


def _error_context(error: BaseException) -> dict:
    """Extrai somente status HTTP e cabeçalhos numéricos permitidos da falha."""
    response = getattr(error, "response", None)
    status = getattr(error, "status", None)
    if status is None and response is not None:
        status = response.status_code
    headers = getattr(error, "headers", None)
    if headers is None and response is not None:
        headers = response.headers
    rate_headers = {}
    for name, value in (headers or {}).items():
        name = name.lower()
        if name in RATE_HEADERS and isinstance(value, (str, int)):
            text = str(value)
            if text.isascii() and text.isdecimal():
                rate_headers[name] = text
    return {
        "http_status": status if type(status) is int and 100 <= status <= 599 else None,
        "rate_limit_headers": rate_headers,
    }


def _run_started(start: datetime, end: datetime, limit: int) -> dict:
    """Registra os parâmetros e o limite superior inclusivo congelado da execução."""
    return _diagnostic(
        "run_started", "Descoberta iniciada.",
        min_repository_date=start.date().isoformat(),
        max_repositories=limit,
        upper_timestamp=_utc_timestamp(end - timedelta(seconds=1)),
        strategy="direct_search_then_partition", ordering="updated_desc",
    )


def _run_finished(records: list[dict], count: int, reason: str) -> dict:
    """Determina o estado final a partir dos diagnósticos e candidatos preservados."""
    limited = any(
        record.get("event") in {
            "incomplete_results", "search_saturated", "api_error", "sha_unavailable",
        }
        for record in records
    )
    status = "partial" if limited else "completed"
    if reason == "api_error" and count == 0:
        status = "failed"
    return _diagnostic(
        "run_finished", "Descoberta encerrada.",
        repository_count=count, status=status, stop_reason=reason,
    )


def failed_run_records(
    start: datetime, end: datetime, limit: int, error: BaseException,
) -> list[dict]:
    """Preserva metadados quando a integração falha antes de iniciar a coleta."""
    records = [
        _run_started(start, end, limit),
        _diagnostic("api_error", "Falha na conexão com a API do GitHub.",
                    **_error_context(error)),
    ]
    records.append(_run_finished(records, 0, "api_error"))
    return records


def _repository_record(item: dict, query: str) -> dict:
    """Transforma a observação original da API em um candidato sem SHA resolvido."""
    license_data = item.get("license") or {}
    return {
        "record_type": "repository",
        "repository_id": item["id"],
        "repository_url": item["html_url"],
        "repository": item["full_name"],
        "language": item.get("language"),
        "created_at": item["created_at"],
        "license_reported": license_data.get("spdx_id"),
        "stars": item["stargazers_count"],
        "default_branch": item["default_branch"],
        "commit_sha": None,
        "sha_status": "unavailable",
        "discovered_at": _utc_timestamp(datetime.now(timezone.utc)),
        "search_queries": [query],
    }


def _merge_query(record: dict, query: str) -> None:
    """Acrescenta uma consulta de origem sem alterar a primeira observação."""
    if query not in record["search_queries"]:
        record["search_queries"].append(query)


def _collect_items(client, items: list[dict], query: str, limit: int,
                   repositories: dict, records: list[dict]) -> None:
    """Consolida os IDs da página e resolve o SHA somente na primeira observação."""
    for item in items:
        if len(repositories) >= limit:
            break
        repository_id = item["id"]
        if repository_id in repositories:
            _merge_query(repositories[repository_id], query)
            continue
        record = _repository_record(item, query)
        repositories[repository_id] = record
        records.append(record)
        try:
            record["commit_sha"] = resolve_commit_sha(
                client, record["repository"], record["default_branch"],
            )
            record["sha_status"] = "resolved"
        except EXPECTED_API_ERRORS as error:
            records.append(_diagnostic(
                "sha_unavailable", "Não foi possível recuperar o SHA da branch padrão.",
                query=query, repository_id=repository_id, **_error_context(error),
            ))


def _check_incomplete(data: dict, query: str, records: list[dict]) -> None:
    """Registra a limitação de completude informada em uma página da API."""
    if data["incomplete_results"]:
        records.append(_diagnostic(
            "incomplete_results",
            "A API informou resultados incompletos; o intervalo não é exaustivo.",
            query=query,
        ))


def _collect_pages(client, query: str, first_page: dict, limit: int,
                   repositories: dict, records: list[dict], can_split: bool) -> bool:
    """Aproveita páginas acessíveis e indica a necessidade de subdivisão como fallback."""
    data = first_page
    total_count = first_page["total_count"]
    for page in range(1, 11):
        if page > 1:
            data = fetch_search_page(client, query, page)
        _check_incomplete(data, query, records)
        _collect_items(client, data["items"], query, limit, repositories, records)
        if data["incomplete_results"] and can_split and len(repositories) < limit:
            return True
        if (len(repositories) >= limit or not data["items"]
                or page * 100 >= total_count):
            return False
    if total_count > 1000 and len(repositories) < limit:
        if can_split:
            return True
        records.append(_diagnostic(
            "search_saturated",
            "O intervalo de um segundo excede os 1000 resultados acessíveis.",
            query=query,
        ))
    return False


def collect_repositories(client, start: datetime, end: datetime, limit: int) -> list[dict]:
    """Coleta IDs únicos por busca direta, com particionamento de criação como fallback.

    Recebe limites UTC em segundos no intervalo [start, end), com fim exclusivo.
    Falhas esperadas preservam candidatos; erros de programação são propagados.
    """
    records = [_run_started(start, end, limit)]
    repositories = {}
    intervals = [(start, end)]
    reason = "search_exhausted"
    query = None
    try:
        while intervals and len(repositories) < limit:
            lower, upper = intervals.pop()
            query = build_query(lower, upper)
            data = fetch_search_page(client, query, 1)
            can_split = (upper - lower).total_seconds() > 1
            needs_split = _collect_pages(
                client, query, data, limit, repositories, records, can_split,
            )
            if needs_split:
                newer, older = split_interval(lower, upper)
                intervals.extend((older, newer))
        if len(repositories) >= limit:
            reason = "limit_reached"
    except EXPECTED_API_ERRORS as error:
        reason = "api_error"
        records.append(_diagnostic(
            "api_error", "Falha ao consultar a busca do GitHub.",
            query=query, **_error_context(error),
        ))
    records.append(_run_finished(records, len(repositories), reason))
    return records
