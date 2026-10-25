"""Versioned, immutable runtime case and suite contracts for T11/T13.

Positions are original UTF-16 offsets, not Python string indices. Eligibility is
only a runtime prerequisite; it does not assert direct target association (T11).
"""

import hashlib
import json
from dataclasses import asdict, replace
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, TypeAdapter
from pydantic.dataclasses import dataclass

from robo_minerador.state.artifacts import ArtifactStore

_CONFIG = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


def case_identity(
    framework: str,
    file: str,
    declaration_position: int | None,
    title_path: tuple[str, ...],
    parameter_index: int | None,
    runtime_id: str = "",
) -> str:
    payload = [framework, file, declaration_position, title_path, parameter_index, runtime_id]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True, config=_CONFIG)
class CaseResult:
    framework: Literal["jest", "vitest"]
    framework_version: str
    file: str
    declaration_position: Annotated[int, Field(ge=0)] | None
    title_path: tuple[str, ...]
    parameter_index: Annotated[int, Field(ge=0)] | None
    runtime_id: str
    status: Literal["passed", "failed", "skipped", "pending", "todo", "not_executed"]
    duration_ms: Annotated[float, Field(ge=0)] | None
    expected_failure: bool
    mapping: Literal["trusted", "not_evaluated"]
    evidence_id: Digest
    schema_version: Literal["1.0"] = "1.0"

    def __post_init__(self) -> None:
        if (
            not self.file
            or "\\" in self.file
            or ":" in self.file
            or PurePosixPath(self.file).is_absolute()
            or any(p in ("", ".", "..") for p in self.file.split("/"))
        ):
            raise ValueError("unsafe case file")
        if self.mapping == "trusted" and self.declaration_position is None:
            raise ValueError("trusted mapping requires a declaration position")

    @property
    def identity(self) -> str:
        return case_identity(
            self.framework,
            self.file,
            self.declaration_position,
            self.title_path,
            self.parameter_index,
            self.runtime_id,
        )

    @property
    def eligible(self) -> bool:
        return (
            self.status == "passed"
            and not self.expected_failure
            and self.mapping == "trusted"
            and self.declaration_position is not None
        )


@dataclass(frozen=True, config=_CONFIG)
class SuiteResult:
    ordinal: Annotated[int, Field(ge=1)]
    outcome: Literal["passed", "failed", "interrupted"]
    exit_code: int | None
    evidence_id: Digest
    schema_version: Literal["1.0"] = "1.0"


@dataclass(frozen=True, config=_CONFIG)
class TransformEvidence:
    name: str
    version: str


@dataclass(frozen=True, config=_CONFIG)
class SupportEvidence:
    status: Literal["tested", "not_evaluated"]
    mode: str
    reason: str


@dataclass(frozen=True, config=_CONFIG)
class ScopeEvidence:
    argv: tuple[str, ...]
    observed_files: tuple[str, ...]
    configuration_evidence_id: Digest


@dataclass(frozen=True, config=_CONFIG)
class FrameworkReport:
    schema_version: Literal["1.0"]
    framework: Literal["jest", "vitest"]
    framework_version: str
    compiler_version: str
    transform: TransformEvidence
    cases: tuple[CaseResult, ...]
    evidence_id: Digest
    completeness: Literal["not_evaluated"]
    scope: Literal["unknown"]
    limitations: tuple[str, ...]
    support: SupportEvidence
    scope_evidence: ScopeEvidence
    normalized_evidence_id: str = ""
    # Older T10 envelopes remain readable, but absent trace support never admits T11.
    trace_support: SupportEvidence | None = None


def load_report(normalized: bytes, raw: bytes, artifacts: ArtifactStore) -> FrameworkReport:
    """Validate analyzer output, cross-language IDs, raw linkage; retain exact bytes.

    Raw bytes and normalized bytes live at ArtifactStore.root/<sha256>. A report
    references raw/config evidence; normalized_evidence_id references its envelope.
    Executor logs/workspace remain independently linked by ExecutionResult.
    """
    raw_id = artifacts.put(raw)
    value = json.loads(normalized)
    identities = [case.pop("identity") for case in value["cases"]]
    report = TypeAdapter(FrameworkReport).validate_json(json.dumps(value))
    if report.evidence_id != raw_id or report.scope_evidence.configuration_evidence_id != raw_id:
        raise ValueError("raw report evidence mismatch")
    metadata = json.loads(raw)
    if (
        report.framework != metadata.get("framework")
        or report.framework_version != metadata.get("version")
        or report.compiler_version != metadata.get("compiler_version")
        or asdict(report.transform) != metadata.get("transform")
    ):
        raise ValueError("raw report metadata mismatch")
    if len(set(identities)) != len(identities):
        raise ValueError("duplicate runtime case identities")
    for case, identity in zip(report.cases, identities, strict=True):
        if (
            case.identity != identity
            or case.evidence_id != raw_id
            or case.framework != report.framework
            or case.framework_version != report.framework_version
        ):
            raise ValueError("case identity/evidence mismatch")
    return replace(report, normalized_evidence_id=artifacts.put(normalized))
