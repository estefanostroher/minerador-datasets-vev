from datetime import UTC, datetime, timedelta, timezone

import pytest

from robo_minerador.config import DiscoveryConfig
from robo_minerador.discovery.partitions import (
    PartitionLimit,
    Window,
    initial_partitions,
    split_window,
)


def config(**overrides: object) -> DiscoveryConfig:
    return DiscoveryConfig.model_validate(
        {"created_from": "2020-01-01", "created_until": "2020-01-02", **overrides}
    )


def test_scopes_and_inclusive_github_boundary() -> None:
    partitions = initial_partitions(config())
    assert len(partitions) == 10
    assert {p.license for p in partitions} == {
        "MIT",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "ISC",
    }
    for p in partitions:
        assert "created:2020-01-01T00:00:00Z..2020-01-01T23:59:59Z" in p.query
        assert "language:TypeScript" in p.query
        assert "is:public" in p.query
        assert "fork:true" in p.query
        assert f"archived:{str(p.archived).lower()}" in p.query
        assert "stars:" not in p.query
    excluded = initial_partitions(config(include_forks=False, include_archived=False))
    assert len(excluded) == 5
    assert all("fork:false" in p.query and "archived:false" in p.query for p in excluded)


def test_split_normalizes_utc_without_overlap_and_rejects_subseconds() -> None:
    start = datetime(2020, 1, 1, 1, tzinfo=timezone(timedelta(hours=1)))
    window = Window(start, start + timedelta(seconds=3))
    left, right = split_window(window)
    assert left.start == datetime(2020, 1, 1, tzinfo=UTC)
    assert left.end == right.start
    assert right.end == window.end
    assert (left.end - left.start).total_seconds() >= 1
    assert (right.end - right.start).total_seconds() >= 1
    with pytest.raises(PartitionLimit):
        split_window(left)
    with pytest.raises(ValueError):
        Window(start, start + timedelta(microseconds=500000))
    with pytest.raises(ValueError):
        Window(start.replace(tzinfo=None), start + timedelta(seconds=2))
