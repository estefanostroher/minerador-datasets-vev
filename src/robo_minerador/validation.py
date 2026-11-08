"""Coordena uma passagem sequencial; somente o chamador escreve o JSONL canônico."""

from copy import deepcopy

from .extraction_context import load_repository_context
from .snapshot_store import SnapshotStore
from .validation_docker import DockerExecutor
from .validation_evidence import evaluate_repository
from .validation_evaluation import operational_issues, PAIR_POLICY
from .validation_format import utc_now, encode_bytes, decision_counts, run_result
from .validation_instrument import configure_instrument
from .validation_recipe import resolve_recipe
from .validation_workspace import prepare_workspace


PHASES = ("installation", "preparation", "unit_suite")


def _record(kind, **fields):
    """Acrescenta envelope uniforme sem alterar identidades de entrada."""
    return {"schema_version": 2, "stage": "validation", "record_type": kind, "timestamp": utc_now(), **fields}


def _incorporate(value, executor):
    """Copia capturas antes de close e elimina dependência de caminhos temporários."""
    if type(value) is list:
        return [_incorporate(item, executor) for item in value]
    if type(value) is not dict:
        return value
    result = {key: _incorporate(item, executor) for key, item in value.items() if key not in {"stdout_path", "stderr_path"}}
    for stream in ("stdout", "stderr"):
        if stream + "_path" in value:
            try:
                result[stream] = encode_bytes(executor.read_log(value, stream))
            except (OSError, ValueError, KeyError) as error:
                result["capture_loss"] = "Captura indisponível: " + str(error)
    return result


def _not_started(recipe, phase, cause):
    """Explicita cada comando não iniciado sem fabricar timestamps de execução."""
    commands = (recipe or {}).get("phases", {}).get(phase, [])
    return {"phase": phase, "required": bool(commands), "status": "not_started", "cause": cause,
            "start": None, "end": None, "commands": [],
            "not_started_commands": [{**c, "status": "not_started", "cause": cause} for c in commands]}


def _release_group(executor):
    """Recolhe volumes e áreas confiáveis do grupo, mantendo spools até incorporá-los."""
    release = getattr(executor, "_release_resources", None)
    if release is not None:
        release()
    directories = getattr(executor, "_instrument_resources", [])
    while directories:
        directories.pop().cleanup()


