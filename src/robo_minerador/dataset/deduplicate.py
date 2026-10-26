"""T16 exact textual deduplication and immutable, replayable collection membership.

The pure core accepts enriched T14 occurrences. Durable entry points always use
EligibilityService.load; an accepted flag supplied by a caller is not evidence.
Only candidate.target.code participates in comparison, with no source rewriting.
"""

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, Protocol

from robo_minerador.extraction.context import FrozenModel
from robo_minerador.identity import stable_digest
from robo_minerador.state.artifacts import ArtifactStore
from robo_minerador.state.store import DeduplicationCandidate, StateStore, StoredDuplicateGroup
from robo_minerador.validation.service import (
    EligibilityResult,
    EligibilityService,
    EligibleOccurrence,
)

METHOD = "textual-sha256-T16-v1"
STAGE = "deduplication:" + METHOD


@dataclass(frozen=True)
class Occurrence:
    external_repository_id: int
    eligibility_digest: str
    eligible: EligibleOccurrence


@dataclass(frozen=True)
class DuplicateGroup:
    representative: Occurrence
    occurrences: tuple[Occurrence, ...]
    code_hash: str


def code_digest(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def source_order(source: Occurrence) -> tuple[int, str, str, int]:
    value = source.eligible
    return (
        source.external_repository_id,
        value.repository_sha,
        value.candidate.target.path.replace("\\", "/"),
        value.candidate.target.start_offset,
    )


def _identity(source: Occurrence) -> tuple[str, str, str, int]:
    return (source.eligible.repository_id, *source_order(source)[1:])


def deduplicate(eligible_occurrences: Iterable[Occurrence]) -> list[DuplicateGroup]:
    """Group hash buckets by confirmed text equality; keep each complete origin.

    Repeating an identical input is idempotent. A conflicting payload for the same
    source position is invalid rather than allowing iteration order to choose it.
    """
    unique: dict[tuple[str, str, str, int], Occurrence] = {}
    for source in eligible_occurrences:
        if not source.eligible.accepted:
            raise ValueError("ineligible occurrence cannot enter deduplication")
        if type(source.external_repository_id) is not int or source.external_repository_id < 1:
            raise ValueError("external repository ID must be a positive integer")
        key = _identity(source)
        if key in unique and unique[key] != source:
            raise ValueError("conflicting occurrence at the same source identity")
        unique[key] = source
    buckets: dict[str, dict[str, list[Occurrence]]] = {}
    for source in sorted(unique.values(), key=source_order):
        code = source.eligible.candidate.target.code
        # String-key equality confirms the text even when SHA-256 is colliding.
        buckets.setdefault(code_digest(code), {}).setdefault(code, []).append(source)
    groups = [
        DuplicateGroup(members[0], tuple(members), digest)
        for digest, texts in buckets.items()
        for members in texts.values()
    ]
    return sorted(groups, key=lambda group: source_order(group.representative))


class EligibilityLoader(Protocol):
    def load(self, run_id: str, digest: str) -> EligibilityResult: ...


class Member(FrozenModel):
    """Provenance and explicit removed-to-representative link; payload stays in T14 CAS."""

    id: str
    candidate_id: str
    eligibility_digest: str
    external_repository_id: int
    representative_id: str
    repository_id: str
    commit_sha: str
    path: str
    start_offset: int
    end_offset: int
    symbol_name: str
    source_artifact_id: str

    def sql_candidate(self, run_id: str, group_id: str) -> DeduplicationCandidate:
        return DeduplicationCandidate(
            self.id,
            run_id,
            self.repository_id,
            self.commit_sha,
            self.path,
            self.start_offset,
            self.end_offset,
            self.symbol_name,
            self.source_artifact_id,
            group_id,
        )


class GroupManifest(FrozenModel):
    id: str
    fingerprint: str
    code_hash: str
    representative_id: str
    occurrences: tuple[Member, ...]


class DeduplicationManifest(FrozenModel):
    kind: Literal["deduplication"] = "deduplication"
    schema_version: Literal["1.0"] = "1.0"
    method: Literal["textual-sha256-T16-v1"] = "textual-sha256-T16-v1"
    run_id: str
    inputs: tuple[str, ...]
    groups: tuple[GroupManifest, ...]


@dataclass(frozen=True)
class DeduplicationResult:
    task_id: str
    digest: str
    groups: tuple[DuplicateGroup, ...]


class DeduplicationService:
    def __init__(
        self,
        state: StateStore,
        artifacts: ArtifactStore,
        *,
        eligibility: EligibilityLoader | None = None,
    ) -> None:
        self.state, self.artifacts = state, artifacts
        self.eligibility = (
            eligibility if eligibility is not None else EligibilityService(state, artifacts)
        )

    def _groups(self, run_id: str, digests: Iterable[str]) -> tuple[DuplicateGroup, ...]:
        self.state.get_run(run_id)
        repositories = {str(r["id"]): r for r in self.state.repositories(run_id)}
        sources = []
        for digest in sorted(set(digests)):
            result = self.eligibility.load(run_id, digest)
            value = result.occurrence
            row = repositories.get(value.repository_id)
            if row is None or row["commit_sha"] != value.repository_sha:
                raise ValueError("eligible repository differs from collection identity/commit")
            sources.append(Occurrence(int(str(row["external_id"])), digest, value))
        return tuple(deduplicate(sources))

    def _manifest(self, run_id: str, groups: tuple[DuplicateGroup, ...]) -> DeduplicationManifest:
        # Ordinals distinguish colliding texts without changing the public hash or
        # depending on a second hash. Frozen population prevents later remapping.
        texts: dict[str, list[str]] = {}
        for group in groups:
            texts.setdefault(group.code_hash, []).append(
                group.representative.eligible.candidate.target.code
            )
        for codes in texts.values():
            codes.sort()
        records = []
        for group in groups:
            code = group.representative.eligible.candidate.target.code
            fingerprint = f"{group.code_hash}:{texts[group.code_hash].index(code)}"
            group_id = stable_digest({"run": run_id, "method": METHOD, "fingerprint": fingerprint})
            representative_id = self._candidate_id(run_id, group.representative)
            members = []
            for source in group.occurrences:
                value = source.eligible
                target = value.candidate.target
                members.append(
                    Member(
                        id=self._candidate_id(run_id, source),
                        candidate_id=value.candidate.id,
                        eligibility_digest=source.eligibility_digest,
                        external_repository_id=source.external_repository_id,
                        representative_id=representative_id,
                        repository_id=value.repository_id,
                        commit_sha=value.repository_sha,
                        path=target.path,
                        start_offset=target.start_offset,
                        end_offset=target.end_offset,
                        symbol_name=target.qualified_name,
                        source_artifact_id=hashlib.sha256(target.code.encode("utf-8")).hexdigest(),
                    )
                )
            records.append(
                GroupManifest(
                    id=group_id,
                    fingerprint=fingerprint,
                    code_hash=group.code_hash,
                    representative_id=representative_id,
                    occurrences=tuple(members),
                )
            )
        return DeduplicationManifest(
            run_id=run_id,
            inputs=tuple(
                sorted(
                    source.eligibility_digest for group in groups for source in group.occurrences
                )
            ),
            groups=tuple(records),
        )

    @staticmethod
    def _candidate_id(run_id: str, source: Occurrence) -> str:
        # Candidate IDs in extraction may be shared across collections; SQL IDs
        # must also be run-scoped. The original candidate ID remains in provenance.
        return stable_digest({"run": run_id, "source": _identity(source)})

    def _task(self, run_id: str) -> str | None:
        for event in self.state.events(run_id):
            if event.task_id and self.state.get_task(event.task_id).stage == STAGE:
                return event.task_id
        return None

    def deduplicate(self, run_id: str, eligibility_digests: Iterable[str]) -> DeduplicationResult:
        groups = self._groups(run_id, eligibility_digests)
        manifest = self._manifest(run_id, groups)
        with self.state.transaction():
            previous = self._task(run_id)
            if previous is not None:
                events = self.state.task_checkpoints(previous)
                if len(events) != 1:
                    raise ValueError("deduplication lacks its immutable manifest")
                digest = str(events[0].payload["digest"])
                saved = DeduplicationManifest.model_validate_json(self.artifacts.get(digest))
                if saved.inputs != manifest.inputs:
                    raise ValueError(
                        "deduplication population is immutable; create a new collection"
                    )
                return self.load(run_id, digest)
            # Bytes may survive rollback as unreferenced CAS objects; SQL publishes
            # groups, every membership, and the completed task in ONE transaction.
            for group in groups:
                for source in group.occurrences:
                    digest = self.artifacts.put(
                        source.eligible.candidate.target.code.encode("utf-8")
                    )
                    self.state.register_artifact(digest)
                    self.state.register_artifact(source.eligibility_digest)
            digest = self.artifacts.put(manifest.model_dump_json().encode())
            self.state.register_artifact(digest)
            task = self.state.start_task(run_id, run_id, STAGE)
            for record in manifest.groups:
                self.state.record_duplicate_group(
                    StoredDuplicateGroup(
                        record.id,
                        run_id,
                        METHOD,
                        record.fingerprint,
                    )
                )
                for member in record.occurrences:
                    self.state.record_duplicate_candidate(member.sql_candidate(run_id, record.id))
            self.state.record_task_checkpoint(task, "deduplication", digest)
            self.state.finish_task(task, "completed", evidence_ids=(digest,))
        return DeduplicationResult(task, digest, groups)

    def load(self, run_id: str, digest: str) -> DeduplicationResult:
        """Reverify T14 evidence, exact bytes, representative and all SQL memberships."""
        saved = DeduplicationManifest.model_validate_json(self.artifacts.get(digest))
        task = self._task(run_id)
        if saved.run_id != run_id or task is None:
            raise ValueError("deduplication manifest does not belong to this run")
        checkpoints = self.state.task_checkpoints(task)
        if (
            self.state.get_task(task).outcome != "completed"
            or len(checkpoints) != 1
            or checkpoints[0].payload["digest"] != digest
            or digest not in self.state.task_evidence(task)
        ):
            raise ValueError("deduplication manifest lacks durable completed ownership")
        groups = self._groups(run_id, saved.inputs)
        if self._manifest(run_id, groups) != saved:
            raise ValueError("deduplication manifest differs from verified provenance replay")
        expected_groups = []
        expected_candidates = []
        for record in saved.groups:
            expected_groups.append(
                StoredDuplicateGroup(record.id, run_id, METHOD, record.fingerprint)
            )
            for member in record.occurrences:
                content = self.artifacts.get(member.source_artifact_id)
                source = next(
                    s
                    for g in groups
                    for s in g.occurrences
                    if s.eligibility_digest == member.eligibility_digest
                )
                if content != source.eligible.candidate.target.code.encode("utf-8"):
                    raise ValueError("deduplication source bytes differ from verified target")
                expected_candidates.append(member.sql_candidate(run_id, record.id))
        actual_groups, actual_candidates = self.state.deduplication_rows(
            run_id,
            METHOD,
            tuple(member.id for member in expected_candidates),
        )
        if sorted(actual_groups, key=lambda r: r.id) != sorted(
            expected_groups, key=lambda r: r.id
        ) or sorted(actual_candidates, key=lambda r: r.id) != sorted(
            expected_candidates, key=lambda r: r.id
        ):
            raise ValueError("deduplication SQL mapping differs from immutable manifest")
        return DeduplicationResult(task, digest, groups)
