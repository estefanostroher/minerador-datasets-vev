"""Schema-validated client for the isolated T08 analyzer."""

import json
from importlib.resources import files
from pathlib import Path
from types import TracebackType
from typing import Literal, Self

import httpx
from jsonschema import Draft7Validator
from pydantic import BaseModel, ConfigDict, ValidationError


class ProtocolError(Exception):
    """Malformed, incompatible or uncorrelated protocol message."""


class AnalysisTransportError(Exception):
    """HTTP transport could not complete the request."""


class ProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AnalysisRequest(ProtocolModel):
    protocol_version: Literal["1.0"] = "1.0"
    request_id: str
    operation: Literal["analyze_snapshot"] = "analyze_snapshot"
    snapshot_id: str
    root: str
    tsconfig_paths: list[str]
    framework: Literal["jest", "vitest"]


class Issue(ProtocolModel):
    code: str
    message: str
    path: str | None = None


class Target(ProtocolModel):
    id: str
    kind: Literal["function", "method"]
    qualified_name: str
    path: str
    start_offset: int
    end_offset: int
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    code: str
    signature: str


class TestCall(ProtocolModel):
    symbol_target_id: str | None
    callsite_path: str
    start_offset: int
    end_offset: int
    case_declaration_id: str
    case_title: str
    case_start_offset: int
    parameterized: bool
    link_kind: Literal["direct", "unresolved"]
    reason: Literal[
        "resolved_symbol", "ambiguous_receiver", "unsupported_callee", "target_not_found"
    ]
    path_resolution: list[str]
    runtime_confirmed: Literal[False]
    mock_potential: bool


class Evaluation(ProtocolModel):
    status: Literal["approved", "rejected", "not_evaluated"]
    reason: str
    evidence: list[str]


class DocumentMarker(ProtocolModel):
    kind: Literal["inputs", "outputs", "constraints", "errors"]
    text: str
    start_offset: int
    end_offset: int


class DocumentSource(ProtocolModel):
    path: str
    start_offset: int
    end_offset: int
    text: str
    association: Literal["adjacent_jsdoc", "overload_jsdoc", "markdown_path_symbol"]
    symbol: str
    markers: list[DocumentMarker]


class Documentation(Evaluation):
    symbol_target_id: str
    sources: list[DocumentSource]


class Decision(ProtocolModel):
    kind: str
    text: str
    start_offset: int
    end_offset: int


class Metrics(ProtocolModel):
    symbol_target_id: str
    status: Literal["approved", "not_evaluated"]
    reason: str
    method: Literal["cyclomatic-ts-v1"]
    cyclomatic_complexity: int | None
    physical_lines: int
    decisions: list[Decision]
    triviality: Evaluation


class AnalysisResponse(ProtocolModel):
    protocol_version: Literal["1.0"]
    request_id: str
    status: Literal["ok", "error"]
    targets: list[Target]
    test_calls: list[TestCall]
    documents: list[Documentation]
    metrics: list[Metrics]
    limitations: list[Issue]
    errors: list[Issue]


class AnalysisError(Exception):
    """The analyzer returned a structured failure."""

    def __init__(self, errors: list[Issue]) -> None:
        super().__init__("Analyzer rejected analysis")
        self.errors = errors


def load_schema(name: Literal["analysis-request", "analysis-response"]) -> dict[str, object]:
    resource = files("robo_minerador.extraction").joinpath("schemas", name + ".schema.json")
    if resource.is_file():
        text = resource.read_text(encoding="utf-8")
    else:
        # Editable source trees use the sole schema source. Wheels carry the same
        # files via Hatch force-include; neither path depends on the current cwd.
        path = Path(__file__).resolve().parents[3] / "schemas" / (name + ".schema.json")
        text = path.read_text(encoding="utf-8")
    schema: dict[str, object] = json.loads(text)
    return schema


REQUEST_VALIDATOR = Draft7Validator(load_schema("analysis-request"))
RESPONSE_VALIDATOR = Draft7Validator(load_schema("analysis-response"))


def check_response(data: object, request_id: str) -> AnalysisResponse:
    if not RESPONSE_VALIDATOR.is_valid(data):
        raise ProtocolError("Invalid analysis response schema")
    try:
        result = AnalysisResponse.model_validate(data)
    except ValidationError:
        raise ProtocolError("Invalid analysis response types") from None
    if result.request_id != request_id:
        raise ProtocolError("Analysis response request_id mismatch")
    return result


class AnalysisClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._http = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(timeout),
            transport=transport,
            trust_env=False,
            follow_redirects=False,
        )

    def analyze(self, request: AnalysisRequest) -> AnalysisResponse:
        data = request.model_dump(mode="json")
        if not REQUEST_VALIDATOR.is_valid(data):
            raise ProtocolError("Invalid analysis request schema")
        try:
            response = self._http.post("/v1/analyze", json=data)
        except httpx.HTTPError:
            raise AnalysisTransportError("Analyzer transport failed") from None
        try:
            result = check_response(response.json(), request.request_id)
        except (ValueError, UnicodeError):
            raise ProtocolError("Invalid analyzer JSON response") from None
        if result.status == "error":
            raise AnalysisError(result.errors)
        if response.status_code != 200:
            raise ProtocolError("Unexpected analyzer HTTP status")
        return result

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
