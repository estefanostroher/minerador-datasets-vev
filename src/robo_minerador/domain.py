"""Domain values; persistence envelopes belong to the state layer (T03)."""

from dataclasses import dataclass
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

CriterionStatus = Literal["approved", "rejected", "not_evaluated"]


@dataclass(frozen=True)
class Assessment:
    """A criterion decision with immutable evidence references.

    T03's persisted envelope adds rule_version, timestamp, stage and explanation.
    """

    entity_id: str
    criterion_id: str
    status: CriterionStatus
    reason_code: str
    evidence_ids: tuple[str, ...]


class Metric(BaseModel):
    """A finite measurement or an explicitly justified absence, never an implicit zero."""

    model_config = ConfigDict(extra="forbid")

    value: Annotated[float, Field(strict=True, allow_inf_nan=False)] | None
    availability: Literal["available", "unavailable", "not_applicable"]
    method: str
    reason_code: str | None

    @model_validator(mode="after")
    def _consistent_availability(self) -> Self:
        if self.availability == "available":
            if self.value is None:
                raise ValueError("available metric requires a finite numeric value")
        else:
            if self.value is not None:
                raise ValueError("absent metric requires a null value")
            if self.reason_code is None or not self.reason_code.strip():
                raise ValueError("absent metric requires a nonblank reason_code")
        return self
