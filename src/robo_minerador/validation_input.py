"""Confere a publicação inteira da extração antes de selecionar candidatas V3.

Esta leitura verifica o protocolo e os trechos publicados, não concede mérito
científico. A confirmação contra os objetos originais pertence ao replay offline
de load_repository_context e permanece obrigatória antes de qualquer execução.
"""

from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re

from .extraction import instance_id, _unit_position
from .extraction_analyzer import CRITERIA, VALIDATION, _fields, _range, _snippet
from .extraction_format import expand_instance, validate_snapshot_repository
from .extraction_input import STATES, aggregate, strict_float, strict_object, valid_error_context
from .triage import _invalid_constant


def _require(condition, message="Extração V3 inválida ou inconsistente."):
    """Interrompe a leitura sem transformar corrupção em pendência científica."""
    if not condition:
        raise ValueError(message)


def _instant(value):
    """Exige instante UTC explícito em registros de origem e diagnósticos."""
    _require(isinstance(value, str))
    instant = datetime.fromisoformat(value)
    _require(instant.utcoffset() is not None and instant.utcoffset().total_seconds() == 0)


def _counts(row, expected):
    """Confere contagens inteiras sem aceitar booleanos como números."""
    _require(all(type(row.get(key)) is int and row[key] == value for key, value in expected.items()),
             "Contagens da extração divergentes.")


def _workspace_path(value):
    """Exige referência portátil de arquivo dentro do workspace, sem normalização."""
    _require(isinstance(value, str) and bool(value) and value != "."
             and "\\" not in value and ":" not in value and not any(ord(c) < 32 for c in value))
    path = PurePosixPath(value)
    _require(not path.is_absolute() and not PureWindowsPath(value).drive
             and ".." not in path.parts and str(path) == value, "Caminho fora do contrato portátil.")


def _validate_unit(unit, files):
    """Confere toda evidência publicada, inclusive de unidades não selecionadas."""
    _fields(unit, ("record_type", "repository_id", "repository_url", "commit_sha", "extracted_at",
                   "instance_id", "decision", "function", "documentation", "tests", "context_paths",
                   "criteria", "validation", "reasons"))
    _require(type(unit["repository_id"]) is int and unit["repository_id"] > 0)
    function = unit["function"]
    _fields(function, ("kind", "name", "class_name", "path", "start_line", "end_line", "signature", "reference_code"))
    _require(function["kind"] in ("function_declaration", "class_method"))
    _require(isinstance(function["name"], str) and bool(function["name"]))
    _require(function["class_name"] is None or isinstance(function["class_name"], str) and bool(function["class_name"]))
    _require(function["kind"] != "function_declaration" or function["class_name"] is None)
    _snippet(function, "reference_code", files)
    _require(isinstance(function["signature"], str) and bool(function["signature"])
             and function["reference_code"].startswith(function["signature"]))
    _fields(unit["criteria"], CRITERIA)
    for criterion in unit["criteria"].values():
        _fields(criterion, ("status", "reason"))
        _require(isinstance(criterion["status"], str) and criterion["status"] in STATES
                 and isinstance(criterion["reason"], str) and bool(criterion["reason"]))
    _require(unit["decision"] == aggregate(c["status"] for c in unit["criteria"].values()))
    _require(unit["validation"] == VALIDATION)
    paths = unit["context_paths"]
    _require(isinstance(paths, list) and all(isinstance(p, str) and p in files for p in paths))
    _require(len(paths) == len(set(paths)) and function["path"] in paths)
    _require(unit["reasons"] == [c["reason"] for c in unit["criteria"].values() if c["status"] != "approved"])
    _require(isinstance(unit["documentation"], list) and isinstance(unit["tests"], list))
    for doc in unit["documentation"]:
        _fields(doc, ("kind", "path", "start_line", "end_line", "original_text", "association"))
        _snippet(doc, "original_text", files)
        association = doc["association"]
        _require(isinstance(association, dict))
        if doc["kind"] == "attached_comment":
            _require(association == {"kind": "attached"} and doc["path"] == function["path"])
        elif doc["kind"] == "external":
            _require(association == {"kind": "source_link", "source_path": function["path"]}
                     or association == {"kind": "explicit_reference", "target": f"{function['path']}#{function['name']}"})
        else:
            raise ValueError("Documentação da extração inválida.")
    for test in unit["tests"]:
        _fields(test, ("path", "framework", "name", "start_line", "end_line", "parameterized", "modifiers", "association"), ("skipped",))
        _range(test, files)
        _require(test["framework"] in ("vitest", "jest", "mocha", "node:test") and isinstance(test["name"], str))
        _require(type(test["parameterized"]) is bool and ("skipped" not in test or type(test["skipped"]) is bool))
        _require(isinstance(test["modifiers"], list) and all(isinstance(m, str) for m in test["modifiers"]))
        association = test["association"]
        _fields(association, ("kind", "call_line", "expression", "resolved_target"))
        _require(association["kind"] == "direct_call" and type(association["call_line"]) is int
                 and test["start_line"] <= association["call_line"] <= test["end_line"])
        expression = association["expression"]
        _require(isinstance(expression, str) and bool(expression))
        _require(any(len(re.split(r"\r\n|[\n\r\u2028\u2029]", files[test["path"]][:match.start()])) == association["call_line"]
                     for match in re.finditer(re.escape(expression), files[test["path"]])))
        _fields(association["resolved_target"], ("path", "start_line", "name"))
        _require(type(association["resolved_target"]["start_line"]) is int and association["resolved_target"] == {
            "path": function["path"], "start_line": function["start_line"], "name": function["name"]})
    _require(unit["criteria"]["associated_documentation"]["status"] != "approved" or bool(unit["documentation"]))
    _require(unit["criteria"]["direct_test_association"]["status"] != "approved"
             or any(t.get("skipped") is False for t in unit["tests"]))


