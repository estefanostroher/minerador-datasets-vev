"""Reavalia provas persistidas sob política explícita, sem executar candidatos."""

from copy import deepcopy

from .validation_evaluation import PAIR_POLICY, evaluate_repository, operational_issues
from .validation_format import (utc_now, decision_counts, run_result, validate_output_records,
                                _validate_policy, _hash)


def replay_pair_records(records, *, source_path, source_sha256):
    """Produz publicação v3 separada a partir de observações históricas v2.

    O chamador calcula o digest dos bytes originais e usa parsing JSON estrito.
    Critérios antigos não são certificados por modelos de recursos atuais: ficam
    rotulados como diagnóstico herdado. As decisões novas são todas rederivadas
    das fases, ambiente, plano e streams salvos pelo avaliador e leitor portáteis.
    """
    records = list(records)
    if (len(records) < 2 or records[0].get("record_type") != "run_started"
            or records[-1].get("record_type") != "run_finished"
            or any(row.get("schema_version") != 2 for row in records)
            or not isinstance(source_path, str) or not source_path or not _hash(source_sha256)):
        raise ValueError("Replay exige publicação histórica v2 fechada e proveniência explícita.")
    _validate_policy(records[0])
    stamp = utc_now()

    def envelope(kind, **fields):
        """Marca o instante da reavaliação, sem alterar horários das execuções salvas."""
        return {"schema_version": 3, "stage": "validation", "record_type": kind, "timestamp": stamp, **fields}

    started = deepcopy(records[0])
    started.update(schema_version=3, timestamp=stamp, policy=deepcopy(PAIR_POLICY), replay={
        "kind": "posthoc_saved_evidence", "path": source_path, "sha256": source_sha256,
        "schema_version": 2, "policy": deepcopy(records[0]["policy"]),
        "original_finished_at": records[-1]["timestamp"], "candidate_execution": False})
    output, repositories, instances = [started], [], []
    operational = 0
    for original in (row for row in records if row.get("record_type") == "repository"):
        saved = [row for row in records if row.get("record_type") == "instance"
                 and row.get("repository_id") == original["repository_id"]]
        observation = original["unit_suite"]
        units = {}
        for row in saved:
            target = observation.get("plan", {}).get("targets", {}).get(row["instance_id"], {})
            units[row["instance_id"]] = {"instance_id": row["instance_id"], "function": {"path": target.get("path", "")},
                "tests": [deepcopy(item["test"]) for item in row["test_cases"]["associations"]]}
        verified = None
        if original.get("snapshot") is not None:
            verified = {"snapshot": original["snapshot"], "instances": units,
                        "context": {"files": (original.get("resource_evidence") or {}).get("sources", [])},
                        "resource_universe": original.get("resource_universe")}
        group = {"repository": original, "instances": [{"instance_id": row["instance_id"]} for row in saved]}
        execution = {"verified": verified, "phases": original["execution"], "observation": observation,
            "environment": original["environment"], "resource_evidence": original.get("resource_evidence"),
            "resource_local_method": original.get("resource_local_method"),
            "instance_resource_evidence": original.get("instance_resource_evidence", {}),
            "resource_diagnostics": {"mode": "historical_not_recertified",
                "global_criterion": original["criteria"]["no_external_services"],
                "instance_criteria": {row["instance_id"]: row["criteria"]["no_external_services"] for row in saved}}}
        repository, local = evaluate_repository(group, original["recipe"], execution, policy_version=2)
        repository.update(timestamp=stamp, infrastructure_events=deepcopy(original.get("infrastructure_events", [])),
            historical_diagnostics=[deepcopy(row) for row in records if row.get("record_type") == "diagnostic"
                                    and row.get("repository_id") == original["repository_id"]])
        for row in local:
            row["timestamp"] = stamp
        output.extend([repository, *local])
        repositories.append(repository)
        instances.extend(local)
        issues = operational_issues(repository)
        if issues or repository["decision"] == "not_evaluated":
            operational += 1
            output.append(envelope("diagnostic", repository_id=repository["repository_id"], operational=True,
                code="REPLAY_REQUIRED_EVIDENCE_PENDING", phase="replay",
                message="Impedimentos ou pré-requisitos obrigatórios nas evidências salvas; nenhuma execução nova.",
                evidence={"issues": issues, "criteria": [key for key, value in repository["criteria"].items()
                                                       if value["status"] == "not_evaluated"]}))
    output.append(envelope("run_finished", source_sha256=started["source"]["sha256"],
        repository_count=len(repositories), instance_count=len(instances),
        repository_decisions=decision_counts(repositories), instance_decisions=decision_counts(instances),
        operational_errors=operational, status=run_result(repositories, instances, operational)))
    validate_output_records(output)
    return output
