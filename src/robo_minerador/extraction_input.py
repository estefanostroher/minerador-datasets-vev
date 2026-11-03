"""Valida integralmente a triagem antes de permitir rede ou escrita."""

import hashlib
import json
import re
from datetime import datetime
from math import isfinite
from pathlib import Path

from .discovery import RATE_HEADERS
from .triage import _invalid_constant
from .triage_checks import CRITERION_NAMES

STATES = frozenset({"approved", "rejected", "not_evaluated"})


def aggregate(statuses) -> str:
    """Consolida critérios: rejeição prevalece, aprovação exige unanimidade."""
    statuses = list(statuses)
    return ("rejected" if "rejected" in statuses else
            "approved" if all(value == "approved" for value in statuses) else "not_evaluated")


def strict_object(pairs) -> dict:
    """Rejeita chaves repetidas para evitar interpretações divergentes do protocolo."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Chave JSON duplicada.")
        result[key] = value
    return result


def strict_float(value: str) -> float:
    """Rejeita números que transbordariam para infinito na representação JSON local."""
    number = float(value)
    if not isfinite(number):
        raise ValueError("Número JSON fora do intervalo finito.")
    return number


def valid_error_context(value: dict) -> bool:
    """Aceita somente status HTTP e cabeçalhos numéricos já permitidos pela coleta."""
    status = value.get("http_status")
    if status is not None and (type(status) is not int or not 100 <= status <= 599):
        return False
    headers = value.get("rate_limit_headers", {})
    return isinstance(headers, dict) and all(
        key in RATE_HEADERS and isinstance(item, str) and item.isascii() and item.isdecimal()
        for key, item in headers.items()
    )


def _validate_diagnostic(row: dict) -> None:
    """Confere campos comuns e instante UTC dos diagnósticos da triagem."""
    if (row.get("stage") != "triage" or not isinstance(row.get("event"), str)
            or not row["event"] or not isinstance(row.get("message"), str)
            or not row["message"].strip() or not isinstance(row.get("observed_at"), str)):
        raise ValueError("Diagnóstico de triagem inválido.")
    try:
        instant = datetime.fromisoformat(row["observed_at"])
    except ValueError as error:
        raise ValueError("Instante de diagnóstico inválido.") from error
    if instant.utcoffset() is None or instant.utcoffset().total_seconds() != 0:
        raise ValueError("Instante do diagnóstico deve estar em UTC.")
    if not valid_error_context(row):
        raise ValueError("Metadados operacionais do diagnóstico inválidos.")


def validate_criteria(criteria, names) -> str:
    """Valida critérios obrigatórios e retorna sua decisão agregada coerente."""
    if not isinstance(criteria, dict) or set(criteria) != set(names):
        raise ValueError("Conjunto de critérios inválido.")
    for value in criteria.values():
        if (not isinstance(value, dict) or not isinstance(value.get("status"), str)
                or value["status"] not in STATES
                or not isinstance(value.get("reason"), str) or not value["reason"].strip()
                or not isinstance(value.get("evidence"), dict)):
            raise ValueError("Critério inválido.")
    return aggregate(value["status"] for value in criteria.values())


def load_triage(path: Path) -> tuple[list[dict], dict]:
    """Confere todos os registros e contagens e seleciona apenas SHAs aprovados.

    input_count inclui todas as decisões da triagem; eligible_count inclui somente
    aprovados. O hash identifica os bytes originais, sem normalizar espaços ou BOM.
    error_count conta diagnósticos repository_error, inclusive erros de fechamento.
    """
    content = path.read_bytes()
    try:
        lines = content.decode("utf-8").split("\n")
        if lines[-1] == "":
            lines.pop()
        rows = [json.loads(line, parse_constant=_invalid_constant, object_pairs_hook=strict_object,
                           parse_float=strict_float)
                for line in lines]
    except (UnicodeError, ValueError) as error:
        raise ValueError("A triagem deve ser JSONL UTF-8 estrito.") from error
    if len(rows) < 2 or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Triagem incompleta.")
    first, last = rows[0], rows[-1]
    if first.get("event") != "run_started" or last.get("event") != "run_finished":
        raise ValueError("Início ou fim da triagem ausente.")
    seen = set()
    candidates = []
    counts = dict.fromkeys(STATES, 0)
    errors = 0
    any_evaluated = False
    for index, row in enumerate(rows):
        if row.get("record_type") == "diagnostic":
            _validate_diagnostic(row)
            if ((row["event"] == "run_started" and index != 0)
                    or (row["event"] == "run_finished" and index != len(rows) - 1)):
                raise ValueError("Diagnóstico fora de posição.")
            if row["event"] not in {"run_started", "run_finished", "repository_error"}:
                raise ValueError("Evento de triagem desconhecido.")
            if row["event"] == "repository_error":
                identity = row.get("repository_id")
                if identity is not None and (type(identity) is not int or identity not in seen):
                    raise ValueError("Diagnóstico associado a repositório desconhecido.")
            errors += row["event"] == "repository_error"
            continue
        if row.get("record_type") != "repository" or index in {0, len(rows) - 1}:
            raise ValueError("Registro de triagem inválido.")
        identity = row.get("repository_id")
        url = row.get("repository_url")
        if type(identity) is not int or identity <= 0 or identity in seen:
            raise ValueError("ID inválido ou duplicado na triagem.")
        seen.add(identity)
        if (not isinstance(url, str)
                or not re.fullmatch(r"https://github\.com/[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", url)
                or url.rsplit("/", 1)[-1] in {".", ".."}):
            raise ValueError("URL canônica inválida na triagem.")
        decision = validate_criteria(row.get("criteria"), CRITERION_NAMES)
        if row.get("decision") != decision:
            raise ValueError("Decisão incompatível com os critérios.")
        sha = row.get("commit_sha")
        if ("commit_sha" not in row or (sha is None and decision == "approved")
                or (sha is not None and (not isinstance(sha, str)
                                        or not re.fullmatch(r"[a-fA-F0-9]{40}", sha)))):
            raise ValueError("SHA inválido na triagem.")
        if sha is None and row["criteria"]["access"]["status"] != "not_evaluated":
            raise ValueError("Acesso avaliado sem SHA imutável.")
        counts[decision] += 1
        any_evaluated |= any(c["status"] != "not_evaluated" for c in row["criteria"].values())
        if decision == "approved":
            candidates.append({**row, "repository": url.removeprefix("https://github.com/")})
    expected = {"repository_count": len(seen), **counts, "error_count": errors}
    if any(type(last.get(key)) is not int or last[key] != value for key, value in expected.items()):
        raise ValueError("Contagens finais da triagem divergentes.")
    status = "partial" if counts["not_evaluated"] or errors else "completed"
    if seen and not any_evaluated:
        status = "failed"
    if last.get("status") != status:
        raise ValueError("Estado final da triagem incompatível com seus registros.")
    if type(first.get("input_count")) is not int or first["input_count"] != len(seen):
        raise ValueError("Contagem inicial da triagem divergente.")
    return candidates, {"path": str(path), "sha256": hashlib.sha256(content).hexdigest(),
                        "status": status, "input_count": len(seen),
                        "eligible_count": len(candidates)}
