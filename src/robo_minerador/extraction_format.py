"""Publica referências compactas sem alterar evidências da extração legada."""

from collections import Counter
from copy import deepcopy
import json

from .extraction_analyzer import VALIDATION


def _require(condition, message):
    """Recusa contratos incompatíveis sem completar evidências ausentes."""
    if not condition:
        raise ValueError(message)


def _paths(repository):
    """Obtém os caminhos reais do contexto sem presumir dependências."""
    paths = [item["path"] for item in repository["context"]["files"]]
    _require(len(paths) == len(set(paths)), "Caminhos de contexto duplicados.")
    return sorted(paths)


def _criterion_reasons(criteria):
    """Reconstrói a sequência original de motivos dos critérios não aprovados."""
    return [item["reason"] for item in criteria.values() if item["status"] != "approved"]


def _issue_key(issue):
    """Identifica o payload integral distinguindo presença e ausência de caminho."""
    return ("path" in issue, json.dumps({key: value for key, value in issue.items() if key != "path"},
                                      sort_keys=True, ensure_ascii=True, allow_nan=False))


def _group_issues(issues):
    """Agrupa ocorrências mantendo todos os detalhes e caminhos repetidos."""
    groups, lookup = [], {}
    for issue in issues:
        key = _issue_key(issue)
        if key not in lookup:
            group = {name: deepcopy(value) for name, value in issue.items() if name != "path"}
            _require(not {"issue_id", "affected_count", "affected_paths"} & group.keys(),
                     "Issue já compactada ou incompatível.")
            group.update(issue_id=f"I{len(groups) + 1:03d}", affected_count=0)
            if "path" in issue:
                group["affected_paths"] = []
            lookup[key] = group
            groups.append(group)
        group = lookup[key]
        group["affected_count"] += 1
        if "path" in issue:
            group["affected_paths"].append(deepcopy(issue["path"]))
    return groups, lookup


def expand_instance(instance: dict, repository: dict, reasons: dict[str, str]) -> dict:
    """Expande instância v2 usando catálogo id→texto; preserva instâncias legadas."""
    _require(instance.get("record_type") == "instance" and repository.get("record_type") == "repository",
             "Tipos incompatíveis para expansão.")
    _require(instance.get("schema_version", 1) in (1, 2)
             and repository.get("schema_version", 1) in (1, 2), "Versão desconhecida.")
    _require(instance.get("repository_id") == repository.get("repository_id")
             and "repository_id" in instance, "Repositório incompatível com a instância.")
    result = deepcopy(instance)
    for field in ("repository_url", "commit_sha", "extracted_at"):
        _require(field in repository, f"Origem ausente: {field}.")
        if field in result:
            _require(result[field] == repository[field], f"Origem divergente: {field}.")
        else:
            result[field] = deepcopy(repository[field])
    if "context_ref" in result:
        _require(result.pop("context_ref") == "repository" and "context_paths" not in result,
                 "Referência de contexto desconhecida ou duplicada.")
        result["context_paths"] = _paths(repository)
    _require("context_paths" in result, "Contexto ausente.")
    _require(isinstance(result.get("criteria"), dict), "Critérios ausentes ou inválidos.")
    for criterion in result["criteria"].values():
        if "reason_ref" in criterion:
            reference = criterion.pop("reason_ref")
            _require(isinstance(reference, str) and reference in reasons and "reason" not in criterion,
                     "Motivo ausente ou definido em duplicidade.")
            _require(isinstance(reasons[reference], str), "Definição de motivo inválida.")
            criterion["reason"] = reasons[reference]
        _require(isinstance(criterion.get("reason"), str), "Motivo ausente.")
    if "validation" not in result:
        _require("validation" in repository.get("instance_defaults", {}), "Validação padrão ausente.")
        result["validation"] = deepcopy(repository["instance_defaults"]["validation"])
    if "reasons" not in result:
        result["reasons"] = _criterion_reasons(result["criteria"])
    return result


def _compact_instance(instance, repository, catalog, paths):
    """Compacta uma instância e devolve definições novas antes de suas referências."""
    _require(not {"context_ref", "schema_version"} & instance.keys(), "Instância não é v1.")
    for field in ("repository_id", "repository_url", "commit_sha", "extracted_at"):
        _require(field in instance and instance[field] == repository[field], f"Origem divergente: {field}.")
    _require(all(field in instance for field in ("validation", "context_paths", "criteria", "reasons")),
             "Instância v1 incompleta.")
    result = deepcopy(instance)
    for field in ("repository_url", "commit_sha", "extracted_at"):
        del result[field]
    if result["validation"] == repository["instance_defaults"]["validation"]:
        del result["validation"]
    if result["context_paths"] == paths:
        del result["context_paths"]
        result["context_ref"] = "repository"
    if result["reasons"] == _criterion_reasons(result["criteria"]):
        del result["reasons"]
    definitions = []
    for criterion in result["criteria"].values():
        _require("reason_ref" not in criterion and isinstance(criterion.get("reason"), str),
                 "Critério não é v1 ou motivo inválido.")
        text = criterion.pop("reason")
        if text not in catalog:
            catalog[text] = f"R{len(catalog) + 1:03d}"
            definitions.append({"record_type": "criterion_reason", "reason_id": catalog[text], "text": text})
        criterion["reason_ref"] = catalog[text]
    return definitions, result


