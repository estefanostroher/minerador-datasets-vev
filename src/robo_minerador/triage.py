"""Valida a descoberta e coordena decisões estáticas no SHA original."""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .discovery import EXPECTED_API_ERRORS, _error_context
from .triage_checks import ALLOWED_LICENSES, CRITERION_NAMES, evaluate_snapshot
from .triage_snapshot import Snapshot, SnapshotUnavailable


def _validate_candidate(candidate: dict, seen: set[int]) -> None:
    """Valida identidade única, URL canônica e consistência do SHA observado."""
    repository_id = candidate.get("repository_id")
    if type(repository_id) is not int or repository_id <= 0 or repository_id in seen:
        raise ValueError("ID de candidato inválido ou duplicado.")
    name = candidate.get("repository")
    if (not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", name)
            or name.split("/")[1] in {".", ".."}
            or candidate.get("repository_url") != f"https://github.com/{name}"):
        raise ValueError("Identidade ou URL canônica do candidato inválida.")
    sha = candidate.get("commit_sha")
    if "commit_sha" not in candidate:
        raise ValueError("Campo SHA ausente.")
    if sha is None:
        valid = candidate.get("sha_status") == "unavailable"
    else:
        valid = (isinstance(sha, str) and re.fullmatch(r"[0-9a-fA-F]{40}", sha)
                 and candidate.get("sha_status") == "resolved")
    if not valid:
        raise ValueError("SHA ou estado de resolução inválido.")
    seen.add(repository_id)


def _invalid_constant(value: str):
    """Rejeita NaN e infinitos, aceitos pelo decodificador mas inválidos em JSON."""
    raise ValueError("Constante não permitida em JSON.")


def load_discovery(path: Path) -> tuple[list[dict], dict]:
    """Lê e valida todo o JSONL antes da rede/escrita e retorna candidatos e origem.

    A origem contém path, sha256 e status; o hash corresponde aos bytes originais.
    Falhas de formato geram ValueError; falhas de leitura preservam OSError.
    """
    content = path.read_bytes()
    try:
        records = [json.loads(line, parse_constant=_invalid_constant)
                   for line in content.decode("utf-8").splitlines()]
    except (UnicodeError, ValueError) as error:
        raise ValueError("A descoberta deve ser JSONL UTF-8 válido.") from error
    if len(records) < 2 or any(not isinstance(row, dict) for row in records):
        raise ValueError("Descoberta vazia ou registros inválidos.")
    candidates = []
    seen = set()
    for index, row in enumerate(records):
        if row.get("record_type") == "repository":
            _validate_candidate(row, seen)
            candidates.append(row)
        elif row.get("record_type") == "diagnostic":
            if row.get("stage") != "discovery" or not isinstance(row.get("event"), str):
                raise ValueError("Diagnóstico de descoberta inválido.")
            if row["event"] == "run_started" and index != 0:
                raise ValueError("Início da descoberta fora de posição.")
            if row["event"] == "run_finished" and index != len(records) - 1:
                raise ValueError("Fim da descoberta fora de posição.")
        else:
            raise ValueError("Tipo de registro desconhecido na descoberta.")
    first, last = records[0], records[-1]
    if (first.get("record_type") != "diagnostic" or first.get("event") != "run_started"
            or last.get("record_type") != "diagnostic" or last.get("event") != "run_finished"
            or type(last.get("repository_count")) is not int
            or last["repository_count"] != len(candidates)
            or last.get("status") not in {"completed", "partial", "failed"}):
        raise ValueError("Descoberta incompleta ou contagem final divergente.")
    return candidates, {
        "path": str(path), "sha256": hashlib.sha256(content).hexdigest(),
        "status": last["status"],
    }


