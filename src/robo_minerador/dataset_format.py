"""Leitor isolado do dataset; não reexecuta diagnósticos AST de recursos.

O diagnóstico global é preservado e conferido estruturalmente, sem novo replay
AST. O gate local de elegibilidade é rederivado integralmente em Python.
"""

from datetime import datetime
import hashlib
from pathlib import Path
import re

from .dataset import METRIC_POLICY
from .dataset_input import _license, _readme, _stars
from .deduplication_input import _check_approved, read_jsonl_bytes
from .deduplication_representation import POLICY, _snippet, build_representation, canonical_bytes
from .extraction_analyzer import CRITERIA, _snippet as _extraction_snippet
from .extraction_format import CONTEXT_POLICY, validate_snapshot_repository
from .validation_evaluation import PAIR_POLICY, _association
from .validation_evidence import _json_value
from .validation_format import _captures, _hash, _instance, _repository
from .validation_recipe import _path


def _require(condition):
    if not condition:
        raise ValueError("Registro de dataset inválido.")


def _dedup(row, identity, digest, count, representative):
    fields = {"schema_version", "stage", "record_type", "timestamp", "instance_id", "repository_id",
              "repository_url", "commit_sha", "decision", "disposition", "representative_instance_id",
              "representation_sha256", "representation_byte_count", "criteria"}
    _require(type(row) is dict and set(row) == fields and type(row["schema_version"]) is int
             and row["schema_version"] == 1 and row["stage"] == "deduplication" and row["record_type"] == "instance"
             and _hash(row["instance_id"]) and type(row["repository_id"]) is int and row["repository_id"] > 0
             and _hash(row["commit_sha"], 40) and type(row["repository_url"]) is str
             and re.fullmatch(r"https://github\.com/[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", row["repository_url"])
             and row["repository_url"].rsplit("/", 1)[-1] not in {".", ".."}
             and row["representative_instance_id"] == identity and row["instance_id"] >= identity
             and row["disposition"] == ("representative" if representative else "duplicate")
             and row["decision"] == ("approved" if representative else "rejected")
             and (row["instance_id"] == identity) == representative
             and row["representation_sha256"] == digest and type(row["representation_byte_count"]) is int
             and row["representation_byte_count"] == count)
    stamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
    _require(stamp.utcoffset() is not None and stamp.utcoffset().total_seconds() == 0)
    _require(type(row["criteria"]) is dict and set(row["criteria"]) == {"exact_uniqueness"})
    criterion = row["criteria"]["exact_uniqueness"]
    _require(type(criterion) is dict and set(criterion) == {"status", "code", "message"}
             and criterion["status"] == row["decision"] and criterion["code"] ==
             ("EXACT_REPRESENTATIVE" if representative else "EXACT_DUPLICATE")
             and type(criterion["message"]) is str and bool(criterion["message"]))


