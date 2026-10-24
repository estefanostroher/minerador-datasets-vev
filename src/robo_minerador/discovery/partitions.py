"""Second-resolution, half-open UTC search scopes, independent of HTTP."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from robo_minerador.config import DiscoveryConfig
from robo_minerador.identity import stable_digest

LICENSES = ("MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC")
SECOND = timedelta(seconds=1)


class PartitionLimit(ValueError):
    """A one-second GitHub search window cannot be subdivided further."""


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.utcoffset() is None or self.end.utcoffset() is None:
            raise ValueError("window boundaries must be timezone-aware")
        object.__setattr__(self, "start", self.start.astimezone(UTC))
        object.__setattr__(self, "end", self.end.astimezone(UTC))
        if self.start.microsecond or self.end.microsecond or self.end - self.start < SECOND:
            raise ValueError("window requires whole-second boundaries and at least one second")


def split_window(window: Window) -> tuple[Window, Window]:
    seconds = int((window.end - window.start).total_seconds())
    if seconds < 2:
        raise PartitionLimit("minimum one-second search window reached")
    middle = window.start + timedelta(seconds=seconds // 2)
    return Window(window.start, middle), Window(middle, window.end)


def timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Partition:
    window: Window
    license: str
    archived: bool
    include_forks: bool

    @property
    def query(self) -> str:
        start, end = timestamp(self.window.start), timestamp(self.window.end - SECOND)
        return (
            f"is:public language:TypeScript license:{self.license} "
            f"archived:{str(self.archived).lower()} fork:{str(self.include_forks).lower()} "
            f"created:{start}..{end}"
        )

    @property
    def id(self) -> str:
        return stable_digest({"query": self.query})

    def children(self) -> tuple["Partition", "Partition"]:
        left, right = split_window(self.window)
        return (
            Partition(left, self.license, self.archived, self.include_forks),
            Partition(right, self.license, self.archived, self.include_forks),
        )


def initial_partitions(config: DiscoveryConfig) -> list[Partition]:
    window = Window(config.created_from, config.created_until)
    return [
        Partition(window, license, archived, config.include_forks)
        for license in LICENSES
        for archived in ((False, True) if config.include_archived else (False,))
    ]
