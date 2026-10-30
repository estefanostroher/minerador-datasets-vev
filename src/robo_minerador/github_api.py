"""Integração com o GitHub e avisos restritos de espera por limite da API."""

import logging
import re
from typing import Any

from github import Auth, Github


class _RateLimitWaitFilter(logging.Filter):
    """Restringe os avisos de retry a durações de espera reconhecidas."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Converte somente mensagens conhecidas de espera em avisos sem contexto HTTP."""
        match = re.fullmatch(
            r"(?:Retrying after ([0-9]+(?:\.[0-9]+)?) seconds"
            r"|Setting next backoff to ([0-9]+(?:\.[0-9]+)?)s)",
            record.getMessage(),
        )
        if match is None:
            return False
        seconds = match.group(1) or match.group(2)
        record.msg = f"Limite temporário da API: aguardando {seconds} segundos para tentar novamente."
        record.args = ()
        record.levelno = logging.WARNING
        record.levelname = "WARNING"
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def configure_retry_logging() -> None:
    """Configura avisos de espera no stderr sem duplicar handlers entre clientes."""
    logger = logging.getLogger("github.GithubRetry")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not any(isinstance(item, _RateLimitWaitFilter) for item in logger.filters):
        logger.addFilter(_RateLimitWaitFilter())
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(logging.WARNING)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)


def create_client(token: str) -> Github:
    """Cria um cliente com retry padrão; o chamador deve fechá-lo com close ou with."""
    configure_retry_logging()
    return Github(auth=Auth.Token(token), timeout=30)


def fetch_search_page(client: Github, query: str, page: int) -> dict[str, Any]:
    """Retorna o corpo original de uma página de busca, propagando falhas da API."""
    _, data = client.requester.requestJsonAndCheck(
        "GET",
        "/search/repositories",
        parameters={"q": query, "per_page": 100, "page": page,
                    "sort": "updated", "order": "desc"},
    )
    return data


def resolve_commit_sha(client: Github, full_name: str, default_branch: str) -> str:
    """Obtém o SHA do commit atual da branch padrão, propagando falhas da API."""
    return client.get_repo(full_name, lazy=True).get_branch(default_branch).commit.sha
