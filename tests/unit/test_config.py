import copy
import tomllib
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from robo_minerador.config import (
    DiscoveryConfig,
    ExecutionConfig,
    MiningConfig,
    ReviewConfig,
    load_config,
    validate_local_references,
)

PILOT = Path(__file__).resolve().parents[2] / "config" / "pilot.toml"


def pilot_data():
    return tomllib.loads(PILOT.read_text(encoding="utf-8"))


def test_loads_complete_pilot_without_resolving_docker_paths():
    config = load_config(PILOT)
    assert config.schema_version == "1.0"
    assert config.workspace == Path("/workspace")
    assert config.environment_catalog == Path("/app/config/environments.lock.json")
    assert len(config.methodology_files) == 1
    assert config.discovery.created_from == datetime(2024, 1, 1, tzinfo=UTC)
    assert config.discovery.created_until == datetime(2024, 1, 2, tzinfo=UTC)
    assert config.discovery.include_forks is True
    assert config.discovery.include_archived is True
    assert config.discovery.api_version == "2026-03-10"
    assert config.discovery.per_page == 100
    assert config.discovery.transport_attempts == 3
    assert config.discovery.max_wait_seconds == 900
    assert config.execution.model_dump() == {
        "platform": "linux/amd64",
        "parallel_repositories": 1,
        "cpus": 2,
        "memory_mb": 4096,
        "workspace_limit_mb": 10240,
        "install_timeout_seconds": 1200,
        "build_timeout_seconds": 600,
        "suite_timeout_seconds": 900,
        "suite_repetitions": 3,
        "minimum_valid_cases": 4,
    }
    assert config.review.model_dump() == {
        "confidence": 0.95,
        "margin": 0.05,
        "proportion": 0.5,
        "seed": 20261024,
    }


@pytest.mark.parametrize(
    "field,value",
    [("suite_repetitions", value) for value in (2, 4, True, 3.0, "3")]
    + [("minimum_valid_cases", value) for value in (3, 5, False, 4.0, "4")],
)
def test_rejects_changed_or_coerced_methodological_constants(field, value):
    with pytest.raises(ValidationError):
        ExecutionConfig(**{field: value})


@pytest.mark.parametrize("section", [None, "discovery", "execution", "review"])
def test_rejects_unknown_fields_at_every_level(section):
    data = pilot_data()
    target = data if section is None else data[section]
    target["unexpected"] = 1
    with pytest.raises(ValidationError, match="extra_forbidden"):
        MiningConfig.model_validate(data)


@pytest.mark.parametrize("until", ["2023-12-31", "2024-01-01", "invalid", "2024-01-01T00:00:00Z"])
def test_rejects_invalid_or_non_increasing_dates(until):
    with pytest.raises(ValidationError):
        DiscoveryConfig(created_from="2024-01-01", created_until=until)


@pytest.mark.parametrize(
    "start",
    [
        "2024-01-01",
        date(2024, 1, 1),
        datetime(2024, 1, 1, tzinfo=UTC),
        "2024-01-01T03:00:00+03:00",
        datetime(2024, 1, 1, 3, tzinfo=timezone(timedelta(hours=3))),
    ],
)
def test_normalizes_date_and_aware_datetime_to_utc(start):
    config = DiscoveryConfig(created_from=start, created_until="2024-01-02")
    assert config.created_from == datetime(2024, 1, 1, tzinfo=UTC)
    assert config.created_from.tzinfo is UTC


@pytest.mark.parametrize("start", [datetime(2024, 1, 1), "2024-01-01T00:00:00", 1704067200, True])
def test_rejects_ambiguous_datetime_and_epoch_inputs(start):
    with pytest.raises(ValidationError):
        DiscoveryConfig(created_from=start, created_until="2024-01-02")


@pytest.mark.parametrize(
    "field",
    [
        "parallel_repositories",
        "cpus",
        "memory_mb",
        "workspace_limit_mb",
        "install_timeout_seconds",
        "build_timeout_seconds",
        "suite_timeout_seconds",
    ],
)
@pytest.mark.parametrize("value", [-1, 0, True])
def test_rejects_nonpositive_or_boolean_execution_resources(field, value):
    with pytest.raises(ValidationError):
        ExecutionConfig(**{field: value})


@pytest.mark.parametrize("field", ["per_page", "transport_attempts", "max_wait_seconds"])
@pytest.mark.parametrize("value", [-1, 0, True])
def test_rejects_invalid_discovery_resources(field, value):
    with pytest.raises(ValidationError):
        DiscoveryConfig(created_from="2024-01-01", created_until="2024-01-02", **{field: value})


def test_rejects_page_size_above_github_limit():
    with pytest.raises(ValidationError):
        DiscoveryConfig(created_from="2024-01-01", created_until="2024-01-02", per_page=101)


@pytest.mark.parametrize("field", ["confidence", "margin", "proportion"])
@pytest.mark.parametrize("value", [0, 1, -0.1, 1.1, float("nan"), float("inf"), True])
def test_rejects_invalid_review_parameters(field, value):
    with pytest.raises(ValidationError):
        ReviewConfig(**{field: value})


def test_rejects_unrecognized_population_size():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ReviewConfig(n=0)


@pytest.mark.parametrize(
    "field,value",
    [
        ("workspace", ""),
        ("environment_catalog", ""),
        ("methodology_files", []),
        ("methodology_files", [""]),
        ("schema_version", "2.0"),
    ],
)
def test_rejects_invalid_top_level_contract(field, value):
    data = pilot_data()
    data[field] = value
    with pytest.raises(ValidationError):
        MiningConfig.model_validate(data)


def test_checks_existing_references_explicitly(tmp_path):
    catalog = tmp_path / "environments.lock.json"
    methodology = tmp_path / "spec.md"
    catalog.write_text("{}", encoding="utf-8")
    methodology.write_text("spec", encoding="utf-8")
    data = pilot_data()
    data.update(environment_catalog=catalog, methodology_files=[methodology])
    validate_local_references(MiningConfig.model_validate(data))


@pytest.mark.parametrize("missing", ["catalog", "methodology", "directory", "second_methodology"])
def test_rejects_missing_or_non_file_references(tmp_path, missing):
    catalog = tmp_path / "catalog.json"
    methodology = tmp_path / "spec.md"
    catalog.write_text("{}", encoding="utf-8")
    methodology.write_text("spec", encoding="utf-8")
    data = copy.deepcopy(pilot_data())
    data.update(environment_catalog=catalog, methodology_files=[methodology])
    if missing == "catalog":
        catalog.unlink()
    elif missing == "methodology":
        methodology.unlink()
    elif missing == "directory":
        data["environment_catalog"] = tmp_path
    else:
        data["methodology_files"].append(tmp_path / "missing.md")
    with pytest.raises(FileNotFoundError):
        validate_local_references(MiningConfig.model_validate(data))


def test_load_config_reports_invalid_toml(tmp_path):
    path = tmp_path / "broken.toml"
    path.write_text("[broken", encoding="utf-8")
    with pytest.raises(tomllib.TOMLDecodeError):
        load_config(path)


def test_load_config_reports_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "missing.toml")


def test_load_config_validates_parsed_toml(tmp_path):
    path = tmp_path / "invalid.toml"
    path.write_text('schema_version = "2.0"', encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(path)
