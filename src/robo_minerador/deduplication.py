"""Agrupa instâncias por igualdade integral das evidências publicadas."""

from copy import deepcopy
from datetime import datetime, timezone

from .deduplication_format import validate_deduplication_records
from .deduplication_representation import POLICY, build_representation, representation_digest


def _event(kind):
    """Cria os campos comuns de um evento da publicação."""
    return {"schema_version": 1, "stage": "deduplication", "record_type": kind,
            "timestamp": datetime.now(timezone.utc).isoformat()}


def deduplicate_instances(candidates: list[dict], source: dict) -> list[dict]:
    """Prepara e valida a publicação completa, confirmando igualdade por bytes."""
    ordered = sorted(candidates, key=lambda candidate: candidate["unit"]["instance_id"])
    identities = [candidate["unit"]["instance_id"] for candidate in ordered]
    if len(set(identities)) != len(identities):
        raise ValueError("Identidade de instância repetida.")
    records = [{**_event("run_started"), **deepcopy(source), "policy": deepcopy(POLICY)}]
    buckets = {}
    for candidate in ordered:
        unit = candidate["unit"]
        raw = build_representation(unit, candidate["files"])
        digest = representation_digest(raw)
        bucket = buckets.setdefault(digest, [])
        group = next((entry for entry in bucket if entry["raw"] == raw), None)
        if group is None:
            group = {"raw": raw, "representative": unit["instance_id"], "count": 0}
            bucket.append(group)
        group["count"] += 1
        representative = group["representative"] == unit["instance_id"]
        decision = "approved" if representative else "rejected"
        criterion = {"status": decision,
                     "code": "EXACT_REPRESENTATIVE" if representative else "EXACT_DUPLICATE",
                     "message": "Representante da representação exata." if representative else "Duplicata exata da instância representante."}
        records.append({**_event("instance"), **{key: unit[key] for key in
                       ("instance_id", "repository_id", "repository_url", "commit_sha")},
                        "decision": decision, "disposition": "representative" if representative else "duplicate",
                        "representative_instance_id": group["representative"], "representation_sha256": digest,
                        "representation_byte_count": len(raw), "criteria": {"exact_uniqueness": criterion}})
    groups = [group for bucket in buckets.values() for group in bucket]
    representatives = len(groups)
    duplicates = len(ordered) - representatives
    records.append({**_event("run_finished"),
                    "source_sha256": {name: source["sources"][name]["sha256"] for name in ("extraction", "validation")},
                    "eligible_count": len(ordered), "representative_count": representatives,
                    "duplicate_count": duplicates, "duplicate_group_count": sum(group["count"] >= 2 for group in groups),
                    "instance_decisions": {"approved": representatives, "rejected": duplicates, "not_evaluated": 0},
                    "status": "completed"})
    validate_deduplication_records(records)
    return records
