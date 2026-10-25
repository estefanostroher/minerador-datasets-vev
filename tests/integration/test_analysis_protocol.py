import hashlib
import json
from pathlib import Path

import httpx
import pytest

from robo_minerador.extraction.client import (
    AnalysisClient,
    AnalysisError,
    AnalysisRequest,
    AnalysisResponse,
    AnalysisTransportError,
    ProtocolError,
    check_response,
    load_schema,
)


def request() -> AnalysisRequest:
    return AnalysisRequest(
        request_id="fixture-analysis",
        snapshot_id="sha256:" + hashlib.sha256(b"protocol transport fixture").hexdigest(),
        root="/workspace/snapshots/fixture",
        tsconfig_paths=["tsconfig.json"],
        framework="vitest",
    )


def response(**changes: object) -> dict[str, object]:
    return {
        "protocol_version": "1.0",
        "request_id": "fixture-analysis",
        "status": "ok",
        "targets": [],
        "test_calls": [],
        "documents": [],
        "metrics": [],
        "limitations": [],
        "errors": [],
        **changes,
    }


def test_validated_roundtrip_and_explicit_timeout() -> None:
    def handle(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST"
        assert req.url.path == "/v1/analyze"
        assert json.loads(req.content) == request().model_dump(mode="json")
        assert req.extensions["timeout"]["read"] == 12
        return httpx.Response(200, json=response())

    with AnalysisClient(
        "http://analyzer:3000", timeout=12, transport=httpx.MockTransport(handle)
    ) as c:
        result = c.analyze(request())
    assert isinstance(result, AnalysisResponse)
    assert result.request_id == request().request_id


@pytest.mark.parametrize(
    "changes",
    [
        {"protocol_version": "2.0"},
        {"request_id": "different"},
        {"secret": "hidden"},
        {"targets": [{}]},
        {"documents": [{}]},
        {"status": "error"},
    ],
)
def test_response_contract_and_correlation(changes: dict[str, object]) -> None:
    with pytest.raises(ProtocolError):
        check_response(response(**changes), request().request_id)


@pytest.mark.parametrize("path", ["../tsconfig.json", "/etc/config.json", "C:/config.json"])
def test_invalid_request_never_reaches_transport(path: str) -> None:
    called = False

    def handle(req: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=response())

    with AnalysisClient("http://analyzer:3000", transport=httpx.MockTransport(handle)) as c:
        invalid = request().model_copy(update={"tsconfig_paths": [path]})
        with pytest.raises(ProtocolError):
            c.analyze(invalid)
    assert not called


def test_structured_server_failure() -> None:
    payload = response(
        status="error", errors=[{"code": "invalid_request", "message": "Invalid snapshot"}]
    )
    with AnalysisClient(
        "http://analyzer:3000",
        transport=httpx.MockTransport(lambda req: httpx.Response(400, json=payload)),
    ) as c:
        with pytest.raises(AnalysisError) as error:
            c.analyze(request())
    assert error.value.errors[0].code == "invalid_request"


def test_timeout_is_typed_and_does_not_echo_transport_secrets() -> None:
    def handle(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("sensitive-path", request=req)

    with AnalysisClient("http://analyzer:3000", transport=httpx.MockTransport(handle)) as c:
        with pytest.raises(AnalysisTransportError, match="transport") as error:
            c.analyze(request())
    assert "sensitive-path" not in str(error.value)


def test_schema_loading_does_not_depend_on_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    for name in ("analysis-request", "analysis-response"):
        assert load_schema(name)["additionalProperties"] is False


def characterization() -> dict[str, object]:
    target_id = "sha256:" + "a" * 64
    return response(
        documents=[
            {
                "symbol_target_id": target_id,
                "status": "not_evaluated",
                "reason": "documentation_behavior_unresolved",
                "evidence": ["a.ts:0:22"],
                "sources": [
                    {
                        "path": "a.ts",
                        "start_offset": 0,
                        "end_offset": 22,
                        "text": "/** Helper function */",
                        "association": "adjacent_jsdoc",
                        "symbol": "f",
                        "markers": [],
                    }
                ],
            }
        ],
        metrics=[
            {
                "symbol_target_id": target_id,
                "status": "approved",
                "reason": "static_measurement",
                "method": "cyclomatic-ts-v1",
                "cyclomatic_complexity": 1,
                "physical_lines": 1,
                "decisions": [],
                "triviality": {
                    "status": "not_evaluated",
                    "reason": "triviality_shape_unresolved",
                    "evidence": [],
                },
            }
        ],
    )


def test_t09_characterization_roundtrip_preserves_original_metadata() -> None:
    data = characterization()
    parsed = check_response(data, request().request_id)
    assert parsed.model_dump(mode="json", exclude_none=True) == data
    assert parsed.documents[0].sources[0].text == "/** Helper function */"
    assert parsed.metrics[0].cyclomatic_complexity == 1
    assert parsed.metrics[0].triviality.status == "not_evaluated"


@pytest.mark.parametrize("field", ["documents", "metrics"])
@pytest.mark.parametrize("mutation", ["extra", "wrong_type", "unsafe_path", "bad_state"])
def test_t09_characterization_is_strict(field: str, mutation: str) -> None:
    data = characterization()
    records = data[field]
    assert isinstance(records, list)
    record = records[0]
    if mutation == "extra":
        record["unexpected"] = True
    elif mutation == "wrong_type":
        record["symbol_target_id"] = 1
    elif mutation == "bad_state":
        record["status"] = "unknown"
    elif field == "documents":
        record["sources"][0]["path"] = "../outside.md"
    else:
        record["triviality"]["evidence"] = [1]
    with pytest.raises(ProtocolError):
        check_response(data, request().request_id)
