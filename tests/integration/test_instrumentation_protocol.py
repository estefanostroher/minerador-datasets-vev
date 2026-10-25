"""Coordinator HTTP contract: overlays are data, not host-executed project code."""

import json

import httpx
import pytest

from robo_minerador.extraction.client import ProtocolError
from robo_minerador.extraction.instrumentation import InstrumentationClient, InstrumentationRequest


def request():
    return InstrumentationRequest(
        request_id="t11-protocol",
        snapshot_id="sha256:" + "a" * 64,
        root="/workspace/snapshots/fixture",
        tsconfig_paths=["tsconfig.json"],
        framework="vitest",
        framework_version="4.0.0",
    )


def response():
    return {
        "protocol_version": "1.0",
        "request_id": "t11-protocol",
        "snapshot_id": "sha256:" + "a" * 64,
        "status": "ok",
        "manifest": {
            "schema_version": "1.0",
            "purpose": "observational_supplement",
            "status": "not_evaluated",
            "framework": "vitest",
            "framework_version": "4.0.0",
            "typescript_version": "5.8.3",
            "files": [],
            "declarations": [],
            "targets": [],
            "callsites": [],
            "limitations": [{"code": "unsupported", "message": "Concurrency is not supported"}],
        },
        "supplements": {},
        "errors": [],
    }


def test_coordinator_uses_separate_http_endpoint_and_explicit_patch():
    def handler(received):
        assert received.url.path == "/v1/instrument"
        assert json.loads(received.content) == request().model_dump(mode="json")
        return httpx.Response(200, json=response())

    with InstrumentationClient(
        "http://analyzer:3000", transport=httpx.MockTransport(handler)
    ) as client:
        result = client.instrument(request())
        assert result.manifest.status == "not_evaluated"
        assert result.supplements == {}


@pytest.mark.parametrize(
    "change",
    [
        {"request_id": "different"},
        {"snapshot_id": "sha256:" + "b" * 64},
        {"extra": True},
    ],
)
def test_coordinator_rejects_uncorrelated_or_unknown_response_fields(change):
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={**response(), **change}))
    with InstrumentationClient("http://analyzer:3000", transport=transport) as client:
        with pytest.raises(ProtocolError):
            client.instrument(request())


def test_unsupported_mode_cannot_supply_executable_supplements():
    value = response()
    value["supplements"] = {"runtime.cjs": "untrusted"}
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=value))
    with InstrumentationClient("http://analyzer:3000", transport=transport) as client:
        with pytest.raises(ProtocolError, match="Unsupported"):
            client.instrument(request())
