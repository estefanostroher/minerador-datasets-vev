"""Aplica critérios científicos a evidências verificadas, sem executar candidatos."""

from copy import deepcopy
import hashlib
import json
import re


REPOSITORY_CRITERIA = ("reproducible_environment", "unit_suite_scope", "dependency_installation",
                       "required_preparation", "complete_unit_suite", "no_external_services")
INSTANCE_CRITERIA = ("repository_execution", "original_target_execution", "minimum_valid_cases", "no_external_services")
LOCAL_INSTANCE_CRITERIA = ("execution_prerequisites", "associated_tests_execution", "original_target_execution", "minimum_valid_cases", "no_external_services")
PAIR_INSTANCE_CRITERIA = LOCAL_INSTANCE_CRITERIA[:-1]
PAIR_REPOSITORY_CRITERIA = REPOSITORY_CRITERIA[:-1]
PAIR_POLICY = {"version": 2, "suite_passes": 1, "minimum_valid_cases": 4,
               "no_corrective_retries": True, "instance_execution_scope": "function_original_tests_behavior",
               "resource_gate_scope": "diagnostic_only"}
OPERATIONAL_FLAGS = ("oom", "timeout", "cancelled", "daemon_error", "capture_loss", "inspect_error", "cleanup_error")


def aggregate(criteria):
    """Faz rejeição prevalecer e exige todos os critérios aprovados para aprovação."""
    states = [item["status"] for item in criteria.values()]
    return "rejected" if "rejected" in states else "approved" if all(s == "approved" for s in states) else "not_evaluated"


def minimum_cases_status(valid_cases, counting_complete):
    """Decide o limiar sem confundir uma contagem incompleta com ausência de casos."""
    if type(valid_cases) is not int or valid_cases < 0 or type(counting_complete) is not bool:
        raise ValueError("Contagem e completude devem ser informadas sem coerção de tipos.")
    if valid_cases >= 4:
        return "approved"
    return "rejected" if counting_complete else "not_evaluated"


def criterion(status="not_evaluated", code="OBSERVATION_UNAVAILABLE", message="Evidência obrigatória indisponível.", evidence=None, **extra):
    """Mantém motivo português e evidência local em cada critério obrigatório."""
    return {"status": status, "code": code, "message": message, "evidence": evidence or {}, **extra}


def _clean_commands(phase):
    """Reconhece término limpo sem atribuir causa a códigos de saída isolados."""
    return (phase.get("status") == "completed" and bool(phase.get("commands"))
            and all(c.get("exitcode") == 0 and not any(c.get(k) for k in OPERATIONAL_FLAGS)
                    for c in phase["commands"]))


def _log_text(command):
    """Lê somente logs já incorporados, sem depender de spools temporários."""
    return "\n".join(command.get(stream, {}).get("text", "") for stream in ("stdout", "stderr"))


def _phase_criterion(phase):
    """Separa falha candidata diagnosticada de rede, sinal, orçamento e causa ambígua."""
    if _clean_commands(phase):
        return criterion("approved", "PHASE_COMPLETED", "Todos os comandos necessários terminaram sem falhas.")
    for index, command in enumerate(phase.get("commands", [])):
        if any(command.get(k) for k in OPERATIONAL_FLAGS):
            return criterion(code="EXECUTOR_INTERRUPTED", message="Interrupção operacional da fase.", evidence={"command_index": index})
        text = _log_text(command)
        if re.search(r"\b(ECONNRESET|ECONNREFUSED|ETIMEDOUT|ENOTFOUND|EAI_AGAIN|E503|E502)\b|registry.*unavailable", text, re.I):
            return criterion(code="DEPENDENCY_NETWORK_UNAVAILABLE", message="Rede ou registry indisponível.", evidence={"command_index": index})
        if command.get("exitcode") not in (None, 0) and 0 < command["exitcode"] < 128:
            match = re.search(r"\b(EUSAGE|ELOCKVERIFY|ERR_PNPM_OUTDATED_LOCKFILE|ERR_PNPM_FROZEN_LOCKFILE_WITH_OUTDATED_LOCKFILE|TS\d{4})\b|package.json and (?:package-lock.json|npm-shrinkwrap.json).*in sync|Could not resolve [^\n]+|Module not found[^\n]*", text)
            if match:
                return criterion("rejected", "CANDIDATE_PHASE_FAILURE", "Falha candidata identificada no comando original.",
                                 {"command_index": index, "diagnostic": match.group(0)})
    return criterion(code="PHASE_CAUSE_UNRESOLVED", message="Fase não concluída; código de saída não comprova a causa.")


def _observation_complete(observation):
    """Exige concordância dos fechamentos T3, sem inferir completude de uma flag isolada."""
    return (observation.get("complete") is True and bool(observation.get("commands"))
            and all(command.get("complete") is True and command.get("evidence", {}).get("complete") is True
                    and command.get("census", {}).get("complete") is True
                    and (command.get("report") or {}).get("complete") is True
                    for command in observation["commands"]))


