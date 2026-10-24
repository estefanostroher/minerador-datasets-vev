"""Synchronous REST transport with bounded retries and secret-free evidence.

The wait budget belongs to the client lifetime, across endpoints. Only the
Authorization request header receives the token. No request headers or exception
messages are copied into evidence; response headers use a strict allowlist.
"""

import base64
import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, cast
from urllib.parse import quote

import httpx

from robo_minerador.config import DiscoveryConfig

Envelope = dict[str, Any]
Observer = Callable[[Envelope], None]
SAFE_HEADERS = {
    "retry-after",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
    "x-ratelimit-used",
    "x-ratelimit-resource",
    "x-github-request-id",
    "date",
    "link",
    "content-type",
    "x-github-api-version-selected",
}


@dataclass(frozen=True)
class GitHubResponse:
    envelope: Envelope

    @property
    def data(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(base64.b64decode(self.envelope["body_base64"])))


class GitHubError(RuntimeError):
    def __init__(self, envelope: Envelope) -> None:
        self.envelope = envelope
        super().__init__(f"GitHub {envelope['error']} (status={envelope['status']})")


class TransportError(GitHubError):
    """Network attempts or their wait budget were exhausted."""


class PermanentHTTPError(GitHubError):
    """An HTTP or protocol error that must not be retried."""


class RateLimitExhausted(GitHubError):
    """Rate-limit attempts or the global wait budget were exhausted."""


class TransientHTTPError(GitHubError):
    """Retryable server errors exhausted the attempt/wait budget."""


ERROR_TYPES = {
    "transport": TransportError,
    "http_permanent": PermanentHTTPError,
    "invalid_json": PermanentHTTPError,
    "rate_limit_exhausted": RateLimitExhausted,
    "http_transient": TransientHTTPError,
}


def replay_response(envelope: Envelope) -> GitHubResponse:
    """Interpret archived terminal evidence without making a request."""
    if envelope["error"]:
        raise ERROR_TYPES[envelope["error"]](envelope)
    return GitHubResponse(envelope)


class GitHubClient:
    def __init__(
        self,
        config: DiscoveryConfig,
        *,
        http: httpx.Client,
        token: str | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        on_response: Observer | None = None,
    ) -> None:
        self.config = config.model_copy(deep=True)
        self.http = http
        self._token = token
        self.clock = clock
        self.sleep = sleep
        self.on_response = on_response
        self.waited = 0.0

    def search_page(self, query: str, page: int) -> GitHubResponse:
        if page < 1:
            raise ValueError("page must be positive")
        return self._get(
            "/search/repositories",
            {
                "q": query,
                "page": page,
                "per_page": self.config.per_page,
                "sort": "updated",
                "order": "asc",
            },
        )

    def get_repository(self, id: int) -> GitHubResponse:
        return self._get(f"/repositories/{id}", {})

    def get_commit(self, full_name: str, ref: str) -> GitHubResponse:
        return self._get(f"/repos/{quote(full_name, safe='/')}/commits/{quote(ref, safe='')}", {})

    def _clean(self, text: str) -> str:
        return text.replace(self._token, "[REDACTED]") if self._token else text

    def _delay(self, headers: httpx.Headers, attempt: int, rate: bool) -> float:
        fallback = float(60 * 2 ** (attempt - 1) if rate else 2 ** (attempt - 1))
        values: list[float] = []
        if "retry-after" in headers:
            try:
                delay = float(headers["retry-after"])
            except ValueError:
                try:
                    delay = parsedate_to_datetime(headers["retry-after"]).timestamp() - self.clock()
                except (ValueError, TypeError, OverflowError):
                    delay = fallback
            if math.isfinite(delay) and delay >= 0:
                values.append(max(1.0, delay))
        if headers.get("x-ratelimit-remaining") == "0" and "x-ratelimit-reset" in headers:
            try:
                reset = float(headers["x-ratelimit-reset"])
                if math.isfinite(reset) and reset >= 0:
                    values.append(max(1.0, reset - self.clock()))
            except ValueError:
                pass
        return max(values) if values else fallback

    def _get(self, endpoint: str, params: dict[str, str | int]) -> GitHubResponse:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self.config.api_version,
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        for attempt in range(1, self.config.transport_attempts + 1):
            envelope: Envelope = {
                "api": "https://api.github.com",
                "endpoint": self._clean(endpoint),
                "params": {
                    k: self._clean(v) if isinstance(v, str) else v for k, v in params.items()
                },
                "api_version": self.config.api_version,
                "attempt": attempt,
                "observed_at": datetime.fromtimestamp(self.clock(), UTC).isoformat(),
                "status": None,
                "headers": {},
                "body_base64": "",
                "error": None,
            }
            retry, rate = False, False
            response_headers = httpx.Headers()
            try:
                response = self.http.get(
                    f"https://api.github.com{endpoint}",
                    params=params,
                    headers=headers,
                    follow_redirects=False,
                )
            except httpx.TransportError:
                envelope["error"] = "transport"
                retry = True
            else:
                response_headers = response.headers
                body = response.content
                if self._token:
                    body = body.replace(self._token.encode(), b"[REDACTED]")
                envelope.update(
                    status=response.status_code,
                    headers={
                        k: self._clean(v)
                        for k, v in response.headers.items()
                        if k.lower() in SAFE_HEADERS
                    },
                    body_base64=base64.b64encode(body).decode("ascii"),
                )
                rate = response.status_code == 429 or (
                    response.status_code == 403
                    and (
                        "retry-after" in response.headers
                        or response.headers.get("x-ratelimit-remaining") == "0"
                        or b"you have exceeded a secondary rate limit" in body.lower()
                    )
                )
                retry = rate or 500 <= response.status_code < 600
                if rate:
                    envelope["error"] = "rate_limit_exhausted"
                elif retry:
                    envelope["error"] = "http_transient"
                elif not 200 <= response.status_code < 300:
                    envelope["error"] = "http_permanent"
                else:
                    try:
                        data = json.loads(body)
                        if not isinstance(data, dict):
                            raise ValueError
                    except (ValueError, UnicodeDecodeError):
                        envelope["error"] = "invalid_json"
            delay = self._delay(response_headers, attempt, rate) if retry else 0.0
            retry = retry and attempt < self.config.transport_attempts
            retry = retry and self.waited + delay <= self.config.max_wait_seconds
            envelope["terminal"] = not retry
            if self.on_response:
                self.on_response(envelope)
            if not retry:
                return replay_response(envelope)
            self.waited += delay
            self.sleep(delay)
        raise AssertionError("positive transport_attempts required")
