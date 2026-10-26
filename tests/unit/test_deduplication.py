"""Text identity and durable representative selection, with readable synthetic unit inputs."""

import hashlib
import importlib
from collections.abc import Iterator
from dataclasses import replace
from itertools import permutations
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from robo_minerador.dataset.deduplicate import Occurrence
from robo_minerador.extraction.context import TargetSpan
from robo_minerador.extraction.service import (
    Candidate,
    DocumentEvidence,
    Evaluation,
    ExtractedTarget,
)
from robo_minerador.state.artifacts import ArtifactStore
from robo_minerador.state.store import DeduplicationCandidate, StateStore, StoredDuplicateGroup
from robo_minerador.validation.service import (
    DependencyReview,
    EligibilityResult,
    EligibleOccurrence,
)

CODE = "export function value(n: number) {\n  /* interno 😀 */ return n + 1;\n}"


def api() -> ModuleType:
    try:
        return importlib.import_module("robo_minerador.dataset.deduplicate")
    except ModuleNotFoundError:
        pytest.fail("T16 textual deduplication is missing")


def occurrence(
    repo: str = "repo-two",
    *,
    code: str = CODE,
    sha: str = "abc",
    path: str = "src/value.ts",
    start: int = 20,
    tag: str = "a",
) -> EligibleOccurrence:
    target = ExtractedTarget(
        id="target-" + tag,
        kind="function",
        qualified_name="value",
        path=path,
        start_offset=start,
        end_offset=start + len(code.encode("utf-16-le")) // 2,
        start_line=2,
        start_column=1,
        end_line=4,
        end_column=2,
        code=code,
        signature="export function value(n: number)",
    )
    span_code = "/** preceding docs " + tag + " */\n" + code
    candidate = Candidate(
        id="candidate-" + tag,
        target=target,
        span=TargetSpan(
            start_byte=0,
            end_byte=len(span_code.encode()),
            code=span_code,
            sha256=hashlib.sha256(span_code.encode()).hexdigest(),
        ),
        source_status=Evaluation(status="approved", reason="verified", evidence=()),
        associations=(),
        metrics=(),
        test_files=(tag + ".test.ts",),
        documents=(
            DocumentEvidence(
                symbol_target_id=target.id,
                status="approved",
                reason="docs-" + tag,
                evidence=[],
                sources=[],
            ),
        ),
        context_reference="context-" + tag,
    )
    return EligibleOccurrence(
        request_hash="request-" + tag,
        repository_id=repo,
        repository_sha=sha,
        extraction_digest="extraction-" + tag,
        candidate=candidate,
        context_reference="context-" + tag,
        prepared_context_reference=None,
        prepared_archive="archive-" + tag,
        prepared_evidence="prepared-" + tag,
        repository_evidence="repository-" + tag,
        original_executions=("execution-" + tag,),
        association_evidence=("association-" + tag,),
        case_ids_per_run=((tag,),) * 3,
        unresolved_case_ids_per_run=((),) * 3,
        dependency_review=DependencyReview(
            status="approved",
            reason="local",
            local_paths=(),
            configuration_paths=(),
            observed_attempts=(),
            limitations=(),
        ),
        assessments=(),
        accepted=True,
        coverage_evidence=None,
        limitations=(),
    )


def enriched(
    value: EligibleOccurrence,
    external_id: int = 2,
    digest: str | None = None,
) -> Occurrence:
    return Occurrence(external_id, digest or value.request_hash, value)


def test_identical_text_keeps_one_whole_representative_and_all_provenances() -> None:
    first = enriched(occurrence(tag="first"))
    other = enriched(occurrence("repo-ten", tag="other"), 10)
    groups = api().deduplicate([other, first, other])
    assert len(groups) == 1
    group = groups[0]
    assert group.representative is first
    assert group.occurrences == (first, other)
    assert group.code_hash == hashlib.sha256(CODE.encode("utf-8")).hexdigest()
    assert group.representative.eligible.candidate.documents[0].reason == "docs-first"
    assert group.representative.eligible.candidate.test_files == ("first.test.ts",)
    assert group.representative.eligible.prepared_archive == "archive-first"
    assert group.representative.eligible.case_ids_per_run == (("first",),) * 3


@pytest.mark.parametrize(
    "changed",
    [
        CODE.replace("n + 1", "n  + 1"),
        CODE.replace("\n", "\r\n"),
        CODE.replace("number", "any"),
        CODE.replace("value", "other"),
        CODE.replace("export ", ""),
        CODE.replace("interno", "documentação interna distinta"),
    ],
)
def test_no_whitespace_newline_signature_name_modifier_or_internal_comment_normalization(
    changed: str,
) -> None:
    assert (
        len(
            api().deduplicate(
                [
                    enriched(occurrence()),
                    enriched(occurrence("other", code=changed, tag="b"), 10),
                ]
            )
        )
        == 2
    )