def _suite_criterion(observation, phase, legacy=False):
    """Preserva falhas inesperadas, inclusive retries verdes e hooks do framework."""
    for command_index, command in enumerate(observation.get("commands", [])):
        if "evidence" not in command:
            # collect preserva report bruto mesmo quando sua identidade/protocolo
            # foi recusada. Esse material serve a diagnóstico, não a rejeição.
            continue
        evidence = command.get("evidence", {})
        for case_id, case in evidence.get("cases", {}).items():
            finish = case.get("finished") or {}
            if (finish.get("status") == "failed" or any(a["status"] == "failed" and not a["expected_failure"]
                                                         for a in finish.get("attempts", []))):
                return criterion("rejected", "UNEXPECTED_TEST_FAILURE", "O framework registrou falha inesperada de teste.",
                                 {"command_index": command_index, "case_id": case_id, "result": finish})
        report = command.get("report") or {}
        if not legacy and _instrument_failure(report):
            return criterion(code="INSTRUMENT_FINALIZATION_FAILURE", message="Instrumento não finalizou casos; falha científica não demonstrada.")
        for suite in report.get("suites", []):
            failed = [task for task in suite.get("tasks", []) if task.get("result", {}).get("state") == "failed"]
            failed_modules = [module for module in suite.get("module_results", [])
                              if module.get("state") == "failed" or module.get("errors")]
            if suite.get("errors") or failed or failed_modules:
                return criterion("rejected", "FRAMEWORK_SUITE_FAILURE", "O framework registrou falha de suíte ou hook.",
                                 {"command_index": command_index, "suite_id": suite.get("suite_id"),
                                  "errors": suite.get("errors", []), "failed_tasks": failed, "failed_modules": failed_modules})
        # original_exit pertence ao candidato; exit 86 pertence ao bootstrap confiável.
        original = report.get("original_exit") or {}
        if original.get("signal"):
            return criterion(code="SUITE_SIGNAL", message="Processo original terminou por sinal sem causa científica comprovada.")
        if original.get("code") not in (None, 0):
            diagnosed = _phase_criterion(phase)
            if diagnosed["status"] == "rejected":
                return diagnosed
    if _observation_complete(observation) and _clean_commands(phase) and all(
            (c.get("report") or {}).get("original_exit", {}).get("code") == 0 for c in observation.get("commands", [])):
        return criterion("approved", "COMPLETE_SUITE_PASSED", "Uma passagem completa da suíte original foi aprovada.")
    return criterion(code="SUITE_OBSERVATION_INCOMPLETE", message="Execução ou observação obrigatória da suíte incompleta.")


