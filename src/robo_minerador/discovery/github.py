"""Synchronous REST transport with bounded retries and secret-free evidence.

The wait budget belongs to the client lifetime, across endpoints. Only the
Authorization request header receives the token. No request headers or exception
messages are copied into evidence; response headers use a strict allowlist.
"""

import base64
import json
import math
import re
import time
from collections.abc import Callable
from contextlib import closing
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
    "response_too_large": PermanentHTTPError,
    "unsafe_redirect": PermanentHTTPError,
    "unsupported_content_encoding": PermanentHTTPError,
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

    def get_license(self, full_name: str, commit_sha: str) -> GitHubResponse:
        self._snapshot_identity(full_name, commit_sha)
        return self._get(f"/repos/{full_name}/license", {"ref": commit_sha}, max_bytes=4_000_000)

    def get_archive(self, full_name: str, commit_sha: str, *, max_bytes: int) -> GitHubResponse:
        """Bounded binary response; redirects never inherit client auth/cookies."""
        self._snapshot_identity(full_name, commit_sha)
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        return self._get(
            f"/repos/{full_name}/tarball/{commit_sha}", {}, binary=True, max_bytes=max_bytes
        )

    @staticmethod
    def _snapshot_identity(full_name: str, commit_sha: str) -> None:
        if re.fullmatch(r"[\w.-]+/[\w.-]+", full_name, re.ASCII) is None or any(
            part in (".", "..") for part in full_name.split("/")
        ):
            raise ValueError("invalid repository full_name")
        if re.fullmatch(r"[0-9a-fA-F]{40}", commit_sha) is None:
            raise ValueError("snapshot requires a full commit SHA")

    def _snapshot_request(
        self,
        endpoint: str,
        params: dict[str, str | int],
        headers: dict[str, str],
        max_bytes: int,
        binary: bool,
        envelope: Envelope,
    ) -> tuple[int, httpx.Headers, bytes, list[dict[str, object]]]:
        url = httpx.URL(f"https://api.github.com{endpoint}", params=params)
        hops: list[dict[str, object]] = []
        for _ in range(6):
            # Build Request directly: injected Client defaults (auth, headers, cookies)
            # must not cross the API -> codeload boundary.
            request_headers = dict(headers)
            request_headers["Accept-Encoding"] = "identity"
            if url.host != "api.github.com":
                request_headers.pop("Authorization", None)
            request = httpx.Request("GET", url, headers=request_headers)
            response = self.http.send(request, stream=True, follow_redirects=False, auth=None)
            with closing(response):
                hops.append({"url": self._clean(str(url)), "status": response.status_code})
                envelope.update(
                    status=response.status_code,
                    headers={
                        k: self._clean(v)
                        for k, v in response.headers.items()
                        if k.lower() in SAFE_HEADERS
                    },
                    requests=hops,
                )
                if response.status_code in (301, 302, 303, 307, 308) and binary:
                    try:
                        target = url.join(response.headers.get("location", ""))
                    except httpx.InvalidURL:
                        raise ValueError("unsafe_redirect") from None
                    if (
                        not response.headers.get("location")
                        or target.scheme != "https"
                        or target.host not in {"api.github.com", "codeload.github.com"}
                        or target.port not in (None, 443)
                        or target.userinfo
                        or target.fragment
                    ):
                        raise ValueError("unsafe_redirect")
                    url = target
                    continue
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise ValueError("unsupported_content_encoding")
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=min(65536, max_bytes + 1)):
                    if len(body) + len(chunk) > max_bytes:
                        raise ValueError("response_too_large")
                    body.extend(chunk)
                return response.status_code, response.headers, bytes(body), hops
        raise ValueError("unsafe_redirect")

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

    def _get(
        self,
        endpoint: str,
        params: dict[str, str | int],
        *,
        binary: bool = False,
        max_bytes: int | None = None,
    ) -> GitHubResponse:
        headers = {
            "User-Agent": "robo-minerador/0.1.0",
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
                if max_bytes is not None:
                    status, response_headers, body, hops = self._snapshot_request(
                        endpoint, params, headers, max_bytes, binary, envelope
                    )
                    envelope["requests"] = hops
                else:
                    response = self.http.get(
                        f"https://api.github.com{endpoint}",
                        params=params,
                        headers=headers,
                        follow_redirects=False,
                    )
                    status, response_headers, body = (
                        response.status_code,
                        response.headers,
                        response.content,
                    )
            except httpx.RemoteProtocolError:
                # httpx validates Location even with follow_redirects=False.
                # An invalid protocol/redirect is not a transient snapshot request.
                envelope["error"] = "http_permanent" if max_bytes is not None else "transport"
                retry = max_bytes is None
            except httpx.TransportError:
                envelope["error"] = "transport"
                retry = True
            except ValueError as error:
                if str(error) not in {
                    "unsafe_redirect",
                    "response_too_large",
                    "unsupported_content_encoding",
                }:
                    raise
                envelope["error"] = str(error)
            else:
                if self._token and (not binary or not 200 <= status < 300):
                    body = body.replace(self._token.encode(), b"[REDACTED]")
                envelope.update(
                    status=status,
                    headers={
                        k: self._clean(v)
                        for k, v in response_headers.items()
                        if k.lower() in SAFE_HEADERS
                    },
                    body_base64=base64.b64encode(body).decode("ascii"),
                )
                rate = status == 429 or (
                    status == 403
                    and (
                        "retry-after" in response_headers
                        or response_headers.get("x-ratelimit-remaining") == "0"
                        or b"you have exceeded a secondary rate limit" in body.lower()
                    )
                )
                retry = rate or 500 <= status < 600
                if rate:
                    envelope["error"] = "rate_limit_exhausted"
                elif retry:
                    envelope["error"] = "http_transient"
                elif not 200 <= status < 300:
                    envelope["error"] = "http_permanent"
                elif not binary:
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
