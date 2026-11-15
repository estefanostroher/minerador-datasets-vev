"""Confere a cadeia de publicações e recupera representantes e proveniência."""

from collections import defaultdict
import hashlib
from pathlib import Path, PurePosixPath
import re

from .dataset_metrics import DatasetToolError
from .deduplication import deduplicate_instances
from .deduplication_format import validate_deduplication_records
from .deduplication_input import BindingVerificationUnavailable, load_deduplication_inputs, read_jsonl_bytes
from .extraction_input import load_triage
from .triage import load_discovery
from .triage_checks import ALLOWED_LICENSES, SAMPLE_LIMIT
from .triage_snapshot import is_license_path, is_relevant
from .validation_format import validate_output_records
from .validation_input import load_extraction


def _require(condition):
    """Recusa publicações ou vínculos incompatíveis."""
    if not condition:
        raise ValueError("Cadeia de publicações do dataset inconsistente.")


def _identity(row):
    """Compara SHAs legados sem alterar a evidência original."""
    sha = row["commit_sha"]
    return row["repository_url"], sha.lower() if sha is not None else None


def _envelope(value, reason):
    """Distingue ausência de valores falsos efetivamente observados."""
    return {"status": "available" if value is not None else "unavailable",
            "value": value, "reason": None if value is not None else reason}


def _sample(value):
    """Confere contagem e amostra publicada pela triagem, inclusive legado."""
    _require(type(value) is dict and ("count" in value) != ("total" in value))
    count = value.get("count", value.get("total"))
    paths = value.get("paths")
    _require(type(count) is int and count >= 0 and type(paths) is list
             and all(type(path) is str and bool(path) and "\x00" not in path for path in paths))
    _require(len(paths) == min(count, SAMPLE_LIMIT) and paths == sorted(set(paths)))
    return paths, count


def _stars(value):
    """Valida contagem observada sem confundir zero e ausência."""
    _require(value is None or (type(value) is int and value >= 0))
    return _envelope(value, "Estrelas não disponíveis na descoberta.")


def _readme(value):
    """Valida presença e amostra sem alterar o objeto publicado."""
    if value is not None:
        paths, count = _sample(value)
        _require(type(value.get("present")) is bool and value["present"] == (count > 0)
                 and all(is_relevant(path) and re.match(r"^readme(?:\..*)?$", PurePosixPath(path).name, re.I)
                         for path in paths))
    return _envelope(value, "README não avaliado na triagem.")


def _license(row):
    """Exige licença aprovada e evidência identificável da triagem."""
    criterion = row["criteria"]["license"]
    evidence = criterion["evidence"]
    spdx = evidence.get("spdx_id")
    _require(criterion["status"] == "approved" and spdx in ALLOWED_LICENSES
             and isinstance(evidence.get("path"), str) and bool(evidence["path"])
             and isinstance(evidence.get("blob_sha"), str)
             and re.fullmatch(r"[a-fA-F0-9]{40}", evidence["blob_sha"]) is not None)
    paths, count = _sample(evidence.get("candidates"))
    _require(all(is_relevant(path) and is_license_path(path) for path in paths))
    if count == len(paths) and is_relevant(evidence["path"]) and is_license_path(evidence["path"]):
        _require(evidence["path"] in paths)
    return {"spdx_id": spdx, "criterion": criterion}


