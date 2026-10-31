"""Integração com o GitHub e avisos restritos de espera por limite da API."""

import json
import logging
import re
from threading import Event
from typing import Any

from github import Auth, Github
from github.GithubRetry import GithubRetry
from github.Requester import Requester
from urllib3.exceptions import MaxRetryError

from .concurrency import RateLimitGate


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


class _CoordinatedRetry(GithubRetry):
    """Delega reconhecimento ao PyGithub e compartilha apenas pausas de rate limit."""

    def __init__(self, *, gate, cancel, **kwargs):
        """Preserva o gate e o cancelamento ao construir políticas e seus clones."""
        self.gate = gate
        self.cancel = cancel
        self._rate_limit_type = None
        super().__init__(**kwargs)

    def new(self, **kwargs):
        """Mantém gate e evento em cada clone produzido pelo urllib3."""
        return super().new(gate=self.gate, cancel=self.cancel, **kwargs)

    def increment(self, method=None, url=None, response=None, error=None,
                  _pool=None, _stacktrace=None):
        """Publica limites reconhecidos mesmo se o retry esgotar, preservando o erro."""
        self._rate_limit_type = None
        try:
            retry = super().increment(method, url, response, error, _pool, _stacktrace)
        except MaxRetryError:
            if response is not None and (
                response.status == 429 or response.status == 403 and (
                    "Retry-After" in response.headers or self._rate_limit_type is not None
                )
            ):
                self._publish_limit(response)
            raise
        if response is not None and response.status in (403, 429):
            self._publish_limit(response, retry)
        return retry

    def get_content(self, response, url):
        """Lê o corpo uma única vez e retém apenas o tipo de limite reconhecido."""
        content = super().get_content(response, url)
        try:
            message = json.loads(content).get("message")
        except (ValueError, AttributeError):
            return content
        if isinstance(message, str) and Requester.isRateLimitError(message):
            self._rate_limit_type = (
                "primary" if Requester.isPrimaryRateLimitError(message) else "secondary"
            )
        return content

    def _publish_limit(self, response, retry=None):
        """Compartilha a espera explícita, calculada ou reconhecida no último retry."""
        seconds = self.get_retry_after(response)
        if seconds is None:
            if retry is not None:
                seconds = retry.get_backoff_time()
            else:
                seconds = self.secondary_rate_wait if self._rate_limit_type == "secondary" else 0
        self.gate.defer(seconds)
        headers = dict(response.headers)
        if self._rate_limit_type == "primary":
            headers["x-ratelimit-remaining"] = "0"
        self.gate.observe(headers)

    def sleep(self, response=None):
        """Espera uma única vez; erros 5xx usam backoff local, não pausa global."""
        seconds = self.get_retry_after(response) if response is not None else None
        if seconds is None:
            seconds = self.get_backoff_time()
        if response is not None and response.status in (403, 429):
            self.gate.wait(cancel=self.cancel)
        else:
            self.gate.wait_for(seconds, cancel=self.cancel)


def create_client(token: str, gate: RateLimitGate | None = None) -> Github:
    """Cria cliente isolado; gate opcional coordena chamadas e retries entre workers.

    O chamador fecha o cliente. cancel_pending_requests interrompe suas esperas,
    sem alterar a pausa compartilhada nem cancelar os demais clientes.
    """
    configure_retry_logging()
    if gate is None:
        return Github(auth=Auth.Token(token), timeout=30)
    cancel = Event()
    retry = _CoordinatedRetry(
        gate=gate, cancel=cancel, total=Github.default_retry.total,
        backoff_factor=Github.default_retry.backoff_factor,
        status_forcelist=list(range(500, 600)) + [429],
    )
    client = Github(auth=Auth.Token(token), timeout=30, retry=retry)
    request_json = client.requester.requestJson

    def request_with_gate(*args, **kwargs):
        """Preserva argumentos do requester, aguardando antes e observando a cota após."""
        gate.wait(cancel=cancel)
        status, headers, body = request_json(*args, **kwargs)
        if 200 <= status < 400:
            gate.observe(headers)
        return status, headers, body

    def cancel_pending_requests():
        """Acorda todas as esperas deste cliente para permitir desligamento do pool."""
        cancel.set()
        gate.wake()

    client.requester.requestJson = request_with_gate
    client.cancel_pending_requests = cancel_pending_requests
    return client


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