def validate_repositories(groups, source, catalog, executor=None, store=None, *, policy_version=2):
    """Gera abertura, grupos consolidados, diagnósticos locais e fechamento.

    Fecha o executor inclusive quando injetado, depois de incorporar suas capturas;
    o chamador deve usar executor exclusivo desta execução. Store injetado permanece
    aberto. Generator.close/erro de escrita exige fechar este gerador para cancelar.
    Preflight global deve ser chamado pela CLI antes de abrir a saída; se necessário
    é repetido aqui somente quando o executor ainda não possui daemon verificado.
    Zero grupos não constrói executor/store nem exige ferramentas externas.

    A política 2 não chama observe_resources: auditoria é diagnóstico opcional fora
    do caminho de execução/publicação. Versão 1 permanece explícita para reprodução
    do contrato histórico e suas fixtures; a CLI usa sempre a versão 2.
    """
    if type(policy_version) is not int or policy_version not in {1, 2}:
        raise ValueError("Versão de política desconhecida.")
    schema = 3 if policy_version == 2 else 2
    def record(kind, **fields):
        """Publica todos os envelopes sob a mesma versão de formato."""
        return _record(kind, **{**fields, "schema_version": schema})

    groups = list(groups)
    repositories, instances = [], []
    operational = 0
    own_store = store is None
    closed = False
    try:
        if groups:
            executor = executor or DockerExecutor()
            store = store or SnapshotStore()
            if getattr(executor, "daemon", None) is None:
                executor.preflight()
        limits = [{"repository_id": group["repository"]["repository_id"],
                   "limits": deepcopy(catalog.get("recipes", {}).get((group["repository"]["repository_id"], group["repository"]["commit_sha"]), {}).get("limits"))}
                  for group in groups]
        yield record("run_started", source=deepcopy(source), policy=deepcopy(PAIR_POLICY) if policy_version == 2 else {"suite_passes": 1, "minimum_valid_cases": 4,
                      "no_corrective_retries": True, "instance_execution_scope": "associated_declarations_and_module_hooks",
                       "resource_gate_scope": "associated_closure_v1"}, limits=limits, miner_version="0.1.0", instrument_protocol_version=1)
        for group in groups:
            resolution = resolve_recipe(group["repository"], catalog)
            recipe = resolution.get("recipe")
            execution = {"phases": {p: _not_started(recipe, p, "Pré-requisito ainda não comprovado.") for p in PHASES}}
            diagnostics = []
            event_start = len(getattr(executor, "events", []))
            try:
                execution["verified"] = load_repository_context(group["instances"], group["repository"], group["reasons"], store)
                if resolution["resolution_status"] == "resolved":
                    workspace = prepare_workspace(store, execution["verified"], recipe, executor)
                    execution["environment"] = workspace.environment
                    for phase in ("installation", "preparation"):
                        result = executor.run_phase(workspace, phase, recipe)
                        execution["phases"][phase] = _incorporate(result, executor)
                        if result["status"] != "completed":
                            break
                    else:
                        # configure_instrument realiza collect_dependencies e confere
                        # lockfiles efetivos antes de aceitar a instalação do Vitest.
                        session = configure_instrument(workspace, execution["verified"], recipe, executor)
                        result = executor.run_phase(workspace, "unit_suite", recipe)
                        execution["phases"]["unit_suite"] = _incorporate(result, executor)
                        execution["observation"] = session.collect(executor, result)
                        observer = getattr(executor, "observe_resources", None) if policy_version == 1 else None
                        if observer is not None:
                            produced = observer(workspace, execution["verified"], recipe, execution["observation"])
                            if "resource_evidence" in produced:
                                execution.update({key: produced[key] for key in ("resource_evidence", "resource_local_method", "instance_resource_evidence")})
                            else:
                                execution["resource_evidence"] = produced
                else:
                    diagnostics.append(record("diagnostic", repository_id=group["repository"]["repository_id"],
                        code=resolution.get("code") or "RECIPE_UNRESOLVED", phase="recipe", operational=True,
                        message="Receita ausente ou ainda não resolvida; nenhum comando candidato iniciado.", evidence=resolution))
            except Exception as error:
                diagnostics.append(record("diagnostic", repository_id=group["repository"]["repository_id"],
                    code=getattr(error, "code", "REPOSITORY_OPERATIONAL_FAILURE"), phase="repository", operational=True,
                    message="Avaliação interrompida por impedimento operacional: " + str(error), evidence={}))
            finally:
                events = getattr(executor, "events", [])[event_start:]
                execution["infrastructure_events"] = _incorporate(events, executor)
                try:
                    _release_group(executor)
                except (ValueError, RuntimeError, OSError) as error:
                    diagnostics.append(record("diagnostic", repository_id=group["repository"]["repository_id"], code="GROUP_CLEANUP_FAILED",
                        phase="cleanup", operational=True, message="Limpeza do grupo não foi confirmada: " + str(error), evidence={}))
            repository, local = evaluate_repository(group, resolution, execution, policy_version=policy_version)
            repository["infrastructure_events"] = execution["infrastructure_events"]
            repository["timestamp"] = utc_now()
            issues = operational_issues(repository)
            if issues:
                diagnostics.append(record("diagnostic", repository_id=repository["repository_id"], code="OPERATIONAL_EVIDENCE_FAILURE",
                    phase="evaluation", operational=True, message="Impedimentos operacionais observados, independentemente da decisão científica.",
                    evidence={"issues": issues}))
            # Pendência obrigatória é auditável mesmo quando o executor não lançou.
            if repository["decision"] == "not_evaluated" and not diagnostics:
                diagnostics.append(record("diagnostic", repository_id=repository["repository_id"], code="REQUIRED_EVIDENCE_PENDING",
                    phase="evaluation", operational=True, message="Evidência obrigatória não foi comprovada.",
                    evidence={"criteria": [k for k, v in repository["criteria"].items() if v["status"] == "not_evaluated"]}))
            repositories.append(repository)
            instances.extend(local)
            operational += len(diagnostics)
            yield repository
            for instance in local:
                instance["timestamp"] = utc_now()
                yield instance
            yield from diagnostics
        if groups:
            executor.close()
            closed = True
            errors = getattr(executor, "cleanup_errors", [])
            if errors:
                operational += 1
                yield record("diagnostic", repository_id=repositories[-1]["repository_id"], code="RESOURCE_CLEANUP_FAILED",
                    phase="cleanup", operational=True, message="Limpeza de recursos próprios não foi confirmada.", evidence={"errors": errors})
        yield record("run_finished", source_sha256=source["sha256"], repository_count=len(repositories), instance_count=len(instances),
            repository_decisions=decision_counts(repositories), instance_decisions=decision_counts(instances),
            operational_errors=operational, status=run_result(repositories, instances, operational))
    finally:
        if groups and executor is not None and not closed:
            executor.cancel()
            executor.close()
        if groups and own_store and store is not None:
            store.cancel()
