from dataclasses import FrozenInstanceError, asdict

import pytest
from pydantic import ValidationError

from robo_minerador.domain import Assessment, Metric


def test_assessment_preserves_decision_and_evidence_as_immutable_value():
    assessment = Assessment("function-1", "documented", "approved", "jsdoc_present", ("abc",))
    assert asdict(assessment) == {
        "entity_id": "function-1",
        "criterion_id": "documented",
        "status": "approved",
        "reason_code": "jsdoc_present",
        "evidence_ids": ("abc",),
    }
    with pytest.raises(FrozenInstanceError):
        assessment.status = "rejected"


@pytest.mark.parametrize("value", [0, 0.5, -2, 100])
def test_available_metric_preserves_numeric_values_including_zero(value):
    metric = Metric(value=value, availability="available", method="coverage", reason_code=None)
    assert metric.value == value
    assert Metric.model_validate_json(metric.model_dump_json()) == metric


@pytest.mark.parametrize("value", [None, True, "0", float("nan"), float("inf"), -float("inf")])
def test_available_metric_rejects_missing_non_numeric_or_nonfinite_values(value):
    with pytest.raises(ValidationError):
        Metric(value=value, availability="available", method="coverage", reason_code=None)


@pytest.mark.parametrize(
    "availability,reason",
    [
        ("unavailable", "report_missing"),
        ("not_applicable", "total_branches_zero"),
    ],
)
def test_absent_metrics_serialize_as_null_with_reason(availability, reason):
    metric = Metric(
        value=None, availability=availability, method="branch_coverage", reason_code=reason
    )
    assert metric.model_dump()["value"] is None
    assert metric.reason_code == reason
    assert Metric.model_validate_json(metric.model_dump_json()) == metric


@pytest.mark.parametrize("availability", ["unavailable", "not_applicable"])
@pytest.mark.parametrize("value,reason", [(0, "missing"), (None, None), (None, ""), (None, "  ")])
def test_absent_metric_requires_null_and_nonblank_reason(availability, value, reason):
    with pytest.raises(ValidationError):
        Metric(value=value, availability=availability, method="coverage", reason_code=reason)


def test_metric_rejects_unknown_availability_and_fields():
    with pytest.raises(ValidationError):
        Metric.model_validate(
            {"value": 0, "availability": "unknown", "method": "coverage", "reason_code": None}
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        Metric.model_validate(
            {
                "value": 0,
                "availability": "available",
                "method": "coverage",
                "reason_code": None,
                "stage": "execution",
            }
        )