def test_order_is_numeric_repository_then_sha_then_slash_path_then_offset() -> None:
    inputs = [
        enriched(occurrence("ten", sha="aaa", tag="ten"), 10),
        enriched(occurrence(sha="bbb", tag="sha")),
        enriched(occurrence(path="src/z.ts", tag="path")),
        enriched(occurrence(path="src\\a.ts", start=40, tag="late")),
        enriched(occurrence(path="src\\a.ts", start=10, tag="first")),
    ]
    expected = tuple(reversed(inputs))
    for ordered in permutations(inputs):
        group = api().deduplicate(ordered)[0]
        assert group.occurrences == expected
        assert group.representative.eligible.candidate.target.path == "src\\a.ts"


def test_hash_collision_requires_text_equality(monkeypatch: pytest.MonkeyPatch) -> None:
    module = api()
    monkeypatch.setattr(module, "code_digest", lambda _: "0" * 64)
    first = enriched(occurrence())
    second = enriched(occurrence("other", code=CODE + " ", tag="b"), 10)
    assert [g.occurrences for g in module.deduplicate([second, first])] == [(first,), (second,)]


def test_repeated_identity_with_conflicting_payload_is_rejected_in_both_orders() -> None:
    first = enriched(occurrence())
    second = enriched(occurrence(code=CODE + " "))
    for inputs in ([first, second], [second, first]):
        with pytest.raises(ValueError, match="conflicting"):
            api().deduplicate(inputs)


def test_ineligible_inputs_and_empty_collection() -> None:
    assert api().deduplicate([]) == []
    with pytest.raises(ValueError, match="ineligible"):
        api().deduplicate([enriched(occurrence().model_copy(update={"accepted": False}))])


class VerifiedLoader:
    """Unit port; the production loader is covered by the real P3 integration test."""

    def __init__(self, run: str, values: dict[str, EligibleOccurrence]) -> None:
        self.run, self.values = run, values
        self.calls: list[str] = []

    def load(self, run_id: str, digest: str) -> EligibilityResult:
        assert run_id == self.run
        self.calls.append(digest)
        return EligibilityResult("eligibility-" + digest, digest, self.values[digest])


Persistent = tuple[StateStore, ArtifactStore, str, VerifiedLoader]


@pytest.fixture
def persistent(tmp_path: Path) -> Iterator[Persistent]:
    artifacts = ArtifactStore(tmp_path / "cas")
    state = StateStore(tmp_path / "state.sqlite", artifacts=artifacts)
    run = state.create_run("deduplication-unit")
    two = state.upsert_repository(run, 2, "owner/two", "abc")
    ten = state.upsert_repository(run, 10, "owner/ten", "abc")
    values = {}
    for value in (
        occurrence(two),
        occurrence(ten, tag="b"),
        occurrence(ten, code=CODE + " ", path="src/other.ts", tag="c"),
    ):
        values[artifacts.put(value.model_dump_json().encode())] = value
    loader = VerifiedLoader(run, values)
    yield state, artifacts, run, loader
    state.close()