def _hash(value):
    """Identifica estruturas de auditoria sem depender de ordem de chaves."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def resource_universe(verified):
    """Publica universo verificado com hashes/linhas, sem duplicar textos do contexto."""
    if not verified:
        return None
    if "resource_universe" in verified:
        return verified["resource_universe"]
    context = verified["context"]
    value = {"snapshot": verified["snapshot"], "analysis_complete": context.get("analysis_complete") is True,
             "files": [{"path": file["path"], "sha256": hashlib.sha256(file["content"].encode()).hexdigest(),
                         "line_count": len(file["content"].splitlines())} for file in context["files"] + verified.get("resource_sources", [])]}
    if "resource_manifest" in verified:
        value["manifest"] = deepcopy(verified["resource_manifest"])
    return {**value, "sha256": _hash(value)}


def resource_audit_reference(audit):
    """Referencia a prova compartilhada sem repetir fontes inteiras por instância."""
    if type(audit) is not dict:
        return {}
    return {"audit_sha256": _hash(audit), "scope_sha256": audit.get("scope_sha256"),
            "universe_sha256": audit.get("universe_sha256")}


def _evaluate_resources(recipe, verified, audit, observation, environment):
    """Confere auditoria explícita de recursos contra fontes e escopo verificados.

    O produtor fornece modelos/bindings, fontes originais, scope_sha256, files,
    complete e executed_paths. Cada finding contém linhas, resource, role e regra.
    O replay AST rederiva os efeitos antes de aceitar decisão. Aprovação exige
    auditoria integral do manifesto/contexto e caminhos executados cobertos.
    Ausência deste produtor não é suprida por sucesso da suíte ou network=none.
    """
    reference = resource_audit_reference(audit)
    pending = criterion(code="RESOURCE_PROOF_UNAVAILABLE", message="Ausência de serviços reais não comprovada no escopo unitário.", evidence=reference)
    if not recipe or not verified or recipe["unit_scope"]["status"] != "approved":
        return pending
    universe = resource_universe(verified)
    sources = {file["path"]: file for file in universe["files"]}
    services = recipe["unit_scope"]["external_services"]
    # Veredito negativo curado pertence ao catálogo versionado e ao SHA exato;
    # confere os arquivos de prova antes de interromper sem iniciar infraestrutura.
    if services["status"] == "rejected" and services["evidence"] and set(services["evidence"]) <= set(sources):
        return criterion("rejected", "RECIPE_PROHIBITED_UNIT_SERVICE", "Receita comprova serviço real necessário ao escopo unitário original.",
            {"recipe_scope": recipe["unit_scope"], "sources": [{"path": path, "sha256": sources[path]["sha256"]}
                for path in services["evidence"]], "recipe_evidence": [e for e in recipe["evidence"] if e["path"] in services["evidence"]]})
    if type(audit) is not dict:
        return pending
    from .validation_resources import verify_resource_audit, resource_paths
    if not verify_resource_audit(recipe, universe, audit, observation, environment):
        return criterion(code="RESOURCE_AUDIT_INVALID", message="Prova estrutural/modelos não conferem com fontes, ambiente e passagem originais.", evidence=reference)
    # Efeitos opacos em outros módulos/configs podem substituir uma API aparente.
    # Nem a rejeição pode confiar em chamada isolada quando esse fechamento falta.
    if audit.get("complete") is not True:
        return pending
    if audit.get("scope_sha256") != _hash(recipe["unit_scope"]) or audit.get("scope") != "unit_suite":
        return pending
    checked = set()
    fully_audited = True
    for item in audit.get("files", []):
        path = item.get("path")
        if path not in sources or path in checked or item.get("sha256") != sources[path]["sha256"]:
            return pending
        findings = item.get("findings")
        if type(findings) is not list or not findings and sources[path]["line_count"] != 0:
            return pending
        for finding in findings:
            if (type(finding.get("start_line")) is not int or type(finding.get("end_line")) is not int
                    or not 1 <= finding["start_line"] <= finding["end_line"] <= sources[path]["line_count"]
                    or finding.get("resource") not in {"none", "mock", "temporary_files", "embedded_database", "simulation_server", "real_service", "unknown"}
                    or finding.get("role") not in {"effective_dependency", "not_used", "unknown"}):
                return pending
            if finding["resource"] == "real_service" and finding["role"] == "effective_dependency":
                return criterion("rejected", "PROHIBITED_UNIT_SERVICE", "Serviço real separado necessário ao escopo unitário comprovado.", {**reference, "file": item})
        audited_lines = {line for finding in findings for line in range(finding["start_line"], finding["end_line"] + 1)}
        fully_audited = fully_audited and audited_lines == set(range(1, sources[path]["line_count"] + 1))
        checked.add(path)
    executed = set(resource_paths(observation)[0])
    if (audit.get("complete") is True and fully_audited and checked == set(sources) and checked
            and universe["analysis_complete"] is True and _observation_complete(observation)
            and executed and executed <= checked
            and type(audit.get("executed_paths")) is list and set(audit["executed_paths"]) == executed
            and all(f["resource"] != "unknown" and f["role"] != "unknown"
                    for item in audit["files"] for f in item["findings"])):
        return criterion("approved", "UNIT_RESOURCE_AUDIT", "Auditoria integral vinculada às fontes demonstra somente recursos permitidos.", reference)
    return pending


def evaluate_resources(recipe, verified, audit, observation=None, environment=None):
    """Falha fechado quando modelo, replay, fontes ou cobertura da auditoria não conferem."""
    try:
        return _evaluate_resources(recipe, verified, audit, observation or {}, environment or {})
    except (KeyError, ValueError, TypeError, AttributeError, UnicodeError, OSError, RuntimeError, RecursionError):
        return criterion(code="RESOURCE_AUDIT_INVALID", message="Auditoria de recursos não confere com fontes e escopo verificados.", evidence={"audit": audit})


def _association(test):
    """Retém localização e vínculo originais sem duplicar texto do teste."""
    return {k: deepcopy(v) for k, v in test.items() if k not in {"code", "text", "source", "reference_code", "title"}}


def _count_instance_v1(unit, observation):
    """Conta identidades runtime distintas com tentativa positiva e entrada original."""
    instance_id = unit["instance_id"]
    plan = observation.get("plan", {})
    points, declarations = plan.get("points", {}), plan.get("declarations", {})
    complete = _observation_complete(observation) and not any(
        limitation.get("instance_id") in (None, instance_id) for limitation in plan.get("limitations", []))
    credited, observed, original_entry = {}, [], False
    for command_index, command in enumerate(observation.get("commands", [])):
        evidence = command.get("evidence", {})
        for case_id, case in evidence.get("cases", {}).items():
            identity = case["started"]["case"]
            declaration = declarations.get(identity["declaration_id"], {})
            relevant = [p for p in points.values() if p["instance_id"] == instance_id and p["declaration_id"] == identity["declaration_id"]]
            if not relevant:
                continue
            finish = case.get("finished") or {}
            attempts = finish.get("attempts", [])
            original_entry = original_entry or any(call.get("entered") and call["direct"]["case_id"] == case_id
                and call["direct"]["instance_id"] == instance_id and call["direct"]["point_id"] in points
                for call in evidence.get("calls", []))
            calls = [call for call in evidence.get("calls", []) if call["direct"]["case_id"] == case_id
                     and call["direct"]["instance_id"] == instance_id and call.get("entered")
                     and call["direct"]["point_id"] in points and call["direct"]["attempt"] < len(attempts)
                     and attempts[call["direct"]["attempt"]]["status"] == "passed"
                     and not attempts[call["direct"]["attempt"]]["expected_failure"]]
            positive = (finish.get("status") == "passed" and "fails" not in identity["modifiers"]
                        and attempts and all(a["status"] == "passed" and not a["expected_failure"] for a in attempts))
            status = "valid" if positive and calls and declaration.get("associated") is True else finish.get("status", "unavailable")
            if "fails" in identity["modifiers"] and finish.get("status") == "passed":
                status = "expected_failure"
            if status == "passed":
                status = "call_not_reached" if complete else "unavailable"
            observed.append({"case_id": case_id, "status": status, "declaration_id": identity["declaration_id"]})
            if status == "valid":
                if case_id in credited:
                    complete = False
                    continue
                credited[case_id] = {"case_id": case_id, "command_index": command_index, "identity": identity,
                    "declaration": declaration, "result": finish, "attempts": attempts,
                    "points": [deepcopy(points[p]) for p in sorted({c["direct"]["point_id"] for c in calls})],
                    "original_tests": [_association(test) for test in unit.get("tests", []) if any(
                        p.get("path") == test["path"] and p.get("association") == test.get("association") for p in relevant)],
                    "calls": calls, "target": plan.get("targets", {}).get(instance_id),
                    "instance_id": instance_id, "original_entered": True,
                    "instrument_sha256": observation.get("instrument_sha256")}
    associations = []
    for test in unit.get("tests", []):
        matched = [p for p in points.values() if p["instance_id"] == instance_id and p.get("path") == test["path"]
                   and p.get("association") == test.get("association")]
        linked = [o for o in observed if o["declaration_id"] in {p["declaration_id"] for p in matched}]
        static_status = "todo" if "todo" in test.get("modifiers", []) else "skipped" if test.get("skipped") is True or "skip" in test.get("modifiers", []) else "not_discovered"
        associations.append({"test": _association(test), "status": "observed" if linked else static_status if complete and matched else "unavailable",
                             "cases": linked})
    if not plan.get("targets", {}).get(instance_id):
        complete = False
    known = complete or bool(observed)
    return {"valid_count": len(credited) if known else None,
            "counting_complete": complete, "count_kind": "exact" if complete else "lower_bound" if known else "unknown",
             "original_entry_observed": bool(original_entry) if known else None, "associations": associations}, list(credited.values())


def _instrument_failure(report):
    """Reconhece a origem confiável do erro, inclusive relatórios anteriores ao marcador."""
    text = json.dumps(report.get("suites", []), ensure_ascii=False)
    return ("ROBO_INSTRUMENT_FINALIZATION_FAILURE" in text
            or "Caso não finalizado pelo runner:" in text and "validation-runner.mjs" in text)


def execution_prerequisites(criteria):
    """Herda apenas os pré-requisitos necessários, sem herdar a decisão global."""
    keys = ("reproducible_environment", "unit_suite_scope", "dependency_installation", "required_preparation")
    evidence = {key: deepcopy(criteria[key]) for key in keys}
    return criterion(aggregate(evidence), "EXECUTION_PREREQUISITES", "Pré-requisitos reproduzíveis da execução original.", evidence)


def _verified_local_emitters(command, plan, instrument_sha256):
    """Revalida streams locais; censo incompleto de outros workers não fecha este stream."""
    from .validation_evidence import validate_events
    evidence = command.get("evidence", {})
    emitters = evidence.get("emitters", {})
    events = []
    for state in emitters.values():
        events.extend(event for event in (state.get("started"), state.get("finished")) if event)
    for case in evidence.get("cases", {}).values():
        events.extend(event for event in (case.get("started"), case.get("finished")) if event)
    for call in evidence.get("calls", []):
        events.extend(event for event in (call.get("direct"), call.get("entered")) if event)
    if not events:
        return set()
    first = events[0]
    if first.get("instrument_id") != instrument_sha256:
        return set()
    report = command.get("report") or {}
    if "events" in report and sorted(report["events"], key=lambda event: (event["emitter_id"], event["sequence"])) != sorted(
            events, key=lambda event: (event["emitter_id"], event["sequence"])):
        return set()
    if "expected" in report and any(report["expected"].get(key) != first[key]
                                    for key in ("session_id", "repository_id", "commit_sha", "instrument_id")):
        return set()
    expected = {key: first[key] for key in ("session_id", "repository_id", "commit_sha", "instrument_id")}
    declarations = dict(plan.get("declarations", {}))
    for worker in (command.get("report") or {}).get("workers", []):
        for key, declaration in worker.get("declarations", {}).items():
            if key in declarations and declarations[key].get("associated") and declaration != declarations[key]:
                return set()
            declarations.setdefault(key, {**declaration, "associated": False})
    expected.update(emitters={key: state["started"]["role"] for key, state in emitters.items()},
                    census_complete=False, declarations=declarations, points=plan.get("points", {}))
    try:
        checked = validate_events(sorted(events, key=lambda event: (event["emitter_id"], event["sequence"])), expected)
    except (ValueError, KeyError, TypeError):
        return set()
    if checked["emitters"] != emitters or checked["cases"] != evidence.get("cases") or checked["calls"] != sorted(
            evidence.get("calls", []), key=lambda call: (call["direct"]["emitter_id"], call["direct"]["sequence"])):
        return set()
    safe = set()
    for key, state in checked["emitters"].items():
        finish = state.get("finished") or {}
        sequences = sorted(event["sequence"] for event in events if event["emitter_id"] == key)
        if (finish.get("complete") is True and not finish.get("limitations")
                and sequences == list(range(1, len(sequences) + 1))):
            safe.add(key)
    return safe


def _count_instance(unit, observation):
    """Crédita somente testemunhas locais fechadas e mantém exatidão global separada."""
    safe = deepcopy(observation)
    instance_id = unit["instance_id"]
    limited = any(item.get("instance_id") in (None, instance_id) for item in safe.get("plan", {}).get("limitations", []))
    seen, collisions = set(), set()
    for command in safe.get("commands", []):
        for case_id in command.get("evidence", {}).get("cases", {}):
            if case_id in seen:
                collisions.add(case_id)
            seen.add(case_id)
    for command in safe.get("commands", []):
        workers = _verified_local_emitters(command, safe.get("plan", {}), safe.get("instrument_sha256"))
        evidence = command.get("evidence", {})
        evidence["cases"] = {key: case for key, case in evidence.get("cases", {}).items()
                             if not limited and key not in collisions and case.get("emitter_id") in workers}
        evidence["calls"] = [call for call in evidence.get("calls", []) if call["direct"]["case_id"] in evidence["cases"]]
    counts, cases = _count_instance_v1(unit, safe)
    linked = [case for case in cases if case["original_tests"] and case.get("target")]
    if len(linked) != len(cases):
        cases = linked
        counts.update(valid_count=len(cases), counting_complete=False, count_kind="lower_bound")
    if limited or collisions or any(len(a.get("evidence", {}).get("cases", {})) != len(b.get("evidence", {}).get("cases", {}))
                                    for a, b in zip(safe.get("commands", []), observation.get("commands", []))):
        counts.update(counting_complete=False, count_kind="lower_bound" if counts["valid_count"] is not None else "unknown")
    counts["original_entry_observed"] = bool(cases) if counts["valid_count"] is not None else None
    return counts, cases


def associated_tests_execution(unit, observation):
    """Exige passagem terminal dos módulos associados, incluindo hooks e todos os casos."""
    pending = criterion(code="LOCAL_EXECUTION_UNPROVEN", message="Fechamento e passagem do conjunto associado não comprovados.")
    plan = observation.get("plan", {})
    instance_id = unit["instance_id"]
    if any(item.get("instance_id") in (None, instance_id) for item in plan.get("limitations", [])):
        return pending
    paths = {test["path"] for test in unit.get("tests", [])}
    paths.update(point["path"] for point in plan.get("points", {}).values() if point["instance_id"] == instance_id)
    declaration_ids = {point["declaration_id"] for point in plan.get("points", {}).values() if point["instance_id"] == instance_id}
    covered, contexts = set(), []
    all_ids = [key for command in observation.get("commands", []) for key in command.get("evidence", {}).get("cases", {})]
    if len(all_ids) != len(set(all_ids)):
        return pending
    for index, command in enumerate(observation.get("commands", [])):
        report = command.get("report") or {}
        workers = _verified_local_emitters(command, plan, observation.get("instrument_sha256"))
        evidence = command.get("evidence", {})
        if not report or "evidence" not in command or _instrument_failure(report):
            return pending
        for suite in report.get("suites", []):
            if not isinstance(suite.get("planned_modules"), list):
                return pending
            selected = [item for item in suite.get("planned_modules") or [] if item["file"] in paths]
            if not selected:
                continue
            # Erros sem localização não são presumidos alheios à instância.
            if suite.get("errors") or suite.get("terminal") is not True or suite.get("reason") == "interrupted":
                return pending
            for item in selected:
                modules = [module for module in suite.get("modules", []) if
                           (module["file"], module["project_id"]) == (item["file"], item["project_id"])]
                if len(modules) != 1:
                    return pending
                module = modules[0]
                if module.get("worker_id") not in workers:
                    return pending
                hashes = {entry["path"]: entry["sha256"] for entry in plan.get("plans", [])}
                required = {item["file"], plan.get("targets", {}).get(instance_id, {}).get("path")}
                if any(not any(transform.get("suite_id") == suite["suite_id"] and transform.get("path") == path
                               and transform.get("sha256") == hashes.get(path) and path in hashes
                               for transform in report.get("transforms", [])) for path in required):
                    return pending
                results = [result for result in suite.get("module_results", []) if result.get("id") == module.get("id")]
                if any(result.get("errors") for result in results):
                    return criterion("rejected", "ASSOCIATED_MODULE_FAILURE", "Falha de módulo ou hook associado comprovada.", {"command_index": index, "module": module, "results": results})
                tasks = [task for task in suite.get("tasks", []) if
                         (task["file"], task["project_id"]) == (item["file"], item["project_id"])]
                local = [case for case in evidence.get("cases", {}).values() if
                         (case["started"]["case"]["file"], case["started"]["case"]["project_id"]) == (item["file"], item["project_id"])]
                for case in local:
                    if case["started"]["case"]["declaration_id"] not in declaration_ids:
                        continue
                    finish = case.get("finished") or {}
                    if finish.get("status") == "failed" or any(attempt["status"] == "failed" and not attempt["expected_failure"] for attempt in finish.get("attempts", [])):
                        return criterion("rejected", "ASSOCIATED_CASE_FAILURE", "Falha inesperada de caso associado, inclusive retry.", {"command_index": index, "case_id": case["started"]["case_id"]})
                if module.get("worker_id") not in workers or not tasks or len(tasks) != len(local):
                    return pending
                for task in tasks:
                    matched = [case for case in local if case["started"]["case"]["runtime_id"] == task["id"]
                               and case["emitter_id"] == module["worker_id"]]
                    if len(matched) != 1 or not matched[0].get("finished"):
                        return pending
                    status = matched[0]["finished"]["status"]
                    if task.get("result", {}).get("state") not in {status, "skipped" if status == "todo" else status}:
                        return pending
                if not results:
                    return pending
                if results:
                    if len(results) != 1 or results[0].get("state") not in {"passed", "failed"}:
                        return pending
                    if results[0]["state"] == "failed" and not any(task.get("result", {}).get("state") == "failed" for task in tasks):
                        return pending
                covered.add(item["file"])
                contexts.append({"command_index": index, "suite_id": suite["suite_id"], "project_id": item["project_id"],
                                 "file": item["file"], "worker_id": module["worker_id"]})
    if paths and covered == paths:
        return criterion("approved", "ASSOCIATED_DECLARATIONS_AND_HOOKS_PASSED", "Declarações associadas e hooks dos módulos concluídos sem falhas inesperadas.",
                         {"declaration_ids": sorted(declaration_ids), "contexts": contexts})
    return pending


def provisional_resources(value):
    """Preserva exclusivamente a leitura histórica de v2 com gate global provisório."""
    return criterion(value["status"], "GLOBAL_RESOURCE_GATE_PROVISIONAL", "Gate provisório: auditoria global de recursos, ainda sem escopo local.", {"global_criterion": deepcopy(value)})


def _metric(hits):
    """Explicita denominador zero como não aplicável, nunca percentual inventado."""
    total = len(hits)
    covered = sum(hit > 0 for hit in hits)
    return {"status": "available" if total else "not_applicable", "covered": covered, "total": total,
            "percentage": covered * 100 / total if total else None}


def _position(source, offset):
    """Converte offset UTF-16 do plano AST em coordenadas originais do Istanbul."""
    prefix = source.encode("utf-16-le")[:offset * 2].decode("utf-16-le")
    return (prefix.count("\n") + 1, len(prefix.rsplit("\n", 1)[-1].encode("utf-16-le")) // 2)


def _location(value):
    """Recusa coordenadas de provider inválidas sem derrubar evidência obrigatória."""
    if (type(value) is not dict or type(value.get("line")) is not int or value["line"] < 1
            or type(value.get("column")) is not int or value["column"] < 0):
        raise ValueError("Posição de cobertura inválida.")
    return value["line"], value["column"]


def _provider_function(unit, observation, verified, suite):
    """Extrai Istanbul somente com fonte original, source map e intervalo exatos."""
    target = observation.get("plan", {}).get("targets", {}).get(unit["instance_id"])
    if not target:
        return None
    path = unit["function"]["path"]
    sources = {f["path"]: f["content"] for f in (verified or {}).get("context", {}).get("files", [])}
    if path not in sources:
        return None
    source = sources[path]
    start, end = _position(source, target["start"]), _position(source, target["end"])
    data = suite.get("coverage")
    if type(data) is not dict:
        return None
    matches = [v for key, v in data.items() if key in {path, "/workspace/" + path}]
    if len(matches) != 1:
        return None
    entry = matches[0]
    mapping = entry.get("inputSourceMap") or {}
    if not any(p.removeprefix("/workspace/") == path and content == source
               for p, content in zip(mapping.get("sources", []), mapping.get("sourcesContent", []))):
        return None
    functions = [f for f in entry.get("fnMap", {}).values()
                 if start <= _location(f["decl"]["start"]) <= _location(f["loc"]["start"])
                 and _location(f["loc"]["end"]) == end]
    if len(functions) != 1:
        return None
    lines, branches = {}, []
    for key, location in entry["statementMap"].items():
        first, last = _location(location["start"]), _location(location["end"])
        if start <= first and last <= end:
            hits = entry["s"][key]
            if type(hits) is not int or hits < 0:
                return None
            lines[first[0]] = max(lines.get(first[0], 0), hits)
    for key, branch in entry["branchMap"].items():
        location = branch["loc"]
        if start <= _location(location["start"]) and _location(location["end"]) <= end:
            hits = entry["b"][key]
            if len(hits) != len(branch["locations"]) or any(type(h) is not int or h < 0 for h in hits):
                return None
            branches.extend(hits)
    return {"lines": lines, "branches": branches, "suite_id": suite["suite_id"], "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "source_map_sha256": _hash(mapping), "provider_data_sha256": _hash(entry), "tools": suite.get("tools", {})}


def _coverage(unit, observation, verified):
    """Mantém escopos distintos; só mapeia provider global comprovado ao código original."""
    unavailable = {"status": "unavailable", "lines": None, "branches": None,
                   "reason": "Não há atribuição confiável ao intervalo original desta função."}
    result = {scope: {**unavailable, "scope": scope} for scope in ("associated_tests", "full_unit_suite")}
    if unit is None:
        return result
    if not observation.get("commands") or any("evidence" not in command for command in observation["commands"]):
        for metric in result.values():
            metric["reason"] = "Relatório sem identidade/protocolo verificados; material bruto serve somente a diagnóstico."
        return result
    suites = [suite for command in observation.get("commands", []) for suite in (command.get("report") or {}).get("suites", [])]
    measurements = []
    for suite in suites:
        try:
            measurement = _provider_function(unit, observation, verified, suite)
        except (KeyError, ValueError, TypeError, AttributeError, UnicodeError):
            measurement = None
        if measurement is not None:
            measurements.append(measurement)
    if measurements and len(suites) == len(measurements):
        # Numeradores são unidos por linha, não somados como se suítes fossem
        # linhas distintas. Branches de múltiplos providers não são fundidas sem ID.
        lines = {}
        for measurement in measurements:
            for line, hits in measurement["lines"].items():
                lines[line] = max(lines.get(line, 0), hits)
        result["full_unit_suite"] = {"status": "available", "scope": "full_unit_suite", "lines": _metric(list(lines.values())),
            "branches": _metric(measurements[0]["branches"]) if len(measurements) == 1 else None,
            "reason": None if len(measurements) == 1 else "Ramificações entre providers distintos sem identidade conjunta confiável.",
            "measurements": [{k: v for k, v in m.items() if k not in {"lines", "branches"}} for m in measurements]}
    return result


def operational_issues(repository):
    """Identifica impedimentos independentemente da prevalência de rejeição científica."""
    issues = []
    for phase, result in repository.get("execution", {}).items():
        for index, command in enumerate(result.get("commands", [])):
            flags = {key: command[key] for key in OPERATIONAL_FLAGS if command.get(key)}
            if flags:
                issues.append({"phase": phase, "command_index": index, "flags": flags})
    for index, event in enumerate(repository.get("infrastructure_events", [])):
        flags = {key: event[key] for key in OPERATIONAL_FLAGS if event.get(key)}
        if flags:
            issues.append({"phase": "infrastructure", "event_index": index, "flags": flags})
    observation = repository.get("unit_suite", {})
    for index, command in enumerate(observation.get("commands", [])):
        if repository.get("schema_version", 2) >= 2 and _instrument_failure(command.get("report") or {}):
            issues.append({"phase": "unit_suite", "command_index": index, "code": "INSTRUMENT_FINALIZATION_FAILURE"})
    if observation.get("commands") and not _observation_complete(observation):
        issues.append({"phase": "unit_suite", "code": "INCOMPLETE_REQUIRED_OBSERVATION",
                       "commands": [{"command_index": index, "limitations": command.get("limitations", []),
                                     "protocol_verified": "evidence" in command,
                                     "evidence_complete": command.get("evidence", {}).get("complete"),
                                     "census_complete": command.get("census", {}).get("complete")}
                                    for index, command in enumerate(observation["commands"])]})
    return issues


def _environment_criterion(recipe, resolution, verified, environment, observation):
    """Exige ferramentas reais, inventário instalado e locks efetivos concordantes."""
    pending = criterion(code="ENVIRONMENT_NOT_VERIFIED", message="Ambiente efetivo ou suas dependências não puderam ser conferidos.")
    if not recipe or not verified or resolution.get("resolution_status") != "resolved":
        return pending
    if (resolution.get("recipe_sha256") != _hash(recipe) or environment.get("image") != recipe["image"]
            or environment.get("platform") != recipe["platform"]):
        return pending
    tools = environment.get("tools", {})
    if any(tools.get(name) != version for name, version in recipe["tools"].items() if name != "vitest"):
        return pending
    dependencies = environment.get("dependencies", {}).get("packages")
    if not isinstance(dependencies, list) or not any(p.get("name") == "vitest" and p.get("version") == recipe["tools"]["vitest"] for p in dependencies):
        return pending
    locks = environment.get("lockfiles", [])
    if len(locks) != len(recipe["lockfiles"]) or any(not any(
            actual.get("path") == lock["path"] and actual.get("effective_sha256") == lock["sha256"]
            and actual.get("matches_original") is True for actual in locks) for lock in recipe["lockfiles"]):
        return pending
    return criterion("approved", "ENVIRONMENT_VERIFIED", "Snapshot, ferramentas e dependências efetivas conferidos.", {"snapshot": verified["snapshot"]})


def evaluate_repository(group, resolution, execution, *, policy_version=1):
    """Consolida provas; versão 1 mantém API histórica e versão 2 avalia o par."""
    if type(policy_version) is not int or policy_version not in {1, 2}:
        raise ValueError("Versão de política desconhecida.")
    pair = policy_version == 2
    schema = 3 if pair else 2
    repository = group["repository"]
    recipe = resolution.get("recipe")
    verified = execution.get("verified")
    phases = execution.get("phases", {})
    observation = execution.get("observation", {})
    criteria = {key: criterion() for key in (PAIR_REPOSITORY_CRITERIA if pair else REPOSITORY_CRITERIA)}
    environment = execution.get("environment", {})
    criteria["reproducible_environment"] = _environment_criterion(recipe, resolution, verified, environment, observation)
    if recipe:
        scope = recipe["unit_scope"]
        criteria["unit_suite_scope"] = criterion(scope["status"], scope.get("code") or "ORIGINAL_UNIT_SCOPE", "Escopo unitário conforme evidências da receita.", {"scope": scope})
        if not recipe["phases"]["preparation"]:
            criteria["required_preparation"] = criterion("approved", "PREPARATION_NOT_REQUIRED", "Receita demonstra que não há preparação necessária.", {"recipe_sha256": resolution["recipe_sha256"]}, required=False)
    for phase, key in (("installation", "dependency_installation"), ("preparation", "required_preparation")):
        if phases.get(phase, {}).get("required") is True:
            criteria[key] = _phase_criterion(phases[phase])
            criteria[key]["required"] = True
    criteria["complete_unit_suite"] = _suite_criterion(observation, phases.get("unit_suite", {}))
    if not pair:
        criteria["no_external_services"] = evaluate_resources(recipe, verified, execution.get("resource_evidence"), observation, environment)
    decision = aggregate(criteria)
    records = []
    for compact in group["instances"]:
        instance_id = compact["instance_id"]
        unit = (verified or {}).get("instances", {}).get(instance_id)
        individual = {key: criterion() for key in (PAIR_INSTANCE_CRITERIA if pair else LOCAL_INSTANCE_CRITERIA)}
        individual["execution_prerequisites"] = execution_prerequisites(criteria)
        if not pair:
            from .validation_resources_local import evaluate_instance_resources
            individual["no_external_services"] = evaluate_instance_resources(
                unit or {"instance_id": instance_id, "tests": []}, recipe, resource_universe(verified), execution.get("resource_evidence"),
                execution.get("resource_local_method"), execution.get("instance_resource_evidence", {}).get(instance_id), observation, environment)
        individual["associated_tests_execution"] = associated_tests_execution(unit or {"instance_id": instance_id, "tests": []}, observation)
        counts = {"valid_count": None, "counting_complete": False, "count_kind": "unknown", "original_entry_observed": None, "associations": []}
        cases = []
        if unit:
            counts, cases = _count_instance(unit, observation)
            individual["associated_tests_execution"] = associated_tests_execution(unit, observation)
        if unit or pair:
            count = counts["valid_count"]
            minimum = minimum_cases_status(count, counts["counting_complete"]) if count is not None else "not_evaluated"
            individual["minimum_valid_cases"] = criterion(minimum, "MINIMUM_FOUR_CASES", "Limiar de quatro casos positivos distintos.", {"valid_count": count, "counting_complete": counts["counting_complete"]})
            identity_status = "approved" if cases else "rejected" if counts["counting_complete"] else "not_evaluated"
            individual["original_target_execution"] = criterion(identity_status, "ORIGINAL_TARGET_WITNESSES", "Entrada original vinculada à chamada direta e ao caso; lacunas permanecem explícitas.", {"case_ids": [c["case_id"] for c in cases]})
        records.append({"schema_version": schema, "record_type": "instance", "stage": "validation",
            "repository_id": repository["repository_id"], "instance_id": instance_id,
            "repository_ref": repository["repository_id"], "decision": aggregate(individual), "criteria": individual,
            "test_cases": counts, "case_evidence": cases, "coverage": _coverage(unit, observation, verified)})
    runtime_cases = [case for command in observation.get("commands", []) for case in command.get("evidence", {}).get("cases", {}).values()]
    first_error = next(({"criterion": key, **value} for key, value in criteria.items() if value["status"] == "rejected"), None)
    summary = {"case_count": len(runtime_cases), "attempt_count": sum(len((case.get("finished") or {}).get("attempts", [])) for case in runtime_cases),
               "case_statuses": {state: sum((case.get("finished") or {}).get("status") == state for case in runtime_cases)
                                 for state in ("passed", "failed", "skipped", "todo")},
               "observation_complete": _observation_complete(observation), "first_error": first_error}
    record = {"schema_version": schema, "record_type": "repository", "stage": "validation",
        **{k: repository[k] for k in ("repository_id", "repository_url", "commit_sha")},
        "decision": decision, "criteria": criteria, "snapshot": (verified or {}).get("snapshot"),
        "recipe": deepcopy(resolution), "environment": environment, "lockfiles": environment.get("lockfiles", []),
        "execution": phases, "unit_suite": observation, "unit_suite_summary": summary, "resource_evidence": execution.get("resource_evidence"),
        "resource_universe": resource_universe(verified),
        "resource_local_method": execution.get("resource_local_method"), "instance_resource_evidence": execution.get("instance_resource_evidence", {}),
        "instance_decisions": {s: sum(r["decision"] == s for r in records) for s in ("approved", "rejected", "not_evaluated")}}
    if pair:
        record["resource_diagnostics"] = deepcopy(execution.get("resource_diagnostics", {
            "mode": "disabled", "message": "Auditoria opcional não executada; nenhuma alegação de ausência de serviços."}))
    return record, records