def _validate_repository(row):
    """Confere contexto, catálogo de issues e origem sem abrir o cache ou usar rede."""
    validate_snapshot_repository(row)
    _instant(row.get("extracted_at"))
    _require(isinstance(row.get("repository_url"), str) and re.fullmatch(
        r"https://github\.com/[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", row["repository_url"]) is not None
        and row["repository_url"].rsplit("/", 1)[-1] not in {".", ".."})
    _require(type(row.get("repository_id")) is int and row["repository_id"] > 0)
    _require(isinstance(row.get("commit_sha"), str) and re.fullmatch(r"[a-f0-9]{40}", row["commit_sha"]) is not None)
    _fields(row.get("analyzer"), ("name", "version"))
    _require(all(isinstance(v, str) and v for v in row["analyzer"].values()))
    _require(row.get("instance_defaults") == {"validation": VALIDATION})
    context = row["context"]
    _fields(context, ("files", "snapshot", "analysis_complete", "local_dependencies", "unresolved_references"))
    for requirement in row["execution_requirements"]:
        _workspace_path(requirement["path"])
    _require(isinstance(context["files"], list))
    files = {}
    for item in context["files"]:
        _fields(item, ("path", "content", "sha256"))
        _workspace_path(item["path"])
        _require(item["path"] not in files)
        _require(isinstance(item["content"], str) and isinstance(item["sha256"], str))
        _require(hashlib.sha256(item["content"].encode("utf-8")).hexdigest() == item["sha256"])
        files[item["path"]] = item["content"]
    for key in ("local_dependencies", "unresolved_references"):
        _require(isinstance(context[key], list))
        for dependency in context[key]:
            _fields(dependency, ("from", "specifier", "resolved_path") if key == "local_dependencies" else ("from", "specifier"))
            _require(isinstance(dependency["from"], str) and dependency["from"] in files
                     and isinstance(dependency["specifier"], str) and bool(dependency["specifier"]))
            if key == "local_dependencies":
                if dependency["resolved_path"] is not None:
                    _workspace_path(dependency["resolved_path"])
    _require(isinstance(row.get("issues"), list))
    issues = {}
    for issue in row["issues"]:
        _fields(issue, ("code", "message", "issue_id", "affected_count"), ("affected_paths", "http_status", "rate_limit_headers"))
        _require(all(isinstance(issue[k], str) and issue[k] for k in ("code", "message", "issue_id")))
        _require(issue["issue_id"] not in issues and type(issue["affected_count"]) is int and issue["affected_count"] > 0
                 and valid_error_context(issue))
        if "affected_paths" in issue:
            _require(isinstance(issue["affected_paths"], list) and len(issue["affected_paths"]) == issue["affected_count"]
                     and all(isinstance(p, str) and p for p in issue["affected_paths"]))
            for path in issue["affected_paths"]:
                _workspace_path(path)
        issues[issue["issue_id"]] = issue
    _require(row.get("extraction_status") in ("failed", "partial", "completed"))
    return files, issues