def test_persistence_collision_reopen_replay_and_frozen_population(
    persistent: Persistent,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, artifacts, run, loader = persistent
    module = api()
    monkeypatch.setattr(module, "code_digest", lambda _: "0" * 64)
    refs = list(loader.values)
    service = module.DeduplicationService(state, artifacts, eligibility=loader)
    result = service.deduplicate(run, refs)
    assert len(result.groups) == 2
    assert len(result.groups[0].occurrences) == 2
    assert result.groups[0].representative.external_repository_id == 2
    rows = state.database.connection.execute("SELECT * FROM duplicate_groups").fetchall()
    assert len(rows) == 2
    assert len({r["fingerprint"] for r in rows}) == 2
    candidates = state.database.connection.execute("SELECT * FROM candidates").fetchall()
    assert len(candidates) == 3
    assert len({r["duplicate_group_id"] for r in candidates}) == 2
    assert all(
        artifacts.get(r["source_artifact_id"]).decode() in (CODE, CODE + " ") for r in candidates
    )
    manifest = module.DeduplicationManifest.model_validate_json(artifacts.get(result.digest))
    merged = manifest.groups[0]
    assert {member.candidate_id for member in merged.occurrences} == {"candidate-a", "candidate-b"}
    assert all(
        member.representative_id == merged.representative_id for member in merged.occurrences
    )
    before = state.events(run)
    state.close()
    artifacts = ArtifactStore(tmp_path / "cas")
    with StateStore(tmp_path / "state.sqlite", artifacts=artifacts) as reopened:
        service = module.DeduplicationService(reopened, artifacts, eligibility=loader)
        assert service.load(run, result.digest) == result
        assert service.deduplicate(run, [*reversed(refs), refs[0]]) == result
        assert reopened.events(run) == before
        with pytest.raises(ValueError, match="immutable"):
            service.deduplicate(run, refs[:1])
    assert set(loader.calls) == set(refs)


def test_atomic_crash_leaves_no_partial_group_or_task(
    persistent: Persistent,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, artifacts, run, loader = persistent
    service = api().DeduplicationService(state, artifacts, eligibility=loader)
    original = state.finish_task

    def crash(*args: Any, **kwargs: Any) -> None:
        raise KeyboardInterrupt("publication crash")

    monkeypatch.setattr(state, "finish_task", crash)
    with pytest.raises(KeyboardInterrupt):
        service.deduplicate(run, list(loader.values))
    for table in ("duplicate_groups", "candidates", "tasks"):
        assert state.database.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    monkeypatch.setattr(state, "finish_task", original)
    state.close()
    with StateStore(tmp_path / "state.sqlite", artifacts=artifacts) as reopened:
        recovered = api().DeduplicationService(reopened, artifacts, eligibility=loader)
        assert len(recovered.deduplicate(run, list(loader.values)).groups) == 2


def test_empty_manifest_is_durable_and_input_population_cannot_grow(persistent: Persistent) -> None:
    state, artifacts, run, loader = persistent
    service = api().DeduplicationService(state, artifacts, eligibility=loader)
    result = service.deduplicate(run, [])
    assert result.groups == ()
    assert service.load(run, result.digest) == result
    with pytest.raises(ValueError, match="immutable"):
        service.deduplicate(run, list(loader.values))


def test_replay_detects_modified_sql_group_mapping_and_source_bytes(persistent: Persistent) -> None:
    state, artifacts, run, loader = persistent
    service = api().DeduplicationService(state, artifacts, eligibility=loader)
    result = service.deduplicate(run, list(loader.values))
    row = state.database.connection.execute("SELECT * FROM candidates LIMIT 1").fetchone()
    state.database.connection.execute(
        "UPDATE candidates SET duplicate_group_id = NULL WHERE id = ?", (row["id"],)
    )
    with pytest.raises(ValueError, match="mapping"):
        service.load(run, result.digest)
    state.database.connection.execute(
        "UPDATE candidates SET duplicate_group_id = ? WHERE id = ?",
        (row["duplicate_group_id"], row["id"]),
    )
    (artifacts.root / row["source_artifact_id"]).write_bytes(b"tampered source")
    with pytest.raises(ValueError, match="digest mismatch"):
        service.load(run, result.digest)


def test_service_rejects_ineligible_or_wrong_repository_without_publishing(
    persistent: Persistent,
) -> None:
    state, artifacts, run, loader = persistent
    ref = next(iter(loader.values))
    original = loader.values[ref]
    for update in ({"accepted": False}, {"repository_id": "another-run-repo"}):
        loader.values[ref] = original.model_copy(update=update)
        with pytest.raises(ValueError):
            api().DeduplicationService(state, artifacts, eligibility=loader).deduplicate(run, [ref])
    assert state.database.connection.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0


def test_empty_eligible_population_ignores_unselected_candidates(persistent: Persistent) -> None:
    state, artifacts, run, loader = persistent
    value = next(iter(loader.values.values()))
    state.record_duplicate_candidate(
        DeduplicationCandidate(
            "unselected",
            run,
            value.repository_id,
            value.repository_sha,
            "unselected.ts",
            0,
            10,
            "unselected",
            None,
            None,
        )
    )
    service = api().DeduplicationService(state, artifacts, eligibility=loader)
    result = service.deduplicate(run, [])
    assert service.load(run, result.digest) == result


def test_persisted_mapping_cannot_be_reassigned_through_state_api(persistent: Persistent) -> None:
    state, artifacts, run, loader = persistent
    api().DeduplicationService(state, artifacts, eligibility=loader).deduplicate(run, loader.values)
    group = state.database.connection.execute("SELECT * FROM duplicate_groups LIMIT 1").fetchone()
    with pytest.raises(ValueError, match="immutable"):
        state.record_duplicate_group(
            replace(StoredDuplicateGroup(**dict(group)), fingerprint="other")
        )
    row = state.database.connection.execute("SELECT * FROM candidates LIMIT 1").fetchone()
    with pytest.raises(ValueError, match="immutable"):
        state.record_duplicate_candidate(
            replace(DeduplicationCandidate(**dict(row)), duplicate_group_id=None)
        )


@pytest.mark.parametrize("reverse", [False, True])
def test_persistence_rejects_conflicting_position_before_publication(
    persistent: Persistent,
    reverse: bool,
) -> None:
    state, artifacts, run, loader = persistent
    first_ref = next(iter(loader.values))
    value = loader.values[first_ref]
    different_target = value.candidate.target.model_copy(update={"code": CODE + " "})
    conflict = value.model_copy(
        update={
            "candidate": value.candidate.model_copy(update={"target": different_target}),
        }
    )
    conflict_ref = artifacts.put(conflict.model_dump_json().encode())
    loader.values[conflict_ref] = conflict
    refs = [first_ref, conflict_ref]
    with pytest.raises(ValueError, match="conflicting"):
        api().DeduplicationService(state, artifacts, eligibility=loader).deduplicate(
            run,
            list(reversed(refs)) if reverse else refs,
        )
    assert (
        state.database.connection.execute("SELECT count(*) FROM duplicate_groups").fetchone()[0]
        == 0
    )
