"""Protege seleção e contagens contra corrupção inclusive fora das aprovadas."""

from copy import deepcopy
import hashlib
import json

import pytest

from robo_minerador.extraction import _extract_candidate
from robo_minerador.extraction_analyzer import Analyzer, preflight
from robo_minerador.extraction_format import compact_records
from robo_minerador.validation_input import load_extraction
from test_extraction_context import make_local_snapshot
from test_extraction_format import diagnostic


@pytest.fixture(scope="module")
def extraction_rows(tmp_path_factory):
    """Publica V3 real com rejeitados, pendentes, issues e contagens do produtor."""
    state = make_local_snapshot(tmp_path_factory.mktemp("validation-input"), {
        "math.ts": "/** Incrementa. */\nexport function increment(x: number) { return x + 1; }\n"
                   "/** Constante. */\nexport function constant() { return 1; }\n"
                   "export function undocumented(x: number) { return x + 2; }\n"
                   "/** Escolhe. */\nexport function ambiguous(x: unknown) { return x; }\n",
        "math.test.ts": "import { test } from 'vitest';\nimport { increment, constant, undocumented, ambiguous } from './math';\n"
                        "test('incrementa', () => { increment(1); });\n"
                        "test('constante', () => { constant(); });\n"
                        "test('sem doc', () => { undocumented(1); });\n"
                        "test('ambigua', () => { ambiguous({}); });\n",
    })
    analyzer = Analyzer(preflight())
    try:
        group = _extract_candidate(state.client, state.candidate, analyzer, store=state.store)
    finally:
        analyzer.cancel()
    repository = group[0]
    return list(compact_records([
        diagnostic("run_started", message="Iniciada.", input_count=1, eligible_count=1), *group,
        diagnostic("run_finished", repository_count=1, **repository["summary"],
                   message="Encerrada.", error_count=len(repository["issues"]), status=repository["extraction_status"]),
    ], schema_version=3))


def write_rows(tmp_path, rows):
    """Grava somente entrada de fixture e devolve caminho e bytes sem normalização."""
    raw = ("\n".join(json.dumps(row, ensure_ascii=True) for row in rows) + "\n").encode()
    path = tmp_path / "extraction.jsonl"
    path.write_bytes(raw)
    return path, raw


def empty_repository_rows(rows):
    """Remove unidades da fixture mantendo uma publicação válida sem candidatas."""
    rows = [row for row in deepcopy(rows) if row["record_type"] != "instance"]
    repository = next(row for row in rows if row["record_type"] == "repository")
    counts = {"instance_count": 0, "approved": 0, "rejected": 0, "not_evaluated": 0}
    repository["summary"] = counts
    repository["extraction_status"] = "partial" if repository["issues"] else "completed"
    rows[-1].update(counts, status=repository["extraction_status"])
    rows.insert(-1, diagnostic("no_instances", message="Sem instâncias.", repository_id=repository["repository_id"]))
    return rows


def test_selects_approved_preserving_partial_and_original_bytes(tmp_path, extraction_rows):
    """A seleção não altera a origem partial nem confunde contagens elegíveis/totais."""
    path, raw = write_rows(tmp_path, extraction_rows)
    groups, source = load_extraction(path)
    assert source["sha256"] == hashlib.sha256(raw).hexdigest()
    assert source["status"] == "partial" and source["schema_version"] == 3
    assert source["input_count"] == 4 and source["eligible_count"] == 1
    assert source["repository_count"] == source["eligible_repository_count"] == 1
    assert len(groups[0]["instances"]) == 1
    assert groups[0]["instances"][0]["function"]["name"] == "increment"
    assert groups[0]["reasons"]


@pytest.mark.parametrize("tamper", ["rejected", "summary", "final", "version", "reference", "duplicate_id",
                                     "issue", "missing_issue", "context_hash", "ordinal", "end", "late_start",
                                     "instance_float_id", "instance_bool_id", "issue_bool_id", "no_instances_bool_id",
                                     "empty_context_path", "resolved_path", "requirement_path", "issue_path",
                                     "global_count", "global_extra", "global_positive_count", "start_extra", "diagnostic_negative_count",
                                     "empty_absolute_path", "empty_windows_path", "empty_noncanonical_path"])
