"""One real T14 -> T16 offline replay, using existing Docker P3 evidence only."""

import json
from pathlib import Path
from typing import Any

import pytest
from test_eligibility_replay import p3_evidence as real_p3_evidence

from robo_minerador.dataset.deduplicate import DeduplicationManifest, DeduplicationService
from robo_minerador.extraction.service import load_extraction
from robo_minerador.state.store import StateStore

p3_evidence = real_p3_evidence


@pytest.mark.parametrize("p3_evidence", [0], indirect=True)
def test_real_eligibility_loader_owns_provenance_and_rejects_tampering(
    p3_evidence: Any,
) -> None:
    state, artifacts, eligibility, args, _, _, _ = p3_evidence
    run = args["run_id"]
    suites = state.suite_runs(args["repository"].task_id)
    eligible = eligibility.validate(**args)
    assert eligible.occurrence.accepted
    api = DeduplicationService(state, artifacts)
    result = api.deduplicate(run, [eligible.digest, eligible.digest])
    assert len(result.groups) == 1
    assert len(result.groups[0].occurrences) == 1
    assert result.groups[0].representative.eligible == eligible.occurrence
    manifest = DeduplicationManifest.model_validate_json(artifacts.get(result.digest))
    member = manifest.groups[0].occurrences[0]
    repository = next(r for r in state.repositories(run) if r["id"] == member.repository_id)
    assert member.external_repository_id == int(repository["external_id"])
    assert member.candidate_id == eligible.occurrence.candidate.id
    assert member.representative_id == member.id
    assert member.eligibility_digest == eligible.digest
    assert (
        artifacts.get(member.source_artifact_id)
        == eligible.occurrence.candidate.target.code.encode()
    )
    database = Path(state.database.connection.execute("PRAGMA database_list").fetchone()[2])
    with StateStore(database, artifacts=artifacts) as reopened:
        replay = DeduplicationService(reopened, artifacts)
        assert replay.load(run, result.digest) == result
        assert replay.deduplicate(run, [eligible.digest]) == result
        other_run = reopened.create_run("foreign-collection")
        with pytest.raises(ValueError, match="durable request"):
            replay.deduplicate(other_run, [eligible.digest])
        with pytest.raises(ValueError, match="belong"):
            replay.load(other_run, result.digest)
    forged = artifacts.put(
        eligible.occurrence.model_copy(update={"request_hash": "forged-but-accepted"})
        .model_dump_json()
        .encode()
    )
    with pytest.raises(ValueError, match="durable request"):
        api.deduplicate(run, [forged])
    extraction = load_extraction(artifacts, args["extraction_digest"])
    undocumented = next(
        c for c in extraction.candidates if c.target.qualified_name == "undocumented"
    )
    ineligible = eligibility.validate(
        **{**args, "candidate_id": undocumented.id, "associations": (), "coverage": None}
    )
    assert not ineligible.occurrence.accepted
    with pytest.raises(ValueError, match="ineligible"):
        api.deduplicate(run, [ineligible.digest])
    assert api.load(run, result.digest) == result
    # Alter SQL-owned assessment history in the isolated database, not original CAS.
    state.database.connection.execute(
        "INSERT INTO assessments SELECT 'tampered-extra-assessment', task_id, run_id, entity_id, "
        "criterion_id, status, reason_code, rule_version, stage, explanation, observed_at "
        "FROM assessments WHERE task_id = ? LIMIT 1",
        (eligible.task_id,),
    )
    with pytest.raises(ValueError, match="assessment history"):
        api.load(run, result.digest)
    assert state.suite_runs(args["repository"].task_id) == suites
    # Only a tiny runtime report; full proof/context remains referenced in CAS.
    (database.parent / "t16-replay.json").write_text(
        json.dumps(
            {
                "eligibility": eligible.digest,
                "deduplication": result.digest,
                "groups": len(result.groups),
                "suite_execution": "offline-no-execution",
            }
        ),
        encoding="utf-8",
    )
