"""Valida o protocolo v1 e expõe a avaliação científica sem alterar seus eventos.

expected contém session_id, repository_id, commit_sha, instrument_id, emitters
(mapa emitter_id -> worker/coordinator), census_complete, declarations e points.
declarations indexa ID -> {file, start, end, modifiers}; points indexa ID ->
{instance_id, declaration_id}. Posições são offsets UTF-16 da fonte verificada.
O censo vem do coordenador confiável, nunca é inferido só dos emissores recebidos.
"""

import hashlib
import json
import math
import re

from .extraction_input import strict_float, strict_object


ENVELOPE = {"protocol_version", "session_id", "repository_id", "commit_sha",
            "instrument_id", "emitter_id", "sequence", "event"}
FIELDS = {
    "run_started": {"role", "tools"},
    "case_started": {"case_id", "case"},
    "direct_call": {"case_id", "attempt", "call_id", "point_id", "instance_id"},
    "target_entered": {"case_id", "attempt", "call_id", "instance_id"},
    "case_finished": {"case_id", "status", "attempts"},
    "run_finished": {"complete", "emitter_ids", "limitations"},
}
CASE_FIELDS = {"project_id", "file", "declaration_id", "occurrence", "worker_id",
               "runtime_id", "title", "modifiers"}
STATUSES = {"passed", "failed", "skipped", "todo"}


def _invalid_number(value):
    """Recusa NaN e infinitos antes que o decoder perca a representação original."""
    raise ValueError("Número não finito no trace.")


def decode_events(raw):
    """Decodifica JSONL UTF-8 integral sem aceitar chaves repetidas ou linhas vazias."""
    if type(raw) is not bytes:
        raise ValueError("Trace deve ser fornecido como bytes originais.")
    try:
        return [json.loads(line, object_pairs_hook=strict_object, parse_float=strict_float,
                           parse_constant=_invalid_number) for line in raw.decode("utf-8").splitlines()]
    except (UnicodeError, ValueError) as error:
        raise ValueError("Trace não contém JSONL UTF-8 estrito.") from error


def _expected(value):
    """Confere catálogo confiável antes de usá-lo como autoridade de associação."""
    _json_value(value)
    if (type(value) is not dict or not {"session_id", "repository_id", "commit_sha", "instrument_id",
                                       "emitters", "census_complete", "declarations", "points"} <= set(value)
            or type(value["repository_id"]) is not int or value["repository_id"] <= 0
            or not all(type(value[key]) is str and value[key] for key in ("session_id", "commit_sha", "instrument_id"))
            or not re.fullmatch("[0-9a-f]{40}", value["commit_sha"])
            or type(value["census_complete"]) is not bool):
        raise ValueError("Identidade esperada inválida.")
    for key in ("emitters", "declarations", "points"):
        if type(value[key]) is not dict or not all(value[key]):
            raise ValueError("Catálogo esperado inválido: " + key)
    if any(role not in {"coordinator", "worker"} for role in value["emitters"].values()):
        raise ValueError("Papel de emissor desconhecido.")
    for declaration in value["declarations"].values():
        if (type(declaration) is not dict or not {"file", "start", "end", "modifiers"} <= set(declaration)
                or type(declaration["file"]) is not str or not declaration["file"]
                or not _integer(declaration["start"]) or not _integer(declaration["end"])
                or declaration["start"] >= declaration["end"] or not _strings(declaration["modifiers"])):
            raise ValueError("Declaração esperada inválida.")
    for point in value["points"].values():
        if (type(point) is not dict or not {"instance_id", "declaration_id"} <= set(point)
                or type(point["instance_id"]) is not str or not point["instance_id"]
                or type(point["declaration_id"]) is not str or point["declaration_id"] not in value["declarations"]):
            raise ValueError("Ponto direto esperado inválido.")


def _json_value(value):
    """Recusa valores fora do JSON estrito, inclusive não finitos aninhados."""
    if value is None or type(value) in {str, bool, int}:
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _json_value(item)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _json_value(item)
        return
    raise ValueError("Valor fora do protocolo JSON estrito.")


def _integer(value):
    """Exige ordinal seguro compartilhável com JavaScript, sem coerção de bool."""
    return type(value) is int and 0 <= value <= 9007199254740991