def test_rejects_inconsistent_entire_stream(tmp_path, extraction_rows, tamper):
    """Corrupção de unidade ignorada ou diagnóstico também bloqueia a validação global."""
    rows = (empty_repository_rows(extraction_rows) if tamper.startswith("empty_") or tamper == "no_instances_bool_id"
            else deepcopy(extraction_rows))
    repository = next(row for row in rows if row["record_type"] == "repository")
    units = [row for row in rows if row["record_type"] == "instance"]
    if tamper == "rejected":
        next(row for row in units if row["decision"] == "rejected")["decision"] = "approved"
    elif tamper == "summary":
        repository["summary"]["approved"] += 1
    elif tamper == "final":
        rows[-1]["instance_count"] += 1
    elif tamper == "version":
        units[-1]["schema_version"] = 2
    elif tamper == "reference":
        next(iter(units[-1]["criteria"].values()))["reason_ref"] = "R-unknown"
    elif tamper == "duplicate_id":
        units[-1]["instance_id"] = units[0]["instance_id"]
    elif tamper == "issue":
        next(row for row in rows if "issue_ref" in row)["affected_count"] += 1
    elif tamper == "missing_issue":
        rows.remove(next(row for row in rows if "issue_ref" in row))
    elif tamper == "context_hash":
        repository["context"]["files"][0]["content"] += "adulterado"
    elif tamper == "ordinal":
        units[-1]["instance_id"] = "f" * 64
    elif tamper == "end":
        rows.pop()
    elif tamper == "late_start":
        rows.insert(-1, deepcopy(rows[0]))
    elif tamper in {"instance_float_id", "instance_bool_id"}:
        units[-1]["repository_id"] = 1.0 if tamper == "instance_float_id" else True
    elif tamper == "issue_bool_id":
        next(row for row in rows if "issue_ref" in row)["repository_id"] = True
    elif tamper == "no_instances_bool_id":
        next(row for row in rows if row.get("event") == "no_instances")["repository_id"] = True
    elif tamper.startswith("empty_"):
        path = {"empty_context_path": "../escape.ts", "empty_absolute_path": "/escape.ts",
                "empty_windows_path": "C:\\escape.ts", "empty_noncanonical_path": "a/./escape.ts"}[tamper]
        repository["context"]["files"].append({"path": path, "content": "", "sha256": hashlib.sha256(b"").hexdigest()})
    elif tamper == "resolved_path":
        repository["context"]["local_dependencies"][0]["resolved_path"] = "../escape.ts"
    elif tamper == "requirement_path":
        repository["execution_requirements"].append({"type": "environment", "path": "../escape.ts", "status": "not_evaluated"})
    elif tamper == "issue_path":
        issue = repository["issues"][0]
        issue["affected_paths"] = ["../escape.ts"] * issue["affected_count"]
    elif tamper == "start_extra":
        rows[0]["path"] = "../escape.ts"
    elif tamper == "diagnostic_negative_count":
        rows[-1]["cache_hits"] = -1
    else:
        error = diagnostic("repository_error", message="Falha ao fechar cliente.", repository_id=None)
        rows.insert(-1, error)
        rows[-1]["error_count"] += 1
        if tamper in {"global_count", "global_positive_count"}:
            count = -7 if tamper == "global_count" else 7
            error["affected_count"] = count
            rows[-1]["issue_count"] += count
        else:
            error["path"] = "../escape.ts"
            rows[-1]["issue_count"] += 1
    path, _ = write_rows(tmp_path, rows)
    with pytest.raises(ValueError):
        load_extraction(path)


def test_empty_repository_and_global_close_error_keep_positive_counts(tmp_path, extraction_rows):
    """Aceita ausência legítima de candidatas e conta o fechamento global uma vez."""
    rows = empty_repository_rows(extraction_rows)
    rows.insert(-1, diagnostic("repository_error", message="Falha ao fechar cliente.", repository_id=None))
    rows[-1]["error_count"] += 1
    rows[-1]["issue_count"] += 1
    rows[-1]["status"] = "partial"
    path, _ = write_rows(tmp_path, rows)
    groups, source = load_extraction(path)
    assert groups == [] and source["eligible_count"] == 0 and source["status"] == "partial"


@pytest.mark.parametrize("raw", [b'{}\n\n', b'\xef\xbb\xbf{}\n', b'{"x":1,"x":2}\n',
                                 b'{"x":NaN}\n', b'{"x":1e999}\n', b'\xff\n'])
def test_rejects_non_strict_json_before_selection(tmp_path, raw):
    """Impede parsers divergentes por chaves repetidas, BOM, Unicode ou não finitos."""
    path = tmp_path / "invalid.jsonl"
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        load_extraction(path)


def test_empty_extraction_requires_no_runtime(tmp_path):
    """Uma publicação vazia válida é aceita sem cache, Node, Docker ou receita."""
    rows = [diagnostic("run_started", message="Iniciada.", schema_version=3, eligible_count=0, input_count=0),
            diagnostic("run_finished", schema_version=3, status="completed", repository_count=0,
                       message="Encerrada.", instance_count=0, approved=0, rejected=0, not_evaluated=0, error_count=0, issue_count=0)]
    path, _ = write_rows(tmp_path, rows)
    groups, source = load_extraction(path)
    assert groups == [] and source["eligible_count"] == 0
