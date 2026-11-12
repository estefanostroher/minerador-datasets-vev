"""Confere a ligação integral entre extração e validação antes da seleção."""

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess

from .extraction_analyzer import HELPER_DIRECTORY, preflight
from .extraction_format import expand_instance
from .extraction_input import strict_float, strict_object
from .triage import _invalid_constant
from .validation_evaluation import PAIR_POLICY, _association
from .validation_format import validate_output_records
from .validation_input import load_extraction


def read_jsonl_bytes(path):
    """Lê uma vez JSONL UTF-8 estrito e devolve registros e bytes originais."""
    raw = Path(path).read_bytes()
    try:
        lines = raw.decode("utf-8").split("\n")
        if lines[-1] == "":
            lines.pop()
        rows = [json.loads(line, object_pairs_hook=strict_object, parse_float=strict_float,
                           parse_constant=_invalid_constant) for line in lines]
    except (UnicodeError, ValueError) as error:
        raise ValueError("JSONL deve ser UTF-8 estrito, sem linhas vazias ou valores inválidos.") from error
    return rows, raw


def _require(condition):
    """Recusa vínculos divergentes entre as publicações."""
    if not condition:
        raise ValueError("Extração e validação possuem evidências ou origens divergentes.")


def _interval(content, start, end):
    """Recupera trecho e linhas em offsets UTF-16 usados pelo TypeScript."""
    raw = content.encode("utf-16-le", errors="surrogatepass")
    _require(type(start) is int and type(end) is int and 0 <= start < end <= len(raw) // 2)
    prefix = raw[:2 * start].decode("utf-16-le", errors="surrogatepass")
    through = raw[:2 * end].decode("utf-16-le", errors="surrogatepass")
    text = raw[2 * start:2 * end].decode("utf-16-le", errors="surrogatepass")
    return text, len(re.split(r"\r\n|[\n\r\u2028\u2029]", prefix)), len(re.split(r"\r\n|[\n\r\u2028\u2029]", through))


def _check_approved(unit, files, record, repository):
    """Vincula alvo e testemunhas creditadas aos textos originais da extração."""
    plan = repository["unit_suite"].get("plan", {})
    target = plan.get("targets", {}).get(unit["instance_id"])
    function = unit["function"]
    _require(type(target) is dict and target.get("id") == unit["instance_id"] and target.get("path") == function["path"])
    text, first, last = _interval(files[function["path"]], target.get("start"), target.get("end"))
    _require((text, first, last) == (function["reference_code"], function["start_line"], function["end_line"]))
    plans = plan.get("plans", [])

    def check_hash(path):
        """Exige um único hash do plano correspondente à fonte publicada."""
        matches = [item for item in plans if item.get("path") == path]
        _require(path in files and len(matches) == 1 and matches[0].get("sha256") == hashlib.sha256(files[path].encode("utf-8")).hexdigest())

    check_hash(function["path"])
    tests = [_association(test) for test in unit["tests"]]
    bindings = []
    for case in record["case_evidence"]:
        declaration = case["declaration"]
        for point in case["points"]:
            matches = [test for test in tests if test["path"] == point.get("path")
                       and test["association"] == point.get("association") and test in case["original_tests"]]
            _require(bool(matches) and declaration.get("file") == point["path"])
            content = files[point["path"]]
            _, decl_first, decl_last = _interval(content, declaration.get("start"), declaration.get("end"))
            _, point_first, _ = _interval(content, point.get("start"), point.get("end"))
            matches = [test for test in matches if (decl_first, decl_last, point_first) ==
                       (test["start_line"], test["end_line"], test["association"]["call_line"])]
            _require(declaration["start"] <= point["start"] < point["end"] <= declaration["end"] and bool(matches))
            check_hash(point["path"])
            test = matches[0]
            bindings.append({"path": point["path"], "declaration_start": declaration["start"],
                             "declaration_end": declaration["end"], "point_start": point["start"],
                             "point_end": point["end"], "start_line": test["start_line"],
                             "end_line": test["end_line"], "call_line": test["association"]["call_line"],
                             "expression": test["association"]["expression"]})
    return bindings


def _verify_bindings(batches):
    """Executa o compilador confiável por repositório sem executar código candidato."""
    try:
        settings = preflight()
        helper = HELPER_DIRECTORY / "deduplication-bindings.mjs"
        if not helper.is_file() or settings["analyzer"]["version"] != "6.0.3":
            raise ValueError()
        for batch in batches.values():
            unique = {json.dumps(binding, sort_keys=True, ensure_ascii=True): binding for binding in batch["bindings"]}
            paths = {binding["path"] for binding in unique.values()}
            payload = {"files": [{"path": path, "content": batch["files"][path]} for path in sorted(paths)],
                       "bindings": list(unique.values())}
            result = subprocess.run([settings["node"], "--max-old-space-size=512", str(helper)],
                                    input=json.dumps(payload, ensure_ascii=True).encode("utf-8"),
                                    capture_output=True, timeout=180, check=False)
            if result.returncode != 0 or json.loads(result.stdout) != {"ok": True}:
                raise ValueError()
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        raise ValueError("Não foi possível conferir os vínculos sintáticos da validação.") from None


def load_deduplication_inputs(validation_path, extraction_path):
    """Valida ambas as origens e seleciona apenas instâncias aprovadas no 04."""
    rows, raw = read_jsonl_bytes(validation_path)
    _require(bool(rows) and all(type(row) is dict and type(row.get("schema_version")) is int
                                and row["schema_version"] == 3 for row in rows))
    _require(json.dumps(rows[0].get("policy"), sort_keys=True) == json.dumps(PAIR_POLICY, sort_keys=True))
    finished = validate_output_records(rows)
    groups, extraction_source = load_extraction(extraction_path)
    source = rows[0]["source"]
    _require(all(source.get(key) == extraction_source[key] for key in (
        "sha256", "schema_version", "status", "input_count", "repository_count", "eligible_count",
        "eligible_repository_count", "decisions")))
    originals = {}
    repositories = {}
    for group in groups:
        repository = group["repository"]
        repositories[repository["repository_id"]] = repository
        files = {item["path"]: item["content"] for item in repository["context"]["files"]}
        for compact in group["instances"]:
            unit = expand_instance(compact, repository, group["reasons"])
            originals[unit["instance_id"]] = {"unit": unit, "files": files}
    instances = [row for row in rows if row["record_type"] == "instance"]
    _require({row["instance_id"] for row in instances} == set(originals))
    published_repositories = {row["repository_id"]: row for row in rows if row["record_type"] == "repository"}
    _require(set(published_repositories) == set(repositories))
    for identity, repository in published_repositories.items():
        _require(all(repository[key] == repositories[identity][key] for key in ("repository_url", "commit_sha")))
    candidates, batches = [], {}
    for record in instances:
        candidate = originals[record["instance_id"]]
        unit = candidate["unit"]
        _require(record["repository_id"] == unit["repository_id"])
        associations = [item["test"] for item in record["test_cases"]["associations"]]
        if associations or record["decision"] == "approved":
            _require(associations == [_association(test) for test in unit["tests"]])
        if record["decision"] == "approved":
            bindings = _check_approved(unit, candidate["files"], record, published_repositories[record["repository_id"]])
            batch = batches.setdefault(record["repository_id"], {"files": candidate["files"], "bindings": []})
            batch["bindings"].extend(bindings)
            candidates.append(candidate)
    if candidates:
        _verify_bindings(batches)
    counts = Counter(record["decision"] for record in instances)
    return candidates, {"sources": {
        "extraction": {key: extraction_source[key] for key in ("path", "sha256", "schema_version", "status")},
        "validation": {"path": str(validation_path), "sha256": hashlib.sha256(raw).hexdigest(),
                       "schema_version": 3, "status": finished["status"]}},
        "input_instance_count": len(instances), "eligible_count": len(candidates),
        "input_decisions": {state: counts[state] for state in ("approved", "rejected", "not_evaluated")}}