def load_dataset_inputs(discovery_path, triage_path, extraction_path, validation_path, deduplication_path) -> dict:
    """Valida as cinco origens antes de selecionar representantes locais."""
    names = ("discovery", "triage", "extraction", "validation", "deduplication")
    paths = dict(zip(names, map(Path, (discovery_path, triage_path, extraction_path,
                                       validation_path, deduplication_path))))
    publications = {name: read_jsonl_bytes(path) for name, path in paths.items()}
    rows = {name: value[0] for name, value in publications.items()}
    hashes = {name: hashlib.sha256(value[1]).hexdigest() for name, value in publications.items()}
    _require(all(len(value) >= 2 and all(type(row) is dict for row in value) for value in rows.values()))
    events = {"run_started", "run_finished", "incomplete_results", "search_saturated", "api_error", "sha_unavailable"}
    _require(all(row.get("event") in events for row in rows["discovery"] if row.get("record_type") == "diagnostic"))
    discovered, discovery_source = load_discovery(paths["discovery"])
    eligible, triage_source = load_triage(paths["triage"])
    _, extraction_source = load_extraction(paths["extraction"])
    _require(all(source["sha256"] == hashes[name] for name, source in
                 (("discovery", discovery_source), ("triage", triage_source), ("extraction", extraction_source))))
    validation_finish = validate_output_records(rows["validation"])
    validate_deduplication_records(rows["deduplication"])
    instances, dedup_start = rows["deduplication"][1:-1], rows["deduplication"][0]
    triage_start, extraction_start = rows["triage"][0], rows["extraction"][0]
    _require(triage_start.get("input_sha256") == hashes["discovery"]
             and triage_start.get("input_status") == discovery_source["status"]
             and triage_start.get("input_count") == len(discovered))
    _require(extraction_start.get("input_sha256") == hashes["triage"]
             and extraction_start.get("input_status") == triage_source["status"]
             and extraction_start.get("input_count") == triage_source["input_count"]
             and extraction_start.get("eligible_count") == triage_source["eligible_count"])
    validation_source = rows["validation"][0]["source"]
    _require(all(validation_source.get(key) == extraction_source[key] for key in
                 ("sha256", "schema_version", "status", "input_count", "repository_count",
                  "eligible_count", "eligible_repository_count", "decisions")))
    for name, status in (("extraction", extraction_source["status"]), ("validation", validation_finish["status"])):
        origin = dedup_start["sources"][name]
        _require(origin["sha256"] == hashes[name] and origin["schema_version"] == 3 and origin["status"] == status)
    discovery_by_id = {row["repository_id"]: row for row in discovered}
    triage_by_id = {row["repository_id"]: row for row in rows["triage"] if row.get("record_type") == "repository"}
    extraction_by_id = {row["repository_id"]: row for row in rows["extraction"] if row.get("record_type") == "repository"}
    eligible_by_id = {row["repository_id"]: row for row in eligible}
    _require(set(discovery_by_id) == set(triage_by_id) and set(eligible_by_id) == set(extraction_by_id))
    _require(all(_identity(row) == _identity(triage_by_id[key]) for key, row in discovery_by_id.items()))
    _require(all(_identity(row) == _identity(extraction_by_id[key]) for key, row in eligible_by_id.items()))
    licenses = {key: _license(row) for key, row in eligible_by_id.items()}
    try:
        approved, linked = load_deduplication_inputs(paths["validation"], paths["extraction"])
    except BindingVerificationUnavailable as error:
        raise DatasetToolError("Não foi possível conferir os vínculos TypeScript.") from error
    _require(all(linked["sources"][name]["sha256"] == hashes[name] for name in ("extraction", "validation")))
    _require(all(dedup_start[key] == linked[key] for key in ("input_instance_count", "eligible_count", "input_decisions")))
    reproduced = deduplicate_instances(approved, linked)
    def scientific_fields(row):
        """Compara identidade e decisão, sem reescrever explicação histórica."""
        fields = ("instance_id", "repository_id", "repository_url", "commit_sha", "decision", "disposition",
                  "representative_instance_id", "representation_sha256", "representation_byte_count")
        criterion = row["criteria"]["exact_uniqueness"]
        return tuple(row[key] for key in fields) + (criterion["status"], criterion["code"])
    _require([scientific_fields(row) for row in reproduced[1:-1]] == [scientific_fields(row) for row in instances])
    validation_instances = {row["instance_id"]: row for row in rows["validation"] if row.get("record_type") == "instance"}
    validation_repositories = {row["repository_id"]: row for row in rows["validation"] if row.get("record_type") == "repository"}
    approved_by_id = {item["unit"]["instance_id"]: item for item in approved}
    duplicates = defaultdict(list)
    for row in instances:
        if row["disposition"] == "duplicate":
            duplicates[row["representative_instance_id"]].append(row)
    candidates = []
    for representative in instances:
        if representative["disposition"] != "representative":
            continue
        identity = representative["instance_id"]
        candidate = approved_by_id[identity]
        repository_id = candidate["unit"]["repository_id"]
        discovery = discovery_by_id[repository_id]
        screened = triage_by_id[repository_id]
        candidates.append({**candidate, "extraction_repository": extraction_by_id[repository_id],
                           "validation_instance": validation_instances[identity],
                           "validation_repository": validation_repositories[repository_id],
                           "source": {"repository_id": repository_id, "repository": discovery["repository"],
                                      "repository_url": discovery["repository_url"], "commit_sha": discovery["commit_sha"],
                                      "license": licenses[repository_id],
                                      "stars": _stars(discovery.get("stars")),
                                      "readme": _readme(screened.get("readme"))},
                           "representative": representative, "duplicate_origins": duplicates[identity]})
    statuses = {"discovery": discovery_source["status"], "triage": triage_source["status"],
                "extraction": extraction_source["status"], "validation": validation_finish["status"],
                "deduplication": rows["deduplication"][-1]["status"]}
    sources = {name: {"path": str(paths[name]), "sha256": hashes[name], "status": statuses[name],
                      "schema_version": rows[name][0].get("schema_version")} for name in names}
    return {"candidates": candidates, "sources": sources, "deduplication_policy": dedup_start["policy"]}