def _strings(value):
    """Confere lista de identificadores textuais sem duplicação."""
    return (type(value) is list and all(type(item) is str for item in value)
            and len(value) == len(set(value)))


def case_identity(value):
    """Reproduz a chave do runtime, preservando Unicode e títulos duplicados."""
    parts = [value[key] for key in ("project_id", "file", "declaration_id", "occurrence",
                                   "worker_id", "runtime_id")]
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def validate_events(events, expected):
    """Recusa trace impossível e retorna completude sem confundir ausência com zero.

    Retorna {protocol_version, complete, limitations, emitters, cases, calls}.
    cases/calls retêm eventos integrais para avaliação posterior na tarefa 4.
    Trace estruturalmente incompleto é aceito apenas com complete=False; corrupção
    de identidade, sequência repetida/desordenada ou vínculos é ValueError.
    """
    _expected(expected)
    emitters, cases, calls = {}, {}, {}
    limitations = []
    for event in events:
        _json_value(event)
        if type(event) is not dict or type(event.get("event")) is not str or event["event"] not in FIELDS:
            raise ValueError("Evento desconhecido no protocolo de validação.")
        kind = event["event"]
        if set(event) != ENVELOPE | FIELDS[kind] or type(event["protocol_version"]) is not int or event["protocol_version"] != 1:
            raise ValueError("Campos ou versão do protocolo inválidos.")
        for key in ("session_id", "repository_id", "commit_sha", "instrument_id"):
            if type(event[key]) is not type(expected[key]) or event[key] != expected[key]:
                raise ValueError("Identidade divergente no trace: " + key)
        emitter_id = event["emitter_id"]
        if type(emitter_id) is not str or emitter_id not in expected["emitters"]:
            raise ValueError("Emissor não registrado pelo coordenador.")
        state = emitters.setdefault(emitter_id, {"sequence": 0, "started": None, "finished": None})
        if not _integer(event["sequence"]) or event["sequence"] <= state["sequence"] or state["finished"]:
            raise ValueError("Sequência repetida, perdida ou posterior ao fechamento.")
        if event["sequence"] != state["sequence"] + 1:
            limitations.append("Lacuna na sequência do emissor: " + emitter_id)
        state["sequence"] = event["sequence"]
        if kind == "run_started":
            if state["started"] or event["sequence"] != 1 or event["role"] != expected["emitters"][emitter_id] or type(event["tools"]) is not dict:
                raise ValueError("Abertura de emissor inválida.")
            state["started"] = event
            continue
        if not state["started"]:
            raise ValueError("Evento anterior à abertura de emissor.")
        if kind == "run_finished":
            if type(event["complete"]) is not bool or not _strings(event["emitter_ids"]) or not _strings(event["limitations"]):
                raise ValueError("Fechamento de emissor inválido.")
            if any(value["emitter_id"] == emitter_id and value["finished"] is None for value in cases.values()):
                raise ValueError("Emissor encerrou com casos abertos.")
            if state["started"]["role"] == "coordinator":
                if set(event["emitter_ids"]) != set(expected["emitters"]):
                    raise ValueError("Censo final diverge dos processos/threads esperados.")
            elif event["emitter_ids"]:
                raise ValueError("Worker não pode declarar censo global.")
            state["finished"] = event
            limitations.extend(event["limitations"])
            if not event["complete"]:
                limitations.append("Emissor declarou observação incompleta: " + emitter_id)
            continue
        if state["started"]["role"] != "worker":
            raise ValueError("Coordenador não pode emitir evidência de corpo de teste.")
        case_id = event["case_id"]
        if type(case_id) is not str:
            raise ValueError("ID do caso inválido.")
        if kind == "case_started":
            value = event["case"]
            if type(value) is not dict or set(value) != CASE_FIELDS or case_id in cases:
                raise ValueError("Caso duplicado ou formato de identidade inválido.")
            if (not all(type(value[key]) is str and value[key] for key in CASE_FIELDS - {"occurrence", "modifiers", "title"})
                    or type(value["title"]) is not str or not _integer(value["occurrence"])
                    or not _strings(value["modifiers"]) or value["worker_id"] != emitter_id
                    or case_identity(value) != case_id):
                raise ValueError("Identidade inequívoca do caso não confere.")
            declaration = expected["declarations"].get(value["declaration_id"])
            if not declaration or declaration["file"] != value["file"] or declaration["modifiers"] != value["modifiers"]:
                raise ValueError("Caso não corresponde à declaração estática verificada.")
            cases[case_id] = {"started": event, "finished": None, "emitter_id": emitter_id}
            continue
        case = cases.get(case_id)
        if not case or case["emitter_id"] != emitter_id or case["finished"]:
            raise ValueError("Caso inexistente, encerrado ou pertencente a outro worker.")
        if kind == "case_finished":
            if type(event["status"]) is not str or event["status"] not in STATUSES or type(event["attempts"]) is not list:
                raise ValueError("Resultado de caso inválido.")
            attempts = event["attempts"]
            coordinates = set()
            for index, attempt in enumerate(attempts):
                if (type(attempt) is not dict or set(attempt) != {"index", "retry", "repeat", "status", "expected_failure"}
                        or not _integer(attempt["index"]) or attempt["index"] != index
                        or not _integer(attempt["retry"]) or not _integer(attempt["repeat"])
                        or type(attempt["status"]) is not str or attempt["status"] not in STATUSES
                        or type(attempt["expected_failure"]) is not bool
                        or attempt["expected_failure"] != ("fails" in case["started"]["case"]["modifiers"])
                        or (attempt["retry"], attempt["repeat"]) in coordinates):
                    raise ValueError("Tentativa duplicada ou inválida.")
                coordinates.add((attempt["retry"], attempt["repeat"]))
                previous = attempts[index - 1] if index else None
                if ((not previous and (attempt["retry"], attempt["repeat"]) != (0, 0))
                        or previous and (attempt["repeat"], attempt["retry"]) not in {
                            (previous["repeat"], previous["retry"] + 1), (previous["repeat"] + 1, 0)}):
                    raise ValueError("Ordem impossível de retry/repeat.")
            if event["status"] == "passed" and not attempts:
                raise ValueError("Caso executado sem tentativas.")
            if (attempts and event["status"] == "passed" and not attempts[-1]["expected_failure"]
                    and attempts[-1]["status"] != "passed"):
                raise ValueError("Caso aprovado sem tentativa final aprovada.")
            for call in calls.values():
                if call["direct"]["case_id"] == case_id and call["direct"]["attempt"] >= len(attempts):
                    raise ValueError("Chamada sem tentativa correspondente.")
            case["finished"] = event
            continue
        if not _integer(event["attempt"]) or type(event["call_id"]) is not str or type(event["instance_id"]) is not str:
            raise ValueError("Token de chamada inválido.")
        call_key = (emitter_id, event["call_id"])
        if kind == "direct_call":
            if type(event["point_id"]) is not str:
                raise ValueError("ID do ponto direto inválido.")
            point = expected["points"].get(event["point_id"])
            if call_key in calls or not point or point["instance_id"] != event["instance_id"] or point["declaration_id"] != case["started"]["case"]["declaration_id"]:
                raise ValueError("Ponto direto divergente ou token duplicado.")
            calls[call_key] = {"direct": event, "entered": None}
        else:
            call = calls.get(call_key)
            if not call or call["entered"] or any(call["direct"][key] != event[key] for key in ("case_id", "attempt", "instance_id")):
                raise ValueError("Entrada original sem chamada direta correspondente.")
            call["entered"] = event
    if expected.get("census_complete") is not True:
        limitations.append("Censo de processos/workers não comprovado.")
    if not expected["emitters"] or "coordinator" not in expected["emitters"].values():
        limitations.append("Coordenador confiável ausente.")
    for emitter_id in expected["emitters"]:
        if not emitters.get(emitter_id, {}).get("finished"):
            limitations.append("Emissor ausente ou sem fechamento: " + emitter_id)
    if any(value["finished"] is None for value in cases.values()):
        limitations.append("Casos sem resultado final.")
    return {"protocol_version": 1, "complete": not limitations, "limitations": limitations,
            "emitters": emitters, "cases": cases, "calls": list(calls.values())}