def _timestamp() -> str:
    """Retorna o instante atual em UTC com precisão de segundos."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def diagnostic(event: str, message: str, **fields) -> dict:
    """Monta diagnóstico da triagem sem incorporar texto de exceções de rede."""
    return {"record_type": "diagnostic", "stage": "triage", "event": event,
            "message": message, "observed_at": _timestamp(), **fields}


def _pending(reason: str) -> dict:
    """Inicializa todos os critérios sem inferir aprovação ou ausência."""
    return {name: {"status": "not_evaluated", "reason": reason, "evidence": {}}
            for name in CRITERION_NAMES}


def _screen_candidate(client, candidate: dict, setup_error=None) -> tuple[dict, dict | None]:
    """Avalia um candidato; somente falhas operacionais previstas viram diagnósticos."""
    criteria = _pending("Critério não avaliado.")
    readme = None
    reasons = []
    error_record = None
    try:
        if candidate["commit_sha"] is None:
            reason = "SHA imutável ausente na descoberta."
            criteria["access"] = {"status": "not_evaluated", "reason": reason,
                                  "evidence": {"code": "missing_commit_sha"}}
            reasons.append(reason)
        else:
            if setup_error is not None:
                raise setup_error
            try:
                snapshot = Snapshot(client, candidate)
            except SnapshotUnavailable as error:
                criteria["access"] = {"status": "not_evaluated", "reason": error.message,
                                      "evidence": {"code": error.code}}
                reasons.append(error.message)
            else:
                evaluation = evaluate_snapshot(snapshot)
                criteria.update(evaluation["criteria"])
                readme = evaluation["readme"]
                reasons = evaluation["reasons"]
    except EXPECTED_API_ERRORS as error:
        reasons.append("Falha operacional ao consultar o snapshot no GitHub.")
        error_record = diagnostic(
            "repository_error", reasons[-1], repository_id=candidate["repository_id"],
            **_error_context(error),
        )
    statuses = [criteria[name]["status"] for name in CRITERION_NAMES]
    if any(status not in {"approved", "rejected", "not_evaluated"} for status in statuses):
        raise ValueError("Estado de critério desconhecido no avaliador.")
    decision = ("rejected" if "rejected" in statuses else
                "approved" if all(status == "approved" for status in statuses) else "not_evaluated")
    return {
        "record_type": "repository", "repository_id": candidate["repository_id"],
        "repository_url": candidate["repository_url"], "commit_sha": candidate["commit_sha"],
        "decision": decision, "criteria": criteria, "readme": readme,
        "reasons": reasons, "screened_at": _timestamp(),
    }, error_record


def _screen_records(client, candidates: list[dict], source: dict, setup_error=None,
                    *, screen_many=None, workers: int = 1):
    """Emite início, resultados incrementais e fim com contagens científicas e operacionais."""
    yield diagnostic(
        "run_started", "Triagem estática iniciada.", input_path=source["path"],
        input_sha256=source["sha256"], input_status=source["status"],
        input_count=len(candidates), allowed_licenses=sorted(ALLOWED_LICENSES),
        strategy="static_snapshot", workers=workers,
    )
    counts = {"approved": 0, "rejected": 0, "not_evaluated": 0}
    errors = 0
    any_evaluated = False
    results = (
        (_screen_candidate(client, candidate, setup_error) for candidate in candidates)
        if screen_many is None else screen_many(candidates)
    )
    for index, (_, (record, error)) in enumerate(zip(candidates, results, strict=True), 1):
        counts[record["decision"]] += 1
        any_evaluated |= any(
            criterion["status"] != "not_evaluated"
            for criterion in record["criteria"].values()
        )
        yield record
        if error is not None:
            errors += 1
            yield error
        if index % 25 == 0 or index == len(candidates):
            print(f"Triagem: {index}/{len(candidates)} candidatos processados.", flush=True)
    status = "partial" if counts["not_evaluated"] or errors else "completed"
    if candidates and not any_evaluated:
        status = "failed"
    yield diagnostic(
        "run_finished", "Triagem encerrada.", repository_count=len(candidates),
        **counts, error_count=errors, status=status,
    )


def screen_repositories(client, candidates: list[dict], source: dict,
                        *, screen_many=None, workers: int = 1):
    """Produz início, uma decisão por candidato, erros sanitizados e fim da triagem.

    Recebe candidatos e origem retornados por load_discovery; não escreve arquivos
    nem fecha o cliente. screen_many retorna pares (registro, erro) na ordem dos
    candidatos; workers registra a configuração. Erros de programação são propagados.
    """
    yield from _screen_records(client, candidates, source,
                               screen_many=screen_many, workers=workers)


def failed_screen_records(error: BaseException, candidates: list[dict], source: dict,
                          *, workers: int = 1):
    """Preserva candidatos pendentes quando a criação do cliente falha operacionalmente."""
    yield from _screen_records(None, candidates, source, setup_error=error, workers=workers)
