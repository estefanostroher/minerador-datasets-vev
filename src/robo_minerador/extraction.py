"""Coordena extração imutável, concorrência limitada e publicação ordenada."""

import hashlib
import json
from threading import Event, Lock

from . import config
from .concurrency import RateLimitGate, WorkerPool
from .discovery import EXPECTED_API_ERRORS, _error_context
from .extraction_analyzer import Analyzer, AnalyzerUnavailable
from .extraction_snapshot import collect_context, project_snapshot
from .extraction_format import CONTEXT_POLICY, compact_records
from .snapshot_store import SnapshotStore
from .triage_snapshot import SnapshotUnavailable
from .github_api import create_client
from .triage import _timestamp


def diagnostic(event: str, message: str, **fields) -> dict:
    """Monta diagnóstico da extração com mensagem sanitizada e instante UTC."""
    return {"record_type": "diagnostic", "stage": "extraction", "event": event,
            "message": message, "observed_at": _timestamp(), **fields}


def instance_id(candidate: dict, function: dict, *, ordinal: int = 0) -> str:
    """Calcula identidade por origem e posição, sem relógio ou contagens executadas.

    A posição inclui intervalo e ordinal lexical na mesma linha, pois duas
    declarações distintas podem compartilhar linha, nome e tipo de unidade.
    """
    identity = [candidate["repository_id"], candidate["commit_sha"].lower(), function["path"],
                [function["start_line"], function["end_line"], ordinal],
                function["kind"], function["name"]]
    encoded = json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _unit_position(unit: dict) -> tuple[str, int]:
    """Ordena unidades pelo caminho original e pela linha inicial da declaração."""
    return unit["function"]["path"], unit["function"]["start_line"]


def _extract_candidate(client, candidate: dict, analyzer: Analyzer, setup_error=None, *, store=None, cancel=None) -> list[dict]:
    """Produz um grupo completo por candidato, preservando falhas como pendências."""
    files, issues = [], []
    analysis = None
    projection = None
    try:
        if setup_error is not None:
            raise setup_error
        if store is None:
            files, issues = collect_context(client, candidate)
            if files or not issues:
                analysis = analyzer.analyze(files, issues)
        else:
            try:
                artifact = store.acquire(candidate, client, cancel=cancel)
                projection = project_snapshot(store, artifact, cancel=cancel)
            except EXPECTED_API_ERRORS:
                raise
            except OSError as error:
                raise SnapshotUnavailable(
                    "SNAPSHOT_CACHE_IO_ERROR", "Falha operacional de acesso ao cache do snapshot."
                ) from error
            files, issues = projection.files, list(projection.issues)
            analysis = analyzer.analyze(files, issues, snapshot=projection.snapshot,
                                        inventory=projection.inventory,
                                        analysis_complete=projection.analysis_complete)
    except SnapshotUnavailable as error:
        issues.append({"code": error.code, "message": error.message})
    except EXPECTED_API_ERRORS as error:
        issues.append({"code": "SNAPSHOT_API_ERROR", "message": "Falha operacional ao consultar o snapshot.",
                       **_error_context(error)})
    except AnalyzerUnavailable:
        issues.append({"code": "ANALYZER_FAILURE", "message": "Analisador indisponível ou protocolo inválido."})
    units = analysis["instances"] if analysis is not None else []
    if analysis is not None:
        issues.extend(analysis["issues"])
    issues = list({json.dumps(issue, sort_keys=True, ensure_ascii=False): issue for issue in issues}.values())
    pending = any(c["status"] == "not_evaluated" for unit in units for c in unit["criteria"].values())
    status = "failed" if analysis is None else "partial" if issues or pending else "completed"
    counts = {state: sum(unit["decision"] == state for unit in units)
              for state in ("approved", "rejected", "not_evaluated")}
    origin = {"repository_id": candidate["repository_id"], "repository_url": candidate["repository_url"],
              "commit_sha": candidate["commit_sha"].lower() if store is not None else candidate["commit_sha"]}
    extracted_at = _timestamp()
    repository = {
        "record_type": "repository", **origin, "extraction_status": status,
        "analyzer": analyzer.settings["analyzer"], "extracted_at": extracted_at,
        "context": {"files": files,
                    "local_dependencies": analysis["local_dependencies"] if analysis else [],
                    "unresolved_references": analysis["unresolved_references"] if analysis else []},
        "issues": issues, "summary": {"instance_count": len(units), **counts},
    }
    if store is not None:
        repository["context_policy"] = CONTEXT_POLICY
        repository["context"].update(snapshot=projection.snapshot if projection else None,
                                      analysis_complete=projection.analysis_complete if projection else False)
        requirements = ((projection.execution_requirements if projection else [])
                        + (analysis.get("execution_requirements", []) if analysis else []))
        repository["execution_requirements"] = list({
            json.dumps(item, sort_keys=True): item for item in requirements}.values())
    records = [repository]
    ordinals = {}
    for unit in sorted(units, key=_unit_position):
        position = _unit_position(unit)
        ordinal = ordinals.get(position, 0)
        ordinals[position] = ordinal + 1
        records.append({"record_type": "instance", **origin,
                        "instance_id": instance_id(candidate, unit["function"], ordinal=ordinal),
                        "extracted_at": extracted_at, **unit})
    for issue in issues:
        records.append(diagnostic("repository_error", issue["message"],
                                  repository_id=candidate["repository_id"],
                                  **{key: value for key, value in issue.items() if key != "message"}))
    if not units and analysis is not None:
        records.append(diagnostic("no_instances", "Nenhuma declaração elegível identificada no contexto disponível.",
                                  repository_id=candidate["repository_id"]))
    return records


