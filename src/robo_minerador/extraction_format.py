"""Publica referências compactas sem alterar evidências da extração legada."""

from collections import Counter
from copy import deepcopy
import json
import re

from .extraction_analyzer import VALIDATION

CONTEXT_POLICY = "versioned_snapshot_static_evidence_v1"


def validate_snapshot_repository(repository):
    """Valida referência portátil V3 sem alegar verificação dos objetos locais."""
    context = repository.get("context", {})
    _require(isinstance(context, dict), "Contexto V3 inválido.")
    reference = context.get("snapshot")
    _require(repository.get("context_policy") == CONTEXT_POLICY, "Política V3 ausente.")
    _require(type(context.get("analysis_complete")) is bool, "Completude de análise ausente.")
    if reference is None:
        _require(repository.get("extraction_status") == "failed"
                 and repository.get("summary", {}).get("instance_count") == 0,
                 "Snapshot V3 ausente.")
        _require("snapshot" in context and repository.get("execution_requirements") == [],
                 "Falha V3 sem referência explícita ou com requisitos não verificados.")
        _require(context["analysis_complete"] is False, "Análise sem snapshot não pode ser completa.")
        return
    fields = {"format", "cache_key", "repository_id", "commit_sha", "tree_oid",
              "manifest_sha256", "file_count", "total_blob_bytes", "status"}
    _require(isinstance(reference, dict) and set(reference) == fields, "Referência V3 inválida.")
    identity, sha = repository.get("repository_id"), repository.get("commit_sha")
    _require(type(identity) is int and identity > 0 and isinstance(sha, str)
             and re.fullmatch(r"[0-9a-f]{40}", sha) is not None, "Origem V3 inválida.")
    _require(reference["repository_id"] == identity and type(reference["repository_id"]) is int
             and reference["commit_sha"] == sha and reference["cache_key"] == f"{identity}/{sha}"
             and reference["format"] == "git-bare-v1" and reference["status"] == "complete",
             "Snapshot de outra origem.")
    for field, length in (("tree_oid", 40), ("manifest_sha256", 64)):
        _require(isinstance(reference[field], str)
                 and re.fullmatch(r"[0-9a-f]{%d}" % length, reference[field]) is not None,
                 "Hash V3 inválido.")
    _require(all(type(reference[field]) is int and reference[field] >= 0
                 for field in ("file_count", "total_blob_bytes")), "Tamanho V3 inválido.")
    requirements = repository.get("execution_requirements")
    _require(isinstance(requirements, list), "Requisitos V3 ausentes.")
    for item in requirements:
        _require(isinstance(item, dict) and set(item) == {"type", "path", "status"}
                 and item["type"] in {"test_configuration", "environment", "external_dependency",
                     "dynamic_loading", "external_resource", "gitlink", "lfs_pointer"}
                 and isinstance(item["path"], str) and bool(item["path"])
                 and item["status"] == "not_evaluated", "Requisito V3 inválido.")


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
    """Expande V1/V2/V3 para inspeção; não verifica cache nem promove legado."""
    _require(instance.get("record_type") == "instance" and repository.get("record_type") == "repository",
             "Tipos incompatíveis para expansão.")
    version = instance.get("schema_version", 1)
    repository_version = repository.get("schema_version", 1)
    _require(type(version) is int and version in (1, 2, 3)
             and type(repository_version) is int and repository_version in (1, 2, 3)
             and (version == 3) == (repository_version == 3), "Versão desconhecida ou misturada.")
    if version == 3:
        validate_snapshot_repository(repository)
    _require(instance.get("repository_id") == repository.get("repository_id")
             and "repository_id" in instance, "Repositório incompatível com a instância.")
    result = deepcopy(instance)
    if version == 3:
        result.pop("schema_version")
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


def compact_records(records, *, schema_version=2):
    """Consome adaptador V1, publica V2/V3 e fecha a origem em cancelamento."""
    _require(type(schema_version) is int and schema_version in (2, 3), "Versão de publicação inválida.")
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
            _require(isinstance(raw, dict) and type(raw.get("schema_version", 1)) is int
                     and raw.get("schema_version", 1) == 1, "Registro não é v1.")
            kind, event = raw.get("record_type"), raw.get("event")
            _require(not finished, "Registro após encerramento.")
            if event == "run_started" and kind == "diagnostic":
                _require(not started, "Início duplicado.")
                started = True
                yield {**raw, "schema_version": schema_version}
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
                if schema_version == 3:
                    validate_snapshot_repository(repository)
                    repository["schema_version"] = 3
                else:
                    _require("context_policy" not in repository and "snapshot" not in repository["context"],
                             "Snapshot V3 não pode ser publicado como legado.")
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
                yield {**instance, "schema_version": 3} if schema_version == 3 else instance
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
                yield {**raw, "schema_version": schema_version, "error_count": error_count, "issue_count": issue_count}
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