def _validate_row(row):
    _require(type(row) is dict and set(row) == {"schema_version", "instance_id", "language", "function", "tests",
             "documentation", "context", "metrics", "source", "environment", "validation", "deduplication", "provenance"}
             and type(row["schema_version"]) is int and row["schema_version"] == 1
             and row["language"] == "TypeScript" and _hash(row["instance_id"]))
    identity = row["instance_id"]
    source, context, validation = row["source"], row["context"], row["validation"]
    _require(type(source) is dict and set(source) == {"repository_id", "repository", "repository_url", "commit_sha", "license", "stars", "readme"}
             and type(source["repository_id"]) is int and source["repository_id"] > 0
             and type(source["repository"]) is str and bool(source["repository"])
             and source["repository_url"] == "https://github.com/" + source["repository"] and _hash(source["commit_sha"], 40))
    license = source["license"]
    _require(type(license) is dict and set(license) == {"spdx_id", "criterion"}
             and _license({"criteria": {"license": license["criterion"]}}) == license)
    for name, validator in (("stars", _stars), ("readme", _readme)):
        envelope = source[name]
        _require(type(envelope) is dict and set(envelope) == {"status", "value", "reason"}
                 and envelope == validator(envelope["value"]))
    _require(type(context) is dict and set(context) == {"files", "snapshot", "execution_requirements", "binary_policy"}
             and context["binary_policy"] == "references_only" and type(context["files"]) is list)
    files = {}
    for item in context["files"]:
        _require(type(item) is dict and set(item) == {"path", "content", "sha256"} and type(item["content"]) is str)
        _path(item["path"])
        _require(item["path"] not in files and item["sha256"] == hashlib.sha256(item["content"].encode("utf-8")).hexdigest())
        files[item["path"]] = item["content"]
    _require(list(files) == sorted(files))
    validate_snapshot_repository({"context_policy": CONTEXT_POLICY, "repository_id": source["repository_id"],
        "commit_sha": source["commit_sha"], "context": {"snapshot": context["snapshot"], "analysis_complete": True},
        "execution_requirements": context["execution_requirements"]})
    _require(type(validation) is dict and set(validation) == {"instance", "repository_evidence", "extraction_criteria"})
    criteria = validation["extraction_criteria"]
    _require(type(criteria) is dict and set(criteria) == set(CRITERIA) and all(type(c) is dict
             and set(c) == {"status", "reason"} and c["status"] == "approved"
             and type(c["reason"]) is str and bool(c["reason"]) for c in criteria.values()))
    repository, instance = validation["repository_evidence"], validation["instance"]
    _require(type(repository) is dict and type(instance) is dict)
    for rec, kind in ((repository, "repository"), (instance, "instance")):
        _require(type(rec.get("schema_version")) is int and rec["schema_version"] == 3
                 and rec.get("stage") == "validation" and rec.get("record_type") == kind
                 and type(rec.get("repository_id")) is int and rec["repository_id"] == source["repository_id"])
        stamp = datetime.fromisoformat(rec["timestamp"].replace("Z", "+00:00"))
        _require(stamp.utcoffset() is not None and stamp.utcoffset().total_seconds() == 0)
        _captures(rec)
    _require(repository["repository_url"] == source["repository_url"] and repository["commit_sha"] == source["commit_sha"]
             and repository["snapshot"] == context["snapshot"] and instance["instance_id"] == identity
             and instance["decision"] == "approved")
    _repository(repository, rederive_diagnostic_resources=False, resource_scope=PAIR_POLICY["resource_gate_scope"])
    _instance(instance, repository, PAIR_POLICY["resource_gate_scope"])
    _require(instance["criteria"]["minimum_valid_cases"]["code"] == "MINIMUM_FOUR_CASES"
             and instance["criteria"]["original_target_execution"]["code"] == "ORIGINAL_TARGET_WITNESSES")
    _require(type(row["environment"]) is dict and row["environment"] ==
             {k: repository[k] for k in ("recipe", "environment", "lockfiles", "execution")})
    function, tests, docs = row["function"], row["tests"], row["documentation"]
    _require(type(function) is dict and set(function) == {"kind", "name", "class_name", "path", "start_line", "end_line", "signature", "reference_code"}
             and function["kind"] in {"function_declaration", "class_method"} and type(function["name"]) is str and bool(function["name"])
             and type(function["signature"]) is str and bool(function["signature"]) and type(function["reference_code"]) is str
             and function["reference_code"].startswith(function["signature"])
             and ((function["kind"] == "function_declaration" and function["class_name"] is None) or
                  (function["kind"] == "class_method" and type(function["class_name"]) is str and bool(function["class_name"]))))
    _snippet(function, files)
    _require(type(tests) is list and bool(tests) and type(docs) is list and bool(docs))
    originals = []
    for test in tests:
        _require(type(test) is dict and set(test) in ({"path", "framework", "name", "start_line", "end_line", "parameterized", "modifiers", "association", "original_text"},
                 {"path", "framework", "name", "start_line", "end_line", "parameterized", "modifiers", "association", "original_text", "skipped"})
                 and test["framework"] in {"vitest", "jest", "mocha", "node:test"} and type(test["name"]) is str
                 and type(test["parameterized"]) is bool and ("skipped" not in test or type(test["skipped"]) is bool)
                 and type(test["modifiers"]) is list and all(type(v) is str for v in test["modifiers"])
                 and test["original_text"] == _snippet(test, files))
        association = test["association"]
        _require(type(association) is dict and set(association) == {"kind", "call_line", "expression", "resolved_target"}
                 and association["kind"] == "direct_call" and type(association["call_line"]) is int
                 and test["start_line"] <= association["call_line"] <= test["end_line"]
                 and type(association["expression"]) is str and bool(association["expression"])
                 and association["expression"] in _snippet({"path": test["path"], "start_line": association["call_line"], "end_line": association["call_line"]}, files)
                 and association["resolved_target"] == {"path": function["path"], "name": function["name"], "start_line": function["start_line"]})
        originals.append({k: v for k, v in test.items() if k != "original_text"})
    for doc in docs:
        _require(type(doc) is dict and set(doc) == {"kind", "path", "start_line", "end_line", "original_text", "association"}
                 and type(doc["original_text"]) is str and bool(doc["original_text"]))
        _extraction_snippet(doc, "original_text", files)
        association = doc["association"]
        _require((doc["kind"] == "attached_comment" and doc["path"] == function["path"] and association == {"kind": "attached"})
                 or (doc["kind"] == "external" and association in ({"kind": "source_link", "source_path": function["path"]},
                     {"kind": "explicit_reference", "target": f'{function["path"]}#{function["name"]}'})))
    unit = {"instance_id": identity, "function": function, "tests": originals, "documentation": docs, "context_paths": list(files)}
    _require([_association(test) for test in originals] == [a["test"] for a in instance["test_cases"]["associations"]])
    _check_approved(unit, files, instance, repository)
    metrics = row["metrics"]
    _require(type(metrics) is dict and set(metrics) == {"size", "cyclomatic_complexity", "test_cases", "coverage"}
             and metrics["test_cases"] == instance["test_cases"] and metrics["coverage"] == instance["coverage"])
    for key, number, method in (("size", "physical_lines", "original_declaration_physical_lines_v1"),
                                ("cyclomatic_complexity", "value", "typescript_ast_decisions_v1")):
        metric = metrics[key]
        _require(type(metric) is dict and set(metric) == {"status", number, "method", "reason"}
                 and metric["status"] == "available" and metric["method"] == method and metric["reason"] is None
                 and type(metric[number]) is int and metric[number] >= 1)
    _require(metrics["size"]["physical_lines"] == function["end_line"] - function["start_line"] + 1)
    dedup = row["deduplication"]
    _require(type(dedup) is dict and set(dedup) == {"policy", "representative", "duplicate_origins"}
             and canonical_bytes(dedup["policy"]) == canonical_bytes(POLICY) and type(dedup["duplicate_origins"]) is list)
    raw = build_representation(unit, files)
    digest = hashlib.sha256(raw).hexdigest()
    _dedup(dedup["representative"], identity, digest, len(raw), True)
    representative = dedup["representative"]
    _require(all(representative[k] == source[k] for k in ("repository_id", "repository_url", "commit_sha")))
    duplicates = dedup["duplicate_origins"]
    for duplicate in duplicates:
        _dedup(duplicate, identity, digest, len(raw), False)
    ids = [d["instance_id"] for d in duplicates]
    _require(ids == sorted(set(ids)))
    provenance = row["provenance"]
    _require(type(provenance) is dict and set(provenance) == {"sources", "builder", "metric_policy"}
             and canonical_bytes(provenance["metric_policy"]) == canonical_bytes(METRIC_POLICY))
    builder = provenance["builder"]
    _require(type(builder) is dict and set(builder) == {"name", "version", "policy"} and builder["name"] == "robo-minerador"
             and type(builder["version"]) is str and bool(builder["version"]) and builder["policy"] == "dataset_build_v1")
    sources = provenance["sources"]
    _require(type(sources) is dict and set(sources) == {"discovery", "triage", "extraction", "validation", "deduplication"})
    for name, origin in sources.items():
        _require(type(origin) is dict and set(origin) == {"path", "sha256", "status", "schema_version"}
                 and type(origin["path"]) is str and bool(origin["path"]) and _hash(origin["sha256"])
                 and origin["status"] in {"completed", "partial"}
                 and (origin["schema_version"] is None if name in {"discovery", "triage"} else
                      type(origin["schema_version"]) is int and origin["schema_version"] == (1 if name == "deduplication" else 3)))
    return ids


def validate_dataset_records(records: list[dict]) -> int:
    """Confere linhas isoladas, sem recuperar fontes ou reexecutar AST diagnóstica."""
    try:
        _require(type(records) is list)
        seen = set()
        previous = None
        shared = None
        for row in records:
            _json_value(row)
            duplicates = _validate_row(row)
            identity = row["instance_id"]
            _require((previous is None or previous < identity) and identity not in seen and not set(duplicates) & seen)
            seen.update([identity, *duplicates])
            previous = identity
            policy = canonical_bytes([row["provenance"], row["deduplication"]["policy"]])
            _require(shared is None or shared == policy)
            shared = policy
        return len(records)
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError, UnicodeError) as error:
        raise ValueError("Registro de dataset inválido.") from error


def load_dataset(path) -> list[dict]:
    """Lê JSONL estrito; zero bytes representa o conjunto vazio."""
    if Path(path).stat().st_size == 0:
        return []
    records, _ = read_jsonl_bytes(path)
    validate_dataset_records(records)
    return records