def extract_repositories(token: str, candidates: list[dict], source: dict, settings: dict, *, workers: int = 4, store=None):
    """Publica V3; preserva store injetado após conclusão normal para consumo offline.

    Aborto cancela permanentemente o transporte, inclusive quando externo; um
    store criado pela própria execução é cancelado também no fechamento normal.
    """
    owns_store = store is None
    store = store if store is not None else SnapshotStore(root=config.SNAPSHOT_CACHE_PATH)
    store.transport.preflight()
    yield from compact_records(_raw_extract_repositories(token, candidates, source, settings,
                               workers=workers, store=store, owns_store=owns_store), schema_version=3)


def _raw_extract_repositories(token: str, candidates: list[dict], source: dict, settings: dict, *, workers: int = 4, store=None, owns_store=False):
    """Publica grupos na ordem de entrada e fecha recursos antes da contagem final.

    error_count conta cada diagnóstico repository_error (issues únicas por
    candidato e erros de fechamento). Rejeição científica isolada não degrada
    a execução. failed exige nenhum repositório com análise útil; partial indica
    issues ou critérios pendentes. Entrada parcial é preservada como proveniência.
    Nenhum elegível resulta em completed sem cliente/rede. No cancelamento,
    filhos Node são mortos antes da espera do pool; HTTP em voo pode aguardar
    seu timeout, e cada processo tem limite independente de 180 segundos.
    """
    policy = {"strategy": "verified_git_snapshot", "context_policy": CONTEXT_POLICY,
              "cache_strategy": "verify_local_before_metadata_single_acquisition",
              "limits": {"entries": config.SNAPSHOT_MAX_ENTRIES, "blob_bytes": config.SNAPSHOT_MAX_BLOB_BYTES,
                         "disk_bytes": config.SNAPSHOT_MAX_DISK_BYTES, "acquisition_seconds": config.SNAPSHOT_TIMEOUT_SECONDS,
                         "concurrent_acquisitions": config.SNAPSHOT_MAX_CONCURRENT,
                         "text_bytes": getattr(config, "SNAPSHOT_MAX_TEXT_BYTES", 32 * 1024 * 1024),
                         "node_heap_mib": 512,
                         "node_invocation_seconds": 180}}
    cache_counts = {"cache_hits": 0, "cache_misses": 0, "cache_errors": 0}
    cancel, cache_lock = Event(), Lock()
    yield diagnostic("run_started", "Extração estática iniciada.", input_path=source["path"],
                     input_sha256=source["sha256"], input_status=source["status"],
                     input_count=source["input_count"], eligible_count=len(candidates),
                     workers=workers, **policy, **cache_counts)
    counts = {"repository_count": 0, "instance_count": 0, "approved": 0,
              "rejected": 0, "not_evaluated": 0, "error_count": 0}
    statuses = []
    if candidates:
        analyzer = Analyzer(settings)
        gate = RateLimitGate()
        normal_drained = False

        def client_factory():
            """Cria cliente exclusivo por thread com pausa de API compartilhada."""
            return create_client(token, gate=gate)

        def cancel_resources():
            """Cancela abortos; preserva store externo somente após drenar todos os workers."""
            cancel.set()
            try:
                analyzer.cancel()
            finally:
                if store is not None and (owns_store or not normal_drained):
                    store.cancel()

        with WorkerPool(workers, client_factory, on_cancel=cancel_resources) as pool:
            def extract_candidate(candidate):
                """Isola falhas operacionais de abertura sem perder candidatos seguintes."""
                try:
                    client = pool.get_client()
                except EXPECTED_API_ERRORS as error:
                    return _extract_candidate(None, candidate, analyzer, setup_error=error,
                                              store=store, cancel=cancel)
                if store is not None:
                    exists = (store.root / str(candidate["repository_id"]) / candidate["commit_sha"].lower()).exists()
                    group = _extract_candidate(client, candidate, analyzer, store=store, cancel=cancel)
                    with cache_lock:
                        if not exists:
                            cache_counts["cache_misses"] += 1
                        elif group[0]["context"]["snapshot"] is not None:
                            cache_counts["cache_hits"] += 1
                        else:
                            cache_counts["cache_errors"] += 1
                    return group
                return _extract_candidate(client, candidate, analyzer)

            results = pool.map_ordered(extract_candidate, candidates)
            try:
                for group in results:
                    repository = group[0]
                    counts["repository_count"] += 1
                    for key, value in repository["summary"].items():
                        counts[key] += value
                    statuses.append(repository["extraction_status"])
                    for record in group:
                        counts["error_count"] += record.get("event") == "repository_error"
                        yield record
                    print(f"Extração: {counts['repository_count']}/{len(candidates)} candidatos processados.", flush=True)
                normal_drained = True
            finally:
                results.close()
        for error in pool.close_errors:
            counts["error_count"] += 1
            yield diagnostic("repository_error", "Falha operacional ao fechar cliente GitHub.",
                             repository_id=None, **_error_context(error))
    status = "completed"
    if statuses and all(value == "failed" for value in statuses):
        status = "failed"
    elif any(value != "completed" for value in statuses) or counts["error_count"]:
        status = "partial"
    yield diagnostic("run_finished", "Extração encerrada.", **counts, status=status, **policy, **cache_counts)