def validate_census(report, expected_cwds=None, plan=None):
    """Confere igualdade de identidades reporter/worker, inclusive omissões e skips.

    O relatório do reporter é observação independente dos probes; jamais fornece
    autoridade para points/targets. O chamador continua usando seu plano confiável.
    """
    limitations, expected, observed = [], {}, {}
    suites = report.get("suites", [])
    if not suites:
        limitations.append("Nenhum processo Vitest censado.")
    if not report.get("processes"):
        limitations.append("Registro independente de processos Node ausente.")
    if expected_cwds is not None and sorted(expected_cwds) != sorted(suite["cwd"] for suite in suites):
        limitations.append("Processos Vitest não correspondem às folhas originais da receita.")
    for suite in suites:
        if suite.get("terminal") is not True:
            limitations.append("Processo Vitest sem relatório terminal.")
        planned = suite.get("planned_modules")
        if type(planned) is not list or suite.get("reason") == "interrupted":
            limitations.append("Seleção original de módulos não censada ou passagem interrompida.")
        elif sorted((item["project_id"], item["file"]) for item in planned) != sorted(
                (item["project_id"], item["file"]) for item in suite["modules"]):
            limitations.append("Módulos selecionados e coletados divergem.")
        if plan is not None:
            selected = {item["file"] for item in planned or []}
            required = set()
            for point in plan["points"].values():
                if point["path"] in selected:
                    required.update((point["path"], plan["targets"][point["instance_id"]]["path"]))
            hashes = {item["path"]: item["sha256"] for item in plan["plans"]}
            for path in required:
                if not any(item["suite_id"] == suite["suite_id"] and item["path"] == path
                           and item["sha256"] == hashes[path] for item in report.get("transforms", [])):
                    limitations.append("Fonte necessária sem transformação original comprovada: " + path)
        for module in suite["modules"]:
            if not module.get("worker_id"):
                limitations.append("Módulo coletado sem emissor conhecido.")
        for task in suite["tasks"]:
            modules = [module for module in suite["modules"]
                       if module["file"] == task["file"] and module["project_id"] == task["project_id"]]
            if len(modules) != 1 or not modules[0].get("worker_id"):
                limitations.append("Caso coletado sem módulo/worker inequívoco.")
                continue
            key = (modules[0]["worker_id"], task["project_id"], task["file"], task["id"])
            if key in expected:
                limitations.append("Caso duplicado no censo do framework.")
            expected[key] = task
    finishes = {event["case_id"]: event for event in report["events"] if event["event"] == "case_finished"}
    for event in report["events"]:
        if event["event"] != "case_started":
            continue
        value = event["case"]
        key = (event["emitter_id"], value["project_id"], value["file"], value["runtime_id"])
        if key in observed:
            limitations.append("Caso runtime duplicado entre os streams.")
        observed[key] = event
        task = expected.get(key)
        finish = finishes.get(event["case_id"])
        states = {"passed": {"passed"}, "failed": {"failed"}, "skipped": {"skipped", "todo"}, "pending": set()}
        if not task or not finish or finish["status"] not in states.get(task["result"]["state"], set()):
            limitations.append("Resultado/identidade diverge entre runner e reporter.")
    if set(expected) != set(observed):
        limitations.append("Universos de casos divergem entre reporter e workers.")
    for process in report.get("processes", []):
        if process.get("terminal_observed") is not True:
            limitations.append("Processo/thread sem término observado pelo coordenador.")
    for child in report.get("children", []):
        if child.get("terminal_observed") is not True or child.get("preload_inherited") is not True:
            limitations.append("Subprocesso sem fechamento ou propagação comprovada do instrumento.")
    for child in report.get("synchronous_children", []):
        if (child.get("preload_inherited") is not True or child.get("opaque_shell")
                or child.get("delegation_observable") is not True or child.get("node_launcher") is not True
                or child.get("process_observed") is not True):
            limitations.append("Descendência síncrona não observada completamente.")
    return {"complete": not limitations, "limitations": limitations,
            "expected_cases": len(expected), "observed_cases": len(observed)}


def evaluate_repository(group, recipe, execution, *, policy_version=1):
    """Avalia evidências T1–T3 e retorna (registro de origem, registros de instâncias).

    recipe é a resolução de resolve_recipe; execution contém verified, phases,
    observation, environment e resource_evidence opcionais. Não executa comandos.
    O módulo separado mantém o protocolo T3 independente das decisões científicas.
    """
    from .validation_evaluation import evaluate_repository as evaluate
    return evaluate(group, recipe, execution, policy_version=policy_version)