def load_extraction(path) -> tuple[list[dict], dict]:
    """Retorna grupos approved e proveniência dos bytes após validar TODOS registros.

    Cada grupo contém repository, instances compactas approved e reasons globais.
    source distingue input_count (instâncias), repository_count e as contagens
    elegíveis; aceita partial sem promovê-lo a completed. Não consulta snapshots.
    """
    path = Path(path)
    content = path.read_bytes()
    try:
        lines = content.decode("utf-8").split("\n")
        if lines[-1] == "":
            lines.pop()
        rows = [json.loads(line, parse_constant=_invalid_constant, object_pairs_hook=strict_object,
                           parse_float=strict_float) for line in lines]
        return _load_rows(rows, path, content)
    except (UnicodeError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise ValueError("A extração deve ser JSONL V3 UTF-8 estrito e integralmente consistente.") from error


def _load_rows(rows, path, content):
    """Confere sequência, referências antecipadas, identidade ordinal e totalizadores."""
    _require(len(rows) >= 2 and all(isinstance(row, dict) for row in rows))
    first, last = rows[0], rows[-1]
    _require(first.get("event") == "run_started" and last.get("event") == "run_finished")
    reasons, groups, repositories, urls, identities = {}, [], set(), set(), set()
    current, files, issues, ordinals = None, {}, {}, Counter()
    totals, local, diagnostic_counts = Counter(), Counter(), Counter()
    errors = issue_count = 0
    statuses = []
    previous_position = None
    diagnostics_started = False
    pending_criteria = False

    def finish_group():
        """Fecha contagens e vínculo das issues antes de aceitar o próximo grupo."""
        if current is None:
            return
        _counts(current["repository"]["summary"], {"instance_count": sum(local.values()), **{s: local[s] for s in STATES}})
        _require(all(diagnostic_counts[key] == item["affected_count"] for key, item in issues.items()))
        status = current["repository"]["extraction_status"]
        _require(status != "failed" or not sum(local.values()))
        if status != "failed":
            _require(status == ("partial" if issues or pending_criteria else "completed"))
        statuses.append(status)
        if current["instances"]:
            groups.append(current)

    for index, row in enumerate(rows):
        kind = row.get("record_type")
        required_version = kind in {"repository", "instance"} or row.get("event") in {"run_started", "run_finished"}
        if required_version or "schema_version" in row:
            _require(type(row.get("schema_version")) is int and row["schema_version"] == 3, "Versões de extração incompatíveis.")
        if kind == "repository":
            finish_group()
            files, issues = _validate_repository(row)
            identity, url = row["repository_id"], row["repository_url"].lower()
            _require(identity not in repositories and url not in urls)
            repositories.add(identity)
            urls.add(url)
            current = {"repository": row, "instances": [], "reasons": reasons}
            local, diagnostic_counts, ordinals = Counter(), Counter(), Counter()
            previous_position, diagnostics_started, pending_criteria = None, False, False
        elif kind == "criterion_reason":
            _fields(row, ("record_type", "reason_id", "text"), ("schema_version",))
            _require(current is not None and not diagnostics_started)
            _require(isinstance(row["reason_id"], str) and bool(row["reason_id"]) and row["reason_id"] not in reasons
                     and isinstance(row["text"], str) and bool(row["text"]))
            reasons[row["reason_id"]] = row["text"]
        elif kind == "instance":
            _require(current is not None and not diagnostics_started)
            expanded = expand_instance(row, current["repository"], reasons)
            _validate_unit(expanded, files)
            position = _unit_position(expanded)
            _require(previous_position is None or previous_position <= position)
            expected_id = instance_id(current["repository"], expanded["function"], ordinal=ordinals[position])
            _require(row["instance_id"] == expected_id and expected_id not in identities)
            ordinals[position] += 1
            previous_position = position
            identities.add(expected_id)
            local[row["decision"]] += 1
            totals[row["decision"]] += 1
            pending_criteria |= any(c["status"] == "not_evaluated" for c in expanded["criteria"].values())
            if row["decision"] == "approved":
                current["instances"].append(row)
        elif kind == "diagnostic":
            _require(row.get("stage") == "extraction" and valid_error_context(row))
            _instant(row.get("observed_at"))
            event = row.get("event")
            _require(event in {"run_started", "run_finished", "repository_error", "no_instances"})
            _require(event != "run_started" or index == 0)
            _require(event != "run_finished" or index == len(rows) - 1)
            if "repository_id" in row and row["repository_id"] is not None:
                _require(type(row["repository_id"]) is int and row["repository_id"] > 0)
            if "issue_ref" in row:
                _require(event == "repository_error" and current is not None
                         and row.get("repository_id") == current["repository"]["repository_id"]
                         and row["issue_ref"] in issues and not diagnostic_counts[row["issue_ref"]])
                _fields(row, ("record_type", "stage", "event", "repository_id", "issue_ref", "affected_count", "observed_at"),
                        ("last_observed_at", "schema_version"))
                _require(type(row["affected_count"]) is int and row["affected_count"] > 0)
                diagnostic_counts[row["issue_ref"]] += row["affected_count"]
                if "last_observed_at" in row:
                    _instant(row["last_observed_at"])
                    _require(datetime.fromisoformat(row["last_observed_at"]) >= datetime.fromisoformat(row["observed_at"]))
            else:
                _require(isinstance(row.get("message"), str) and bool(row["message"]))
                common = ("record_type", "stage", "event", "message", "observed_at")
                if event == "repository_error":
                    _fields(row, common + ("repository_id",), ("schema_version", "http_status", "rate_limit_headers"))
                    _require(row["repository_id"] is None, "Issue de repositório sem referência compacta.")
                elif event == "no_instances":
                    _fields(row, common + ("repository_id",), ("schema_version",))
                    _require(current is not None and row["repository_id"] == current["repository"]["repository_id"])
                else:
                    policy = ("strategy", "context_policy", "cache_strategy", "limits", "cache_hits", "cache_misses", "cache_errors")
                    if event == "run_started":
                        _fields(row, common + ("schema_version", "input_count", "eligible_count"),
                                policy + ("input_path", "input_sha256", "input_status", "workers"))
                    else:
                        _fields(row, common + ("schema_version", "status", "repository_count", "instance_count", "approved",
                                               "rejected", "not_evaluated", "error_count", "issue_count"), policy)
                    for field in ("cache_hits", "cache_misses", "cache_errors", "workers"):
                        if field in row:
                            _require(type(row[field]) is int and row[field] >= (1 if field == "workers" else 0))
                    if "limits" in row:
                        _require(isinstance(row["limits"], dict) and bool(row["limits"])
                                 and all(type(value) is int and value > 0 for value in row["limits"].values()))
            if event in {"repository_error", "no_instances"}:
                diagnostics_started = True
            if event == "no_instances":
                _require(not sum(local.values()) and current is not None)
            errors += event == "repository_error"
            issue_count += (row["affected_count"] if "issue_ref" in row else 1) if event == "repository_error" else 0
        else:
            raise ValueError("Tipo desconhecido na extração.")
    finish_group()
    _require(first.get("record_type") == last.get("record_type") == "diagnostic")
    _counts(last, {"repository_count": len(repositories), "instance_count": len(identities),
                   **{state: totals[state] for state in STATES}, "error_count": errors, "issue_count": issue_count})
    _require(type(first.get("eligible_count")) is int and first["eligible_count"] == len(repositories))
    _require(type(first.get("input_count")) is int and first["input_count"] >= len(repositories))
    expected_status = ("failed" if statuses and all(s == "failed" for s in statuses)
                       else "partial" if errors or any(s != "completed" for s in statuses) else "completed")
    _require(last.get("status") == expected_status)
    return groups, {"path": str(path), "sha256": hashlib.sha256(content).hexdigest(), "schema_version": 3,
                    "status": last["status"], "input_count": len(identities), "repository_count": len(repositories),
                    "eligible_count": totals["approved"], "eligible_repository_count": len(groups),
                    "decisions": {state: totals[state] for state in sorted(STATES)}}
