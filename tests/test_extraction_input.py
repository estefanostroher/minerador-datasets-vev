"""Protege seleção e proveniência contra triagens inconsistentes."""

import hashlib
import json

import pytest

from robo_minerador.extraction import instance_id
from robo_minerador.extraction_input import load_triage
from robo_minerador.jsonl import write_jsonl
from robo_minerador.triage_checks import CRITERION_NAMES


def candidate(identity=1, decision="approved"):
    """Cria decisão integral e coerente com origem imutável."""
    return {"record_type": "repository", "repository_id": identity,
            "repository_url": f"https://github.com/owner/repo-{identity}",
            "commit_sha": f"{identity:040x}", "decision": decision,
            "criteria": {name: {"status": decision, "reason": "Evidência controlada.", "evidence": {}}
                         for name in CRITERION_NAMES}}


def triage_rows(candidates):
    """Envolve decisões com diagnósticos e contagens da triagem real."""
    counts = {state: sum(row["decision"] == state for row in candidates)
              for state in ("approved", "rejected", "not_evaluated")}
    status = "partial" if counts["not_evaluated"] else "completed"
    if candidates and counts["not_evaluated"] == len(candidates):
        status = "failed"
    base = {"record_type": "diagnostic", "stage": "triage", "message": "Diagnóstico.",
            "observed_at": "2026-11-02T00:00:00Z"}
    return [{**base, "event": "run_started", "input_count": len(candidates)}, *candidates,
            {**base, "event": "run_finished", "repository_count": len(candidates),
             **counts, "error_count": 0, "status": status}]


def save_rows(path, rows):
    """Grava bytes controlados com espaços e CRLF para verificar hash original."""
    path.write_bytes(("\r\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\r\n").encode("utf-8"))


@pytest.mark.parametrize("damage", [
    "duplicate_rejected", "count", "error_count", "initial_count", "decision", "criterion",
    "missing_criterion", "sha", "null_sha", "url", "truncated", "nan", "duplicate_key", "utf8",
    "status", "criterion_type", "diagnostic_date", "diagnostic_headers", "overflow",
])
def test_reject_inconsistent_triage(tmp_path, damage):
    """Evita selecionar origem errada ou aprovar critérios e contagens impossíveis."""
    rows = triage_rows([candidate(), candidate(2, "rejected")])
    if damage == "duplicate_rejected":
        rows[2]["repository_id"] = 1
    elif damage == "count":
        rows[-1]["approved"] = True
    elif damage == "error_count":
        rows[-1]["error_count"] = 1
    elif damage == "initial_count":
        rows[0]["input_count"] = 1
    elif damage == "decision":
        rows[1]["decision"] = "rejected"
    elif damage == "criterion":
        rows[1]["criteria"]["access"]["status"] = "unknown"
    elif damage == "criterion_type":
        rows[1]["criteria"]["access"]["status"] = []
    elif damage == "missing_criterion":
        del rows[1]["criteria"]["license"]
    elif damage == "sha":
        rows[1]["commit_sha"] = "main"
    elif damage == "null_sha":
        rows[1]["commit_sha"] = None
    elif damage == "url":
        rows[1]["repository_url"] += "?secret=token"
    elif damage == "truncated":
        rows.pop()
    elif damage == "status":
        rows[-1]["status"] = "partial"
    elif damage == "diagnostic_date":
        rows[0]["observed_at"] = "not-a-date"
    elif damage == "diagnostic_headers":
        rows[0]["rate_limit_headers"] = {"authorization": "secret"}
    path = tmp_path / "triage.jsonl"
    save_rows(path, rows)
    if damage == "nan":
        path.write_bytes(path.read_bytes().replace(b'"repository_id": 1', b'"repository_id": NaN'))
    elif damage == "duplicate_key":
        path.write_bytes(path.read_bytes().replace(b'"repository_id": 1', b'"repository_id": 1, "repository_id": 3'))
    elif damage == "utf8":
        path.write_bytes(b"\xff")
    elif damage == "overflow":
        path.write_bytes(path.read_bytes().replace(b'"repository_id": 1', b'"repository_id": 1e999'))
    with pytest.raises(ValueError):
        load_triage(path)


def test_original_hash_partial_input_and_stable_ids(tmp_path):
    """Preserva bytes e cobertura original e separa identidades entre commits."""
    rows = triage_rows([candidate(), candidate(2, "not_evaluated")])
    rows[2]["commit_sha"] = None
    path = tmp_path / "triage.jsonl"
    save_rows(path, rows)
    selected, source = load_triage(path)
    assert [row["repository_id"] for row in selected] == [1]
    assert selected[0]["repository"] == "owner/repo-1"
    assert source == {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      "status": "partial", "input_count": 2, "eligible_count": 1}
    function = {"path": "a.ts", "start_line": 2, "end_line": 2,
                "kind": "function_declaration", "name": "double"}
    identity = instance_id(selected[0], function)
    assert identity == instance_id({**selected[0], "extracted_at": "later"}, function)
    assert identity != instance_id({**selected[0], "commit_sha": "f" * 40}, function)
    assert identity != instance_id(selected[0], {**function, "start_line": 3})
    assert identity != instance_id(selected[0], function, ordinal=1)


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085"])
@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize("final_newline", [False, True])
def test_writer_unicode_separators_roundtrip(tmp_path, separator, newline, final_newline):
    """Lê evidência Unicode do escritor real sem confundir caracteres com linhas JSONL."""
    item = candidate()
    item["criteria"]["typescript"]["evidence"] = {"path": f"src/a{separator}b.ts"}
    path = tmp_path / "triage.jsonl"
    write_jsonl(path, triage_rows([item]))
    content = path.read_bytes()
    assert separator.encode("utf-8") in content
    if newline != b"\n":
        content = content.replace(b"\n", newline)
    if not final_newline:
        content = content.removesuffix(newline)
    path.write_bytes(content)
    selected, source = load_triage(path)
    assert selected[0]["criteria"] == item["criteria"]
    assert source["sha256"] == hashlib.sha256(content).hexdigest()
    assert source["input_count"] == source["eligible_count"] == 1


@pytest.mark.parametrize("blank", [b"\n", b"\r\n", b" \t\n"])
def test_physical_empty_jsonl_records_rejected(tmp_path, blank):
    """Rejeita registros físicos vazios, inclusive linha extra após o fim da execução."""
    path = tmp_path / "triage.jsonl"
    write_jsonl(path, triage_rows([candidate()]))
    content = path.read_bytes()
    first, remaining = content.split(b"\n", 1)
    for invalid in (first + b"\n" + blank + remaining, content + blank):
        path.write_bytes(invalid)
        with pytest.raises(ValueError):
            load_triage(path)
