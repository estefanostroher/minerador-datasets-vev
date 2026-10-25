"""Typed HTTP overlay API. No analyzer CLI or project code runs on the host."""

import hashlib
import json
from typing import Literal

import httpx
from pydantic import Field, ValidationError, field_validator

from robo_minerador.extraction.client import (
    AnalysisClient,
    AnalysisError,
    AnalysisRequest,
    AnalysisTransportError,
    Issue,
    ProtocolError,
    ProtocolModel,
    Target,
    TestCall,
    check_response,
)


class InstrumentationRequest(AnalysisRequest):
    operation: Literal["instrument_snapshot"] = "instrument_snapshot"  # type: ignore[assignment]
    framework_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")


class Edit(ProtocolModel):
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    text: str
    overlay_start: int = Field(ge=0)
    overlay_end: int = Field(ge=0)


class Transpilation(ProtocolModel):
    typescript_version: Literal["5.8.3"]
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    javascript: str
    source_map: str


class OverlayFile(ProtocolModel):
    path: str
    original_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    overlay_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source: str
    edits: list[Edit]
    transpilation: Transpilation

    @field_validator("path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        if not value or any(p in ("", ".", "..") for p in value.split("/")):
            raise ValueError("unsafe overlay path")
        if any(c in value for c in "\\:\0"):
            raise ValueError("unsafe overlay path")
        return value


class Declaration(ProtocolModel):
    file: str
    original_position: int = Field(ge=0)
    overlay_start: int = Field(ge=0)
    overlay_end: int = Field(ge=0)
    parameterized: bool


def verify_overlay(original: bytes, overlay: OverlayFile) -> None:
    """Replay edits using UTF-16 coordinates, corroborating both source hashes."""
    if hashlib.sha256(original).hexdigest() != overlay.original_sha256:
        raise ValueError("original source hash mismatch")
    encoded = original.decode("utf-8").encode("utf-16-le")
    output = bytearray()
    cursor = 0
    for edit in overlay.edits:
        if not cursor <= edit.start <= edit.end <= len(encoded) // 2:
            raise ValueError("invalid original edit coordinates")
        output.extend(encoded[cursor * 2 : edit.start * 2])
        if edit.overlay_start != len(output) // 2:
            raise ValueError("invalid overlay start coordinates")
        output.extend(edit.text.encode("utf-16-le"))
        if edit.overlay_end != len(output) // 2:
            raise ValueError("invalid overlay end coordinates")
        cursor = edit.end
    output.extend(encoded[cursor * 2 :])
    source = output.decode("utf-16-le")
    if (
        source != overlay.source
        or hashlib.sha256(source.encode()).hexdigest() != overlay.overlay_sha256
    ):
        raise ValueError("overlay source hash mismatch")


class Callsite(TestCall):
    id: str = Field(pattern=r"^[a-f0-9]{64}$")


class InstrumentationManifest(ProtocolModel):
    schema_version: Literal["1.0"]
    purpose: Literal["observational_supplement"]
    status: Literal["ready", "not_evaluated"]
    framework: Literal["jest", "vitest"]
    framework_version: str
    typescript_version: Literal["5.8.3"]
    files: list[OverlayFile]
    declarations: list[Declaration]
    targets: list[Target]
    callsites: list[Callsite]
    limitations: list[Issue]


class InstrumentationResponse(ProtocolModel):
    protocol_version: Literal["1.0"]
    request_id: str
    snapshot_id: str
    status: Literal["ok"]
    manifest: InstrumentationManifest
    supplements: dict[str, str]
    errors: list[Issue]


def check_instrumentation(data: object, request: InstrumentationRequest) -> InstrumentationResponse:
    try:
        result = InstrumentationResponse.model_validate(data)
    except ValidationError:
        raise ProtocolError("Invalid instrumentation response") from None
    if (result.request_id, result.snapshot_id) != (request.request_id, request.snapshot_id):
        raise ProtocolError("Instrumentation response correlation mismatch")
    manifest = result.manifest
    if (manifest.framework, manifest.framework_version) != (
        request.framework,
        request.framework_version,
    ) or result.errors:
        raise ProtocolError("Instrumentation response metadata mismatch")
    for file in manifest.files:
        if (
            hashlib.sha256(file.source.encode()).hexdigest() != file.overlay_sha256
            or file.transpilation.input_sha256 != file.overlay_sha256
        ):
            raise ProtocolError("Overlay content hash mismatch")
        source_map = json.loads(file.transpilation.source_map)
        if source_map.get("version") != 3 or source_map.get("sourcesContent") != [file.source]:
            raise ProtocolError("Invalid overlay transpilation map")
    if manifest.status == "ready":
        encoded = result.supplements.get("manifest.cjs", "")
        if not encoded.startswith("module.exports = ") or not encoded.endswith(";\n"):
            raise ProtocolError("Missing manifest supplement")
        if json.loads(encoded[len("module.exports = ") : -2]) != manifest.model_dump():
            raise ProtocolError("Manifest supplement mismatch")
    elif manifest.files or result.supplements:
        raise ProtocolError("Unsupported instrumentation supplied an overlay")
    return result


class InstrumentationClient(AnalysisClient):
    def instrument(self, request: InstrumentationRequest) -> InstrumentationResponse:
        # Reuse the strict T08 request validator for the common envelope.
        from robo_minerador.extraction.client import REQUEST_VALIDATOR

        data = request.model_dump(mode="json")
        original = {k: v for k, v in data.items() if k != "framework_version"}
        original["operation"] = "analyze_snapshot"
        if not REQUEST_VALIDATOR.is_valid(original):
            raise ProtocolError("Invalid instrumentation request")
        try:
            response = self._http.post("/v1/instrument", json=data)
        except httpx.HTTPError:
            raise AnalysisTransportError("Analyzer transport failed") from None
        try:
            value = response.json()
            if isinstance(value, dict) and value.get("status") == "error":
                raise AnalysisError(check_response(value, request.request_id).errors)
            result = check_instrumentation(value, request)
        except (ValueError, UnicodeError):
            raise ProtocolError("Invalid instrumentation JSON response") from None
        if response.status_code != 200:
            raise ProtocolError("Unexpected analyzer HTTP status")
        return result
