"""Valida e lê o índice portátil sem inferir igualdade científica de hashes."""

from collections import Counter
from datetime import datetime
import re

from .deduplication_input import read_jsonl_bytes
from .deduplication_representation import POLICY, canonical_bytes


def _require(condition):
    """Recusa qualquer divergência estrutural da publicação."""
    if not condition:
        raise ValueError("Publicação de deduplicação inválida.")


def _hash(value, length=64):
    """Confere uma identidade hexadecimal canônica."""
    return type(value) is str and re.fullmatch(r"[0-9a-f]{%d}" % length, value) is not None


def _counts(value):
    """Confere contagens não negativas para os três estados."""
    return type(value) is dict and set(value) == {"approved", "rejected", "not_evaluated"} and all(
        type(number) is int and number >= 0 for number in value.values())


def _validate(records):
    """Confere protocolo, proveniência, referências e somas sem ler artefatos."""
    _require(type(records) is list and len(records) >= 2)
    common = {"schema_version", "stage", "record_type", "timestamp"}
    for index, row in enumerate(records):
        _require(type(row) is dict and type(row.get("schema_version")) is int and row["schema_version"] == 1
                 and row.get("stage") == "deduplication")
        expected = "run_started" if index == 0 else "run_finished" if index == len(records) - 1 else "instance"
        _require(row.get("record_type") == expected and type(row.get("timestamp")) is str)
        stamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
        _require(stamp.utcoffset() is not None and stamp.utcoffset().total_seconds() == 0)
    start, finish = records[0], records[-1]
    _require(set(start) == common | {"sources", "input_instance_count", "eligible_count", "input_decisions", "policy"})
    _require(type(start["policy"]) is dict and canonical_bytes(start["policy"]) == canonical_bytes(POLICY))
    sources = start["sources"]
    _require(type(sources) is dict and set(sources) == {"extraction", "validation"})
    for origin in sources.values():
        _require(type(origin) is dict and set(origin) == {"path", "sha256", "schema_version", "status"}
                 and type(origin["path"]) is str and bool(origin["path"]) and _hash(origin["sha256"])
                 and type(origin["schema_version"]) is int and origin["schema_version"] == 3
                 and origin["status"] in ("completed", "partial", "failed"))
    _require(all(type(start[key]) is int and start[key] >= 0 for key in ("input_instance_count", "eligible_count"))
             and _counts(start["input_decisions"]) and sum(start["input_decisions"].values()) == start["input_instance_count"]
             and start["input_decisions"]["approved"] == start["eligible_count"] == len(records) - 2)
    instance_fields = {"instance_id", "repository_id", "repository_url", "commit_sha", "decision", "disposition",
                       "representative_instance_id", "representation_sha256", "representation_byte_count", "criteria"}
    instances = records[1:-1]
    by_id = {}
    for row in instances:
        _require(set(row) == common | instance_fields and _hash(row["instance_id"]) and row["instance_id"] not in by_id
                 and type(row["repository_id"]) is int and row["repository_id"] > 0 and _hash(row["commit_sha"], 40)
                 and type(row["repository_url"]) is str and re.fullmatch(r"https://github\.com/[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", row["repository_url"]) is not None
                 and row["repository_url"].rsplit("/", 1)[-1] not in {".", ".."}
                 and _hash(row["representation_sha256"]) and type(row["representation_byte_count"]) is int
                 and row["representation_byte_count"] > 0 and _hash(row["representative_instance_id"]))
        representative = row["disposition"] == "representative"
        _require(row["disposition"] in ("representative", "duplicate")
                 and row["decision"] == ("approved" if representative else "rejected"))
        criteria = row["criteria"]
        _require(type(criteria) is dict and set(criteria) == {"exact_uniqueness"})
        criterion = criteria["exact_uniqueness"]
        _require(type(criterion) is dict and set(criterion) == {"status", "code", "message"}
                 and criterion["status"] == row["decision"]
                 and criterion["code"] == ("EXACT_REPRESENTATIVE" if representative else "EXACT_DUPLICATE")
                 and type(criterion["message"]) is str and bool(criterion["message"]))
        by_id[row["instance_id"]] = row
    _require(list(by_id) == sorted(by_id))
    groups = Counter()
    for row in instances:
        ref = row["representative_instance_id"]
        _require(ref in by_id)
        representative = by_id[ref]
        _require(representative["disposition"] == "representative" and representative["representative_instance_id"] == ref
                 and ref <= row["instance_id"] and (row["instance_id"] == ref) == (row["disposition"] == "representative")
                 and (row["representation_sha256"], row["representation_byte_count"]) ==
                     (representative["representation_sha256"], representative["representation_byte_count"]))
        groups[ref] += 1
    representative_count = len(groups)
    duplicate_count = len(instances) - representative_count
    _require(set(finish) == common | {"source_sha256", "eligible_count", "representative_count", "duplicate_count",
                                     "duplicate_group_count", "instance_decisions", "status"}
             and all(type(finish[key]) is int and finish[key] >= 0 for key in
                     ("eligible_count", "representative_count", "duplicate_count", "duplicate_group_count"))
             and finish["source_sha256"] == {name: origin["sha256"] for name, origin in sources.items()}
             and finish["eligible_count"] == len(instances) and finish["representative_count"] == representative_count
             and finish["duplicate_count"] == duplicate_count and finish["duplicate_group_count"] == sum(n >= 2 for n in groups.values())
             and _counts(finish["instance_decisions"]) and finish["instance_decisions"] ==
                 {"approved": representative_count, "rejected": duplicate_count, "not_evaluated": 0}
             and finish["status"] == "completed")
    return finish


def validate_deduplication_records(records) -> dict:
    """Retorna o fechamento ou ValueError uniforme para publicação malformada."""
    try:
        return _validate(records)
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError, ValueError) as error:
        raise ValueError("Publicação de deduplicação inválida.") from error


def load_deduplication(path) -> tuple[list[dict], dict]:
    """Lê JSONL estrito e retorna todas as instâncias e a abertura validada."""
    records, _ = read_jsonl_bytes(path)
    validate_deduplication_records(records)
    return records[1:-1], records[0]
