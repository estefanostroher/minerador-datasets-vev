"""Parse collection parameters independently of runtime filesystem validation."""

import tomllib
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator


def _utc_datetime(value: object) -> datetime:
    """Interpret calendar dates as UTC and normalize explicitly zoned timestamps."""
    if isinstance(value, str):
        text = value
        try:
            value = date.fromisoformat(text)
        except ValueError:
            value = datetime.fromisoformat(text)
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise ValueError("datetime must include a timezone")
        return value.astimezone(UTC)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=UTC)
    raise ValueError("expected an ISO date or timezone-aware datetime")


def _nonempty_path(value: object) -> object:
    if isinstance(value, str) and not value.strip():
        raise ValueError("path must not be empty")
    return value


def _literal_integer(value: object) -> int:
    # Literal alone uses equality, which also accepts 3.0 and 4.0.
    if type(value) is not int:
        raise ValueError("methodological constants must be integers")
    return value


UtcDatetime = Annotated[datetime, BeforeValidator(_utc_datetime)]
ConfigPath = Annotated[Path, BeforeValidator(_nonempty_path)]
PositiveInteger = Annotated[int, Field(gt=0, strict=True)]
PositiveNumber = Annotated[float, Field(gt=0, strict=True, allow_inf_nan=False)]
Probability = Annotated[float, Field(gt=0, lt=1, strict=True, allow_inf_nan=False)]


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)


class DiscoveryConfig(_ConfigModel):
    created_from: UtcDatetime
    created_until: UtcDatetime
    include_forks: bool = True
    include_archived: bool = True
    api_version: str = Field(default="2026-03-10", pattern=r"^\d{4}-\d{2}-\d{2}$")
    per_page: PositiveInteger = Field(default=100, le=100)
    transport_attempts: PositiveInteger = 3
    max_wait_seconds: PositiveInteger = 900

    @model_validator(mode="after")
    def _ordered_interval(self) -> Self:
        if self.created_from >= self.created_until:
            raise ValueError("created_until must be later than created_from")
        return self


class ExecutionConfig(_ConfigModel):
    platform: str = Field(default="linux/amd64", min_length=1)
    parallel_repositories: PositiveInteger = 1
    cpus: PositiveNumber = 2
    memory_mb: PositiveInteger = 4096
    workspace_limit_mb: PositiveInteger = 10240
    install_timeout_seconds: PositiveInteger = 1200
    build_timeout_seconds: PositiveInteger = 600
    suite_timeout_seconds: PositiveInteger = 900
    suite_repetitions: Annotated[Literal[3], BeforeValidator(_literal_integer)] = 3
    minimum_valid_cases: Annotated[Literal[4], BeforeValidator(_literal_integer)] = 4


class ReviewConfig(_ConfigModel):
    confidence: Probability = 0.95
    margin: Probability = 0.05
    proportion: Probability = 0.5
    seed: int = Field(default=20261024, strict=True)


class MiningConfig(_ConfigModel):
    schema_version: Literal["1.0"] = "1.0"
    workspace: ConfigPath
    environment_catalog: ConfigPath
    methodology_files: list[ConfigPath] = Field(min_length=1)
    discovery: DiscoveryConfig
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)


def load_config(path: str | Path) -> MiningConfig:
    """Load TOML without requiring container-specific references on the host."""
    with Path(path).open("rb") as stream:
        return MiningConfig.model_validate(tomllib.load(stream))


def validate_local_references(config: MiningConfig) -> None:
    """Check required input files explicitly before starting a collection."""
    for path in (config.environment_catalog, *config.methodology_files):
        if not path.is_file():
            raise FileNotFoundError(f"configuration reference is not a file: {path}")
