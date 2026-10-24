import base64
import json
from datetime import UTC, datetime
from email.utils import format_datetime

import httpx
import pytest

from robo_minerador.config import DiscoveryConfig
from robo_minerador.discovery.github import (
    GitHubClient,
    PermanentHTTPError,
    RateLimitExhausted,
    TransportError,
)


def settings(**values: object) -> DiscoveryConfig:
    return DiscoveryConfig.model_validate(
        {"created_from": "2020-01-01", "created_until": "2020-01-02", **values}
    )


def test_endpoints_headers_and_safe_evidence(caplog: pytest.LogCaptureFixture) -> None:
    calls = []
    evidence = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer secret-token"
        assert request.headers["Accept"] == "application/vnd.github+json"
        assert request.headers["X-GitHub-Api-Version"] == "2026-03-10"
        return httpx.Response(
            200,
            json={"message": "secret-token", "items": []},
            headers={"Set-Cookie": "secret-token", "X-RateLimit-Remaining": "42"},
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = GitHubClient(
            settings(per_page=2), http=http, token="secret-token", on_response=evidence.append
        )
        client.search_page("language:TypeScript", 2)
        client.get_repository(123)
        client.get_commit("owner/repo", "feature/a")
    assert calls[0].url.params == httpx.QueryParams(
        {
            "q": "language:TypeScript",
            "page": "2",
            "per_page": "2",
            "sort": "updated",
            "order": "asc",
        }
    )
    assert calls[1].url.path == "/repositories/123"
    assert calls[2].url.raw_path == b"/repos/owner/repo/commits/feature%2Fa"
    serialized = json.dumps(evidence)
    assert "secret-token" not in serialized + caplog.text
    assert b"secret-token" not in base64.b64decode(evidence[0]["body_base64"])
    assert "set-cookie" not in serialized.lower()
    assert evidence[0]["headers"]["x-ratelimit-remaining"] == "42"
    assert evidence[0]["status"] == 200
    assert evidence[0]["observed_at"]


@pytest.mark.parametrize(
    "status,headers,delay",
    [
        (429, {"Retry-After": "3"}, 3),
        (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "105"}, 5),
        (403, {"Retry-After": format_datetime(datetime.fromtimestamp(104, UTC))}, 4),
        (403, {"X-RateLimit-Remaining": "0"}, 60),
        (503, {}, 1),
    ],
)
def test_retry_only_transient(status: int, headers: dict[str, str], delay: int) -> None:
    responses = [httpx.Response(status, headers=headers), httpx.Response(200, json={"id": 1})]
    waits = []
    with httpx.Client(transport=httpx.MockTransport(lambda _: responses.pop(0))) as http:
        client = GitHubClient(settings(), http=http, sleep=waits.append, clock=lambda: 100)
        assert client.get_repository(1).data == {"id": 1}
    assert waits == [delay]


def test_permission_error_has_no_retry_and_redacts_exception() -> None:
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(403, text="secret-token")

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = GitHubClient(settings(), http=http, token="secret-token")
        with pytest.raises(PermanentHTTPError) as error:
            client.get_repository(1)
    assert len(calls) == 1
    assert "secret-token" not in str(error.value)


def test_global_wait_budget_and_bounded_attempts() -> None:
    calls = []
    waits = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "6"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = GitHubClient(settings(max_wait_seconds=10), http=http, sleep=waits.append)
        with pytest.raises(RateLimitExhausted):
            client.get_repository(1)
        with pytest.raises(RateLimitExhausted):
            client.get_repository(2)
    assert waits == [6]
    assert len(calls) == 3


def test_network_failure_bounded_without_leaking_headers() -> None:
    calls = []
    evidence = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ConnectError("Authorization: secret-token", request=request)

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = GitHubClient(
            settings(),
            http=http,
            token="secret-token",
            sleep=lambda _: None,
            on_response=evidence.append,
        )
        with pytest.raises(TransportError) as error:
            client.get_repository(1)
    assert len(calls) == 3
    assert len(evidence) == 3
    assert "secret-token" not in str(error.value) + json.dumps(evidence)


def test_normal_rate_headers_on_forbidden_do_not_trigger_retry() -> None:
    calls = []
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: (
                calls.append(request)
                or httpx.Response(
                    403,
                    headers={
                        "X-RateLimit-Remaining": "50",
                        "X-RateLimit-Reset": "9999999999",
                        "X-RateLimit-Resource": "core",
                    },
                )
            )
        )
    ) as http:
        with pytest.raises(PermanentHTTPError):
            GitHubClient(settings(), http=http).get_repository(1)
    assert len(calls) == 1


def test_transient_server_errors_stop_after_configured_attempts() -> None:
    from robo_minerador.discovery.github import TransientHTTPError

    calls, waits = [], []
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: (calls.append(request) or httpx.Response(503))
        )
    ) as http:
        with pytest.raises(TransientHTTPError):
            GitHubClient(settings(), http=http, sleep=waits.append).get_repository(1)
    assert len(calls) == 3
    assert waits == [1, 2]


def test_secondary_limit_message_retries_with_bounded_backoff() -> None:
    calls, waits, evidence = [], [], []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            403,
            headers={"X-RateLimit-Remaining": "50"},
            json={"message": "You have exceeded a secondary rate limit. Please wait."},
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = GitHubClient(
            settings(max_wait_seconds=100),
            http=http,
            sleep=waits.append,
            on_response=evidence.append,
        )
        with pytest.raises(RateLimitExhausted):
            client.get_repository(1)
    assert waits == [60]
    assert len(calls) == 2
    assert evidence[-1]["terminal"] is True
    assert b"secondary rate limit" in base64.b64decode(evidence[-1]["body_base64"])


@pytest.mark.parametrize(
    "headers",
    [
        {"Retry-After": "NaN"},
        {"Retry-After": "-10"},
        {"Retry-After": "Infinity"},
        {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "NaN"},
        {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "-10"},
        {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "Infinity"},
    ],
)
def test_invalid_rate_delay_headers_keep_exponential_fallback(headers: dict[str, str]) -> None:
    calls, waits = [], []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429, headers=headers)

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = GitHubClient(settings(), http=http, sleep=waits.append, clock=lambda: 100)
        with pytest.raises(RateLimitExhausted):
            client.get_repository(1)
    assert waits == [60, 120]
    assert len(calls) == 3