def compact_records(records):
    """Consome v1 preguiçosamente, publica v2 e fecha a origem mesmo em cancelamento."""
    source = iter(records)
    catalog, repository, lookup, pending, matched = {}, None, {}, {}, {}
    paths, instance_ids, occurrences = [], set(), Counter()
    error_count = issue_count = 0
    started = finished = False
    producer_error = None
    try:
        while True:
            try:
                raw = next(source)
            except StopIteration:
                break
            except Exception as error:
                producer_error = error
                break
            _require(isinstance(raw, dict) and raw.get("schema_version", 1) == 1, "Registro não é v1.")
            kind, event = raw.get("record_type"), raw.get("event")
            _require(not finished, "Registro após encerramento.")
            if event == "run_started" and kind == "diagnostic":
                _require(not started, "Início duplicado.")
                started = True
                yield {**raw, "schema_version": 2}
                continue
            _require(started, "Início da execução ausente.")
            if kind == "repository" or event == "run_finished":
                for diagnostic in pending.values():
                    error_count += 1
                    issue_count += diagnostic["affected_count"]
                    yield diagnostic
                pending, matched = {}, {}
            if kind == "repository":
                _require("instance_defaults" not in raw, "Repositório não é v1.")
                _require(all(field in raw for field in ("repository_id", "repository_url", "commit_sha",
                         "extracted_at", "analyzer", "context", "summary", "extraction_status", "issues")),
                         "Repositório v1 incompleto.")
                _require(repository is None or raw["repository_id"] != repository["repository_id"],
                         "Definição de repositório duplicada.")
                repository = deepcopy(raw)
                paths = _paths(repository)
                instance_ids = set()
                occurrences = Counter(json.dumps(issue, sort_keys=True, ensure_ascii=True, allow_nan=False)
                                      for issue in raw["issues"])
                repository["instance_defaults"] = {"validation": deepcopy(VALIDATION)}
                repository["issues"], lookup = _group_issues(raw["issues"])
                yield repository
            elif kind == "instance":
                _require(repository is not None, "Instância sem repositório.")
                _require(raw.get("instance_id") is not None and raw["instance_id"] not in instance_ids,
                         "Identidade de instância ausente ou duplicada.")
                instance_ids.add(raw["instance_id"])
                definitions, instance = _compact_instance(raw, repository, catalog, paths)
                yield from definitions
                yield instance
            elif kind == "diagnostic" and event == "repository_error":
                _require("issue_ref" not in raw and "affected_count" not in raw,
                         "Diagnóstico não é v1.")
                payload = {key: value for key, value in raw.items()
                           if key not in {"record_type", "stage", "event", "observed_at", "repository_id"}}
                key = _issue_key(payload)
                group = lookup.get(key) if repository and raw.get("repository_id") == repository["repository_id"] else None
                occurrence = json.dumps(payload, sort_keys=True, ensure_ascii=True, allow_nan=False)
                available = occurrences[occurrence] if group else 0
                if group and matched.get(occurrence, 0) < available:
                    matched[occurrence] = matched.get(occurrence, 0) + 1
                    reference = group["issue_id"]
                    timestamp = raw["observed_at"]
                    if reference not in pending:
                        pending[reference] = {"record_type": "diagnostic", "stage": raw["stage"],
                                              "event": event, "repository_id": raw["repository_id"],
                                              "issue_ref": reference, "affected_count": 0,
                                              "observed_at": timestamp}
                    diagnostic = pending[reference]
                    diagnostic["affected_count"] += 1
                    first = min(diagnostic["observed_at"], timestamp)
                    last = max(diagnostic.get("last_observed_at", diagnostic["observed_at"]), timestamp)
                    diagnostic["observed_at"] = first
                    if first != last:
                        diagnostic["last_observed_at"] = last
                else:
                    error_count += 1
                    issue_count += 1
                    yield deepcopy(raw)
            elif kind == "diagnostic" and event == "run_finished":
                finished = True
                yield {**raw, "schema_version": 2, "error_count": error_count, "issue_count": issue_count}
            else:
                _require(kind == "diagnostic", "Tipo de registro desconhecido.")
                yield deepcopy(raw)
        for diagnostic in pending.values():
            yield diagnostic
    except (KeyError, TypeError) as error:
        raise ValueError("Registro v1 incompleto ou inválido.") from error
    finally:
        close = getattr(source, "close", None)
        if close is not None:
            close()
    if producer_error is not None:
        raise producer_error
