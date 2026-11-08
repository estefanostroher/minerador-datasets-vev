"""Contrato portátil do JSONL de validação, independente de Docker e arquivos temporários."""

import base64
from collections import Counter
from datetime import datetime, timezone
import hashlib
import re

from .validation_evaluation import (REPOSITORY_CRITERIA, INSTANCE_CRITERIA, aggregate, _phase_criterion,
                                    _suite_criterion, operational_issues, _environment_criterion, _coverage,
                                    evaluate_resources, _count_instance, _count_instance_v1,
                                    LOCAL_INSTANCE_CRITERIA, execution_prerequisites,
                                    associated_tests_execution, provisional_resources,
                                    PAIR_INSTANCE_CRITERIA, PAIR_REPOSITORY_CRITERIA, PAIR_POLICY)
from .validation_evidence import _json_value, case_identity
from .validation_recipe import _hash as recipe_hash, _validate_recipe, _path


STATES = ("approved", "rejected", "not_evaluated")


def utc_now():
    """Produz instante UTC explícito para todos os eventos de publicação."""
    return datetime.now(timezone.utc).isoformat()


def encode_bytes(raw):
    """Preserva bytes integrais, usando base64 somente quando UTF-8 não é reversível."""
    if type(raw) is not bytes:
        raise ValueError("Captura deve conter bytes originais.")
    result = {"byte_count": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    try:
        return {**result, "encoding": "utf-8", "text": raw.decode("utf-8")}
    except UnicodeError:
        return {**result, "encoding": "base64", "data": base64.b64encode(raw).decode("ascii")}


def decision_counts(records):
    """Conta decisões com zeros explícitos e sem depender de ordem dos registros."""
    counts = Counter(record["decision"] for record in records)
    return {state: counts[state] for state in STATES}


def run_result(repositories, instances, operational_errors):
    """Distingue rejeição científica de ausência de avaliação útil por impedimento."""
    pending = any(r["decision"] == "not_evaluated" for r in [*repositories, *instances])
    if not pending and not operational_errors and not any(operational_issues(r) for r in repositories):
        return "completed"
    # Escopo/receita e a não necessidade de preparação são pré-condições, não
    # avaliação útil isolada de um projeto que sequer pôde executar.
    useful = any(r["criteria"][key]["status"] in {"approved", "rejected"}
                 for r in instances for key in ("original_target_execution", "minimum_valid_cases")) or any(
        r["criteria"][key]["status"] in {"approved", "rejected"}
         for r in repositories for key in ("reproducible_environment", "dependency_installation", "complete_unit_suite", "no_external_services")
         if key in r["criteria"])
    return "partial" if useful else "failed"


def _require(condition, message):
    """Recusa corrupção em vez de reparar contagens ou preencher resultados."""
    if not condition:
        raise ValueError(message)


def _hash(value, length=64):
    """Confere digest hexadecimal sem coerção de tipos."""
    return type(value) is str and re.fullmatch(r"[0-9a-f]{%d}" % length, value) is not None


def _counts(value):
    """Exige contagens inteiras reais em todos os estados, inclusive zeros."""
    return type(value) is dict and set(value) == set(STATES) and all(type(v) is int and v >= 0 for v in value.values())


def _criteria(record, names):
    """Confere os critérios obrigatórios e sua decisão agregada."""
    values = record.get("criteria")
    _require(type(values) is dict and set(values) == set(names), "Critérios obrigatórios divergentes.")
    for value in values.values():
        _require(type(value) is dict and value.get("status") in STATES
                 and type(value.get("code")) is str and bool(value["code"])
                 and type(value.get("message")) is str and bool(value["message"])
                 and type(value.get("evidence")) is dict, "Critério sem motivo ou evidência válida.")
        _require("required" not in value or type(value["required"]) is bool, "Necessidade de fase inválida.")
    _require(record.get("decision") == aggregate(values), "Decisão não corresponde aos critérios.")


def _bytes(value):
    """Confere que o envelope preserva exatamente os bytes e seu digest."""
    _require(type(value) is dict, "Envelope de bytes inválido.")
    try:
        raw = value["text"].encode("utf-8") if value["encoding"] == "utf-8" else base64.b64decode(value["data"], validate=True)
    except (KeyError, ValueError, TypeError, UnicodeError) as error:
        raise ValueError("Captura binária inválida.") from error
    _require(value.get("encoding") in {"utf-8", "base64"} and type(value.get("byte_count")) is int
             and value["byte_count"] == len(raw) and value.get("sha256") == hashlib.sha256(raw).hexdigest(), "Bytes/digest da captura divergentes.")


def _captures(value):
    """Verifica recursivamente capturas incorporadas e recusa referências de spool."""
    if type(value) is dict:
        _require(not ({"stdout_path", "stderr_path"} & set(value)), "Evidência depende de spool temporário.")
        if "encoding" in value and "byte_count" in value:
            _bytes(value)
        for item in value.values():
            _captures(item)
    elif type(value) is list:
        for item in value:
            _captures(item)


def _repository(record):
    """Confere identidade compartilhada, receita integral e fases científicas publicadas."""
    pair = record["schema_version"] == 3
    _criteria(record, PAIR_REPOSITORY_CRITERIA if pair else REPOSITORY_CRITERIA)
    snapshot = record.get("snapshot")
    if snapshot is not None:
        _require(type(snapshot) is dict and snapshot.get("repository_id") == record["repository_id"]
                 and snapshot.get("commit_sha") == record["commit_sha"], "Snapshot de outra origem.")
    resolution = record.get("recipe")
    _require(type(resolution) is dict and resolution.get("resolution_status") in {"resolved", "not_evaluated"}
             and _hash(resolution.get("catalog_sha256")), "Resolução de receita inválida.")
    recipe = resolution.get("recipe")
    if recipe is not None:
        _validate_recipe(recipe)
        _require(recipe.get("repository_id") == record["repository_id"] and recipe.get("commit_sha") == record["commit_sha"]
                 and recipe.get("repository_url") == record["repository_url"]
                 and resolution.get("recipe_sha256") == recipe_hash(recipe), "Identidade/hash de receita divergente.")
    else:
        _require(resolution.get("recipe_sha256") is None and resolution["resolution_status"] == "not_evaluated", "Receita ausente declarada resolvida.")
    if record["criteria"]["reproducible_environment"]["status"] == "approved":
        _require(type(snapshot) is dict and snapshot.get("format") == "git-bare-v1"
                 and snapshot.get("status") == "complete" and snapshot.get("cache_key") == f'{record["repository_id"]}/{record["commit_sha"]}'
                 and _hash(snapshot.get("tree_oid"), 40) and _hash(snapshot.get("manifest_sha256"))
                 and all(type(snapshot.get(key)) is int and snapshot[key] >= 0 for key in ("file_count", "total_blob_bytes")),
                 "Ambiente aprovado sem referência completa de snapshot.")
        environment = record.get("environment")
        _require(type(environment) is dict and recipe is not None
                 and _environment_criterion(recipe, resolution, {"snapshot": snapshot}, environment, record.get("unit_suite", {}))["status"] == "approved",
                 "Ambiente aprovado sem ferramentas, imagem, dependências e locks efetivos coerentes.")
        _require(record["criteria"]["reproducible_environment"]["evidence"].get("snapshot") == snapshot
                 and environment.get("lockfiles") == record.get("lockfiles"), "Vínculos de snapshot/lockfiles divergentes.")
        _require(type(environment.get("image_id")) is str and re.fullmatch(r"sha256:[0-9a-f]{64}", environment["image_id"])
                 and type(environment.get("os_release")) is str and bool(environment["os_release"])
                 and environment.get("daemon", {}).get("OSType") == "linux"
                 and type(environment["daemon"].get("ServerVersion")) is str and bool(environment["daemon"]["ServerVersion"]),
                 "Identificação efetiva da imagem/base Linux/daemon ausente.")
        locks = environment["lockfiles"]
        _require(len({lock["path"] for lock in locks}) == len(locks)
                 and all(any(actual.get("path") == lock["path"] and actual.get("manager") == lock["manager"]
                             and actual.get("sha256") == lock["sha256"] for actual in locks) for lock in recipe["lockfiles"]),
                 "Identidade de lockfile efetivo divergente da receita.")
    phases = record.get("execution")
    _require(type(phases) is dict and set(phases) == {"installation", "preparation", "unit_suite"}, "Fases ausentes/divergentes.")
    for name, phase in phases.items():
        _require(type(phase) is dict and phase.get("phase") == name and type(phase.get("required")) is bool
                 and phase.get("status") in {"not_started", "completed", "failed"}
                 and type(phase.get("commands")) is list, "Resultado de fase inválido.")
        for command in phase["commands"]:
            _require(type(command) is dict and type(command.get("argv")) is list and bool(command["argv"])
                     and all(type(arg) is str for arg in command["argv"]) and type(command.get("cwd")) is str
                     and (command.get("exitcode") is None or type(command["exitcode"]) is int), "Comando ou exit code inválido.")
        if name != "unit_suite" and phase["required"]:
            key = "dependency_installation" if name == "installation" else "required_preparation"
            _require(record["criteria"][key]["status"] == _phase_criterion(phase)["status"], "Critério diverge da fase observada.")
    observation = record.get("unit_suite")
    _require(type(observation) is dict, "Observação de suíte inválida.")
    if record["schema_version"] >= 2:
        for command in observation.get("commands", []):
            for state in command.get("evidence", {}).get("emitters", {}).values():
                event = state.get("started") or {}
                _require(event.get("repository_id") == record["repository_id"] and event.get("commit_sha") == record["commit_sha"]
                         and event.get("instrument_id") == observation.get("instrument_sha256"), "Stream local pertence a outra origem/instrumento.")
    _require(record["criteria"]["complete_unit_suite"]["status"] == _suite_criterion(observation, phases["unit_suite"], legacy=record["schema_version"] == 1)["status"], "Resultado científico diverge da suíte original.")
    summary = record.get("unit_suite_summary")
    cases = [case for command in observation.get("commands", []) for case in command.get("evidence", {}).get("cases", {}).values()]
    _require(type(summary) is dict and type(summary.get("case_count")) is int and summary["case_count"] == len(cases)
             and type(summary.get("attempt_count")) is int
             and summary["attempt_count"] == sum(len((c.get("finished") or {}).get("attempts", [])) for c in cases), "Resumo de casos/tentativas divergente.")
    statuses = summary.get("case_statuses")
    _require(type(statuses) is dict and set(statuses) == {"passed", "failed", "skipped", "todo"}
             and all(type(v) is int and v == sum((c.get("finished") or {}).get("status") == s for c in cases)
                     for s, v in statuses.items()), "Resumo de estados de casos divergente.")
    resource = record["criteria"].get("no_external_services")
    universe = record.get("resource_universe")
    if universe is not None:
        _require(type(universe) is dict and {"snapshot", "analysis_complete", "files", "sha256"} <= set(universe)
                 <= {"snapshot", "analysis_complete", "files", "sha256", "manifest"}
                 and universe["snapshot"] == snapshot and snapshot is not None and type(universe["analysis_complete"]) is bool
                 and type(universe["files"]) is list
                 and universe["sha256"] == recipe_hash({k: v for k, v in universe.items() if k != "sha256"}),
                 "Universo de recursos sem vínculo ao snapshot/hash.")
        paths = set()
        for file in universe["files"]:
            _require(type(file) is dict and set(file) == {"path", "sha256", "line_count"}
                     and type(file["path"]) is str and file["path"] not in paths and _hash(file["sha256"])
                     and type(file["line_count"]) is int and file["line_count"] >= 0, "Arquivo do universo de recursos inválido.")
            _path(file["path"])
            paths.add(file["path"])
    if pair:
        _require(type(record.get("resource_diagnostics")) is dict, "Diagnóstico de recursos ausente.")
    if resource is not None and resource["status"] in {"approved", "rejected"}:
        audit = record.get("resource_evidence")
        _require(universe is not None and resource == evaluate_resources(recipe, {"resource_universe": universe}, audit, observation, record.get("environment", {})),
                 "Decisão de recursos diverge da prova rederivada sobre fontes, modelos e caminhos executados.")


def _instance(record, repository, resource_scope=None):
    """Confere testemunhas de todos os casos creditados e vínculos ao plano T3."""
    pair = record["schema_version"] == 3
    local = record["schema_version"] >= 2
    _criteria(record, PAIR_INSTANCE_CRITERIA if pair else LOCAL_INSTANCE_CRITERIA if local else INSTANCE_CRITERIA)
    _require(record.get("repository_ref") == repository["repository_id"], "Referência de repositório divergente.")
    if local:
        _require(record["criteria"]["execution_prerequisites"] == execution_prerequisites(repository["criteria"]), "Pré-requisitos individuais divergentes.")
    else:
        _require(record["criteria"]["repository_execution"]["status"] == repository["decision"], "Herança da decisão de origem divergente.")
    counts, cases = record.get("test_cases"), record.get("case_evidence")
    _require(type(counts) is dict and type(cases) is list and type(counts.get("counting_complete")) is bool
             and counts.get("count_kind") in {"unknown", "exact", "lower_bound"}
             and type(counts.get("associations")) is list, "Contagem ou associações inválidas.")
    count = counts.get("valid_count")
    _require(count is None or type(count) is int and count >= 0, "Contagem inválida.")
    _require(counts.get("original_entry_observed") is None or type(counts["original_entry_observed"]) is bool, "Observação de entrada original inválida.")
    _require((count == len(cases) if count is not None else not cases)
             and (counts["count_kind"] == "exact") == counts["counting_complete"]
             and (counts["count_kind"] == "unknown") == (count is None)
             and (not counts["counting_complete"] or count is not None), "Contagem/completude sem testemunhas.")
    seen = set()
    observation = repository["unit_suite"]
    plan = observation.get("plan", {})
    for case in cases:
        case_id = case.get("case_id")
        _require(_hash(case_id) and case_id not in seen and case_identity(case["identity"]) == case_id, "Identidade de caso duplicada/divergente.")
        seen.add(case_id)
        _require(case.get("instance_id") == record["instance_id"] and case.get("original_entered") is True
                 and case.get("target") == plan.get("targets", {}).get(record["instance_id"])
                 and case.get("target") is not None and case.get("instrument_sha256") == observation.get("instrument_sha256"), "Alvo ou instrumento divergente.")
        _require(case.get("declaration") == plan.get("declarations", {}).get(case["identity"]["declaration_id"])
                 and case["declaration"].get("associated") is True and _hash(case.get("instrument_sha256")), "Declaração/instrumento sem identidade confiável.")
        index = case.get("command_index")
        _require(type(index) is int and 0 <= index < len(observation.get("commands", [])), "Comando de caso inválido.")
        evidence = observation["commands"][index].get("evidence", {})
        original = evidence.get("cases", {}).get(case_id)
        _require(original and original["started"]["case"] == case["identity"] and original["finished"] == case.get("result"), "Caso não consta da observação compartilhada.")
        attempts = case.get("attempts")
        _require(attempts == original["finished"]["attempts"] and attempts
                 and original["finished"]["status"] == "passed" and "fails" not in case["identity"]["modifiers"]
                 and all(a["status"] == "passed" and a["expected_failure"] is False for a in attempts), "Caso creditado sem resultado positivo elegível.")
        _require(case.get("points") and case.get("calls"), "Caso sem ponto ou chamada original.")
        _require(type(case.get("original_tests")) is list and case["original_tests"]
                 and all(test in [a["test"] for a in counts["associations"]] and type(test.get("framework")) is str
                         and type(test.get("start_line")) is int and type(test.get("end_line")) is int
                         and 1 <= test["start_line"] <= test["end_line"] for test in case["original_tests"]), "Intervalo/framework original do teste ausente.")
        point_ids = set()
        for point in case["points"]:
            _require(point == plan.get("points", {}).get(point.get("id")) and point["instance_id"] == record["instance_id"]
                     and point["declaration_id"] == case["identity"]["declaration_id"]
                     and type(point.get("association")) is dict and point["association"].get("kind") == "direct_call"
                     and point["association"].get("resolved_target", {}).get("path") == case["target"]["path"]
                     and point["id"] not in point_ids, "Ponto direto sem vínculo estático ou repetido.")
            point_ids.add(point["id"])
        call_ids = set()
        for call in case["calls"]:
            direct = call.get("direct", {})
            token = (direct.get("emitter_id"), direct.get("call_id"))
            _require(call in evidence.get("calls", []) and call.get("entered")
                     and direct.get("case_id") == case_id and direct.get("instance_id") == record["instance_id"]
                     and direct.get("point_id") in point_ids and type(direct.get("attempt")) is int
                     and 0 <= direct["attempt"] < len(attempts) and token not in call_ids, "Chamada creditada sem entrada original verificável ou repetida.")
            call_ids.add(token)
    minimum = record["criteria"]["minimum_valid_cases"]["status"]
    if local:
        unit = {"instance_id": record["instance_id"], "tests": [association["test"] for association in counts["associations"]]}
        expected_counts, expected_cases = _count_instance(unit, observation)
        _require(counts == expected_counts and cases == expected_cases, "Provas locais divergem dos streams verificados.")
        expected = "approved" if count is not None and count >= 4 else "rejected" if counts["counting_complete"] else "not_evaluated"
        _require(minimum == expected, "Limiar não corresponde à contagem local.")
        original_status = "approved" if cases else "rejected" if counts["counting_complete"] else "not_evaluated"
        _require(record["criteria"]["original_target_execution"]["status"] == original_status, "Prova existencial original divergente.")
        if pair:
            _require(record["criteria"]["minimum_valid_cases"]["evidence"] == {
                "valid_count": count, "counting_complete": counts["counting_complete"]}, "Metadados do limiar divergem das testemunhas.")
            _require(record["criteria"]["original_target_execution"]["evidence"] == {
                "case_ids": [case["case_id"] for case in cases]}, "Metadados de identidade divergem das testemunhas.")
        expected_local = associated_tests_execution(unit, observation)
        _require(record["criteria"]["associated_tests_execution"] == expected_local, "Passagem local diverge dos casos/hooks associados.")
        if pair:
            _require(resource_scope == "diagnostic_only", "Política do par sem escopo diagnóstico explícito.")
        elif resource_scope == "global_provisional":
            _require(record["criteria"]["no_external_services"] == provisional_resources(repository["criteria"]["no_external_services"]), "Gate provisório histórico de recursos divergente.")
        else:
            from .validation_resources_local import evaluate_instance_resources
            expected_resources = evaluate_instance_resources(unit, repository["recipe"].get("recipe"), repository.get("resource_universe"),
                repository.get("resource_evidence"), repository.get("resource_local_method"),
                repository.get("instance_resource_evidence", {}).get(record["instance_id"]), observation, repository.get("environment", {}))
            _require(record["criteria"]["no_external_services"] == expected_resources, "Recursos locais divergem do fechamento original rederivado.")
    elif repository["decision"] == "rejected":
        _require(not cases and count is None and all(record["criteria"][k]["status"] == "not_evaluated"
                 for k in INSTANCE_CRITERIA if k != "repository_execution"), "Execução individual fabricada após rejeição da origem.")
    else:
        expected_counts, expected_cases = _count_instance_v1({"instance_id": record["instance_id"],
            "tests": [association["test"] for association in counts["associations"]]}, observation)
        _require(counts == expected_counts and cases == expected_cases,
                 "Contagem/testemunhas divergem da observação e das limitações da instância.")
        expected = "approved" if count is not None and count >= 4 else "rejected" if counts["counting_complete"] else "not_evaluated"
        _require(minimum == expected, "Limiar não corresponde à contagem.")
        _require(record["criteria"]["no_external_services"]["status"] == repository["criteria"]["no_external_services"]["status"], "Prova de recursos diverge da origem.")
        entry_observed = any(call.get("entered") and call["direct"]["instance_id"] == record["instance_id"]
                             for command in observation.get("commands", []) for call in command.get("evidence", {}).get("calls", []))
        _require(counts["original_entry_observed"] == entry_observed if count is not None else counts["original_entry_observed"] is None, "Entrada original diverge das chamadas observadas.")
        original_status = "approved" if entry_observed and counts["counting_complete"] else "rejected" if counts["counting_complete"] else "not_evaluated"
        _require(record["criteria"]["original_target_execution"]["status"] == original_status, "Identidade original diverge das testemunhas/completude.")
    coverage = record.get("coverage")
    _require(type(coverage) is dict and set(coverage) == {"associated_tests", "full_unit_suite"}, "Escopos de cobertura ausentes.")
    for scope, metric in coverage.items():
        _require(type(metric) is dict and metric.get("scope") == scope and metric.get("status") in {"unavailable", "available", "not_applicable"}, "Métrica/escopo de cobertura inválido.")
        if metric["status"] == "unavailable":
            _require(metric.get("lines") is None and metric.get("branches") is None and bool(metric.get("reason")), "Cobertura indisponível confundida com zero.")
        else:
            _require(scope == "full_unit_suite", "T3 não fornece atribuição de cobertura por caso para associated_tests.")
            _validate_coverage_support(record, repository, metric)
            for key in ("lines", "branches"):
                value = metric.get(key)
                if value is None:
                    _require(bool(metric.get("reason")), "Métrica ausente sem motivo.")
                    continue
                _require(type(value) is dict and type(value.get("covered")) is int and type(value.get("total")) is int
                         and 0 <= value["covered"] <= value["total"], "Numerador/denominador inválido.")
                _require(value.get("percentage") == (100 * value["covered"] / value["total"] if value["total"] else None)
                          and value.get("status") == ("available" if value["total"] else "not_applicable"), "Percentual ou denominador zero inválido.")


def _validate_coverage_support(record, repository, metric):
    """Reconstrói a medição opcional dos dados publicados, sem executar ou revalidar T3."""
    observation = repository["unit_suite"]
    target = observation.get("plan", {}).get("targets", {}).get(record["instance_id"])
    _require(type(target) is dict, "Cobertura disponível sem alvo original.")
    path = target["path"]
    hashes = {item["path"]: item["sha256"] for item in observation["plan"].get("plans", [])}
    _require(_hash(hashes.get(path)), "Cobertura sem hash de fonte do plano verificado.")
    sources = set()
    for command in observation.get("commands", []):
        _require("evidence" in command, "Cobertura depende de relatório bruto recusado pelo protocolo.")
        for suite in (command.get("report") or {}).get("suites", []):
            for key, entry in (suite.get("coverage") or {}).items():
                if key not in {path, "/workspace/" + path}:
                    continue
                mapping = entry.get("inputSourceMap") or {}
                for source_path, content in zip(mapping.get("sources", []), mapping.get("sourcesContent", [])):
                    if source_path.removeprefix("/workspace/") == path and type(content) is str and hashlib.sha256(content.encode()).hexdigest() == hashes[path]:
                        sources.add(content)
    _require(len(sources) == 1, "Cobertura sem fonte/source map vinculados ao plano original.")
    expected = _coverage({"instance_id": record["instance_id"], "function": {"path": path}}, observation,
                         {"context": {"files": [{"path": path, "content": next(iter(sources))}]}})["full_unit_suite"]
    _require(expected["status"] == "available" and metric == expected, "Medição de cobertura sem suporte ou divergente dos dados do provider.")


def _validate_output_records(records):
    """Valida uma execução fechada e retorna seu run_finished; corrupção gera ValueError.

    Aceita iterável de objetos já decodificados estritamente. O consumidor que lê
    JSONL deve recusar chaves repetidas/NaN antes desta chamada (decode_events).
    """
    records = list(records)
    _require(len(records) >= 2, "Execução sem abertura/fechamento.")
    repositories, instances, ids, current, operational = [], [], set(), None, 0
    local_diagnostics = False
    for index, record in enumerate(records):
        _json_value(record)
        _require(type(record) is dict and type(record.get("schema_version")) is int and record["schema_version"] in {1, 2, 3}
                 and record["schema_version"] == records[0].get("schema_version")
                 and record.get("stage") == "validation", "Versão/etapa inválida.")
        kind = record.get("record_type")
        _require(kind in {"run_started", "repository", "instance", "diagnostic", "run_finished"}, "Registro desconhecido.")
        try:
            stamp = datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00"))
            _require(stamp.utcoffset() is not None and stamp.utcoffset().total_seconds() == 0, "Instante não está em UTC.")
        except (KeyError, ValueError, TypeError, AttributeError) as error:
            raise ValueError("Instante UTC inválido.") from error
        _captures(record)
        if kind == "run_started":
            _require(index == 0, "Abertura fora de ordem.")
            _validate_policy(record)
        elif kind == "run_finished":
            _require(index == len(records) - 1, "Fechamento fora de ordem.")
        elif kind == "repository":
            identity = record.get("repository_id")
            _require(type(identity) is int and identity > 0 and identity not in {r["repository_id"] for r in repositories}
                     and _hash(record.get("commit_sha"), 40) and type(record.get("repository_url")) is str, "Origem duplicada/inválida.")
            _require(all(k in record for k in ("snapshot", "recipe", "environment", "lockfiles", "execution", "unit_suite", "instance_decisions")), "Evidência compartilhada ausente.")
            _repository(record)
            repositories.append(record)
            current = record
            local_diagnostics = False
        elif kind == "instance":
            _require(current and not local_diagnostics and current["repository_id"] == record.get("repository_id"), "Instância fora do grupo original.")
            identity = record.get("instance_id")
            _require(_hash(identity) and identity not in ids, "ID de instância inválido/duplicado.")
            ids.add(identity)
            _instance(record, current, records[0].get("policy", {}).get("resource_gate_scope"))
            instances.append(record)
        else:
            _require(current and record.get("repository_id") == current["repository_id"] and type(record.get("message")) is str
                     and type(record.get("code")) is str and type(record.get("operational")) is bool, "Diagnóstico local inválido.")
            operational += int(record["operational"])
            local_diagnostics = True
    started, finished = records[0], records[-1]
    _require(started.get("record_type") == "run_started" and finished.get("record_type") == "run_finished", "Execução não está fechada.")
    _validate_run_started(started, repositories)
    source = started.get("source")
    _require(type(source) is dict and _hash(source.get("sha256")) and type(source.get("schema_version")) is int and source["schema_version"] == 3
             and source.get("status") in {"completed", "partial", "failed"} and type(source.get("path")) is str and bool(source["path"]), "Proveniência inválida.")
    for field in ("input_count", "repository_count", "eligible_count", "eligible_repository_count"):
        _require(type(source.get(field)) is int and source[field] >= 0, "Contagem de origem inválida.")
    _require(_counts(source.get("decisions")) and sum(source["decisions"].values()) == source["input_count"]
             and source["decisions"]["approved"] == source["eligible_count"]
             and source["eligible_repository_count"] <= source["repository_count"], "Decisões de origem divergentes.")
    _require(source["eligible_count"] == len(instances) and source["eligible_repository_count"] == len(repositories), "População selecionada diverge da origem.")
    for repository in repositories:
        local = [r for r in instances if r["repository_id"] == repository["repository_id"]]
        _require(local and _counts(repository["instance_decisions"]) and repository["instance_decisions"] == decision_counts(local), "Soma local divergente.")
        if operational_issues(repository):
            _require(any(r.get("record_type") == "diagnostic" and r.get("repository_id") == repository["repository_id"]
                         and r.get("operational") is True for r in records), "Impedimento operacional sem diagnóstico local.")
    _require(all(type(finished.get(k)) is int and finished[k] >= 0 for k in ("repository_count", "instance_count", "operational_errors"))
             and _counts(finished.get("repository_decisions")) and _counts(finished.get("instance_decisions")), "Totais finais inválidos.")
    _require(finished.get("source_sha256") == source["sha256"] and finished.get("repository_count") == len(repositories)
             and finished.get("instance_count") == len(instances) and finished.get("repository_decisions") == decision_counts(repositories)
             and finished.get("instance_decisions") == decision_counts(instances)
             and finished.get("operational_errors") == operational, "Fechamento/contagens divergentes.")
    _require(finished.get("status") == run_result(repositories, instances, operational), "Estado final divergente.")
    return finished


def _validate_policy(started):
    """Discrimina a semântica antes de interpretar qualquer prova ou decisão."""
    policy = started.get("policy")
    if started["schema_version"] == 3:
        _require(type(policy) is dict and recipe_hash(policy) == recipe_hash(PAIR_POLICY), "Política de comportamento ausente ou incompatível.")
        return
    local = started["schema_version"] == 2
    keys = {"suite_passes", "minimum_valid_cases", "no_corrective_retries"}
    if local:
        keys |= {"instance_execution_scope", "resource_gate_scope"}
    _require(type(policy) is dict and set(policy) == keys
             and type(policy["suite_passes"]) is int and policy["suite_passes"] == 1
             and type(policy["minimum_valid_cases"]) is int and policy["minimum_valid_cases"] == 4
              and policy["no_corrective_retries"] is True, "Política de validação ausente ou incompatível.")
    if local:
        _require(policy["instance_execution_scope"] == "associated_declarations_and_module_hooks"
                  and policy["resource_gate_scope"] in {"global_provisional", "associated_closure_v1"}, "Escopos da política local divergentes.")


def _validate_run_started(started, repositories):
    """Exige política fixa, versões e limites vinculados às receitas de cada origem."""
    _validate_policy(started)
    if "replay" in started:
        replay = started["replay"]
        _require(started["schema_version"] == 3 and type(replay) is dict and set(replay) == {
            "kind", "path", "sha256", "schema_version", "policy", "original_finished_at", "candidate_execution"}
            and replay.get("kind") == "posthoc_saved_evidence" and replay.get("candidate_execution") is False
            and type(replay.get("path")) is str and bool(replay["path"]) and _hash(replay.get("sha256"))
            and type(replay.get("schema_version")) is int and replay["schema_version"] == 2,
            "Proveniência do replay inválida.")
        _validate_policy({"schema_version": 2, "policy": replay["policy"]})
        try:
            instant = datetime.fromisoformat(replay["original_finished_at"].replace("Z", "+00:00"))
            _require(instant.utcoffset() is not None and instant.utcoffset().total_seconds() == 0,
                     "Fechamento histórico não está em UTC.")
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("Instante histórico do replay inválido.") from error
    _require(type(started.get("miner_version")) is str and re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", started["miner_version"])
             and type(started.get("instrument_protocol_version")) is int and started["instrument_protocol_version"] == 1,
             "Versão do minerador/protocolo ausente ou inválida.")
    limits = started.get("limits")
    _require(type(limits) is list and len(limits) == len(repositories), "Limites de abertura não cobrem as origens selecionadas.")
    for item, repository in zip(limits, repositories):
        recipe = repository["recipe"].get("recipe")
        _require(type(item) is dict and set(item) == {"repository_id", "limits"}
                 and type(item["repository_id"]) is int and item["repository_id"] == repository["repository_id"],
                 "Limites associados a origem divergente.")
        if recipe is None:
            _require(item["limits"] is None, "Origem sem receita não pode declarar limites efetivos.")
        else:
            _require(type(item["limits"]) is dict and item["limits"] == recipe["limits"]
                     and all(type(v) is int and v > 0 for v in item["limits"].values()), "Limites divergem da receita ou possuem tipos inválidos.")


def validate_output_records(records):
    """Retorna run_finished validado ou ValueError uniforme para corrupção estrutural.

    Recursos modelados exigem replay AST com Node/TypeScript confiáveis; não abre
    caminhos candidatos nem recupera snapshots. O leitor de bytes
    deve usar parsing JSON estrito antes de fornecer os registros decodificados.
    """
    try:
        return _validate_output_records(records)
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError) as error:
        raise ValueError("Estrutura de registros da validação inválida.") from error
