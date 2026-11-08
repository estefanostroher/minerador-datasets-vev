"""Reabre evidências V3 offline antes do encaminhamento à futura validação."""

from copy import deepcopy
import json

from .extraction import instance_id, _unit_position
from .extraction_analyzer import Analyzer, preflight
from .extraction_format import expand_instance, validate_snapshot_repository
from .extraction_snapshot import project_snapshot


def _same_json(left, right):
    """Compara tipos JSON estritamente, sem equiparar booleanos e números Python."""
    return json.dumps(left, sort_keys=True, ensure_ascii=True, allow_nan=False) == json.dumps(
        right, sort_keys=True, ensure_ascii=True, allow_nan=False)


def load_repository_context(instances, repository, reasons, store) -> dict:
    """Verifica um grupo approved V3 com uma abertura, projeção e análise offline.

    Retorna snapshot_artifact, snapshot, context, execution_requirements e o mapa
    instances (ID → unidade expandida verificada). Qualquer divergência invalida
    o lote inteiro; não há fetch nem recuperação parcial de evidências.
    """
    if type(repository.get("schema_version")) is not int or repository["schema_version"] != 3:
        raise ValueError("O consumidor exige instância e repositório V3.")
    validate_snapshot_repository(repository)
    requested = {}
    for instance in instances:
        if type(instance.get("schema_version")) is not int or instance["schema_version"] != 3:
            raise ValueError("O consumidor exige instância e repositório V3.")
        expanded = expand_instance(instance, repository, reasons)
        if expanded.get("decision") != "approved":
            raise ValueError("Somente instâncias approved podem seguir à validação.")
        identity = expanded.get("instance_id")
        if not isinstance(identity, str) or not identity or identity in requested:
            raise ValueError("Identidade de instância ausente ou duplicada.")
        requested[identity] = expanded
    if not requested:
        raise ValueError("Grupo de instâncias vazio.")
    reference = repository["context"]["snapshot"]
    if reference is None:
        raise ValueError("Snapshot completo obrigatório para consumo.")
    artifact = store.open_verified(reference)
    projection = project_snapshot(store, artifact)
    analyzer = Analyzer(preflight())
    try:
        result = analyzer.analyze(projection.files, projection.issues, snapshot=projection.snapshot,
                                  inventory=projection.inventory,
                                  analysis_complete=projection.analysis_complete)
    finally:
        analyzer.cancel()
    context = {"files": projection.files, "snapshot": projection.snapshot,
               "analysis_complete": projection.analysis_complete,
               "local_dependencies": result["local_dependencies"],
               "unresolved_references": result["unresolved_references"]}
    requirements = list({json.dumps(item, sort_keys=True): item for item in
                         projection.execution_requirements + result["execution_requirements"]}.values())
    if (not _same_json(repository["context"], context)
            or not _same_json(repository["analyzer"], result["analyzer"])
            or not _same_json(repository["execution_requirements"], requirements)):
        raise ValueError("Contexto publicado diverge dos bytes e da análise do snapshot.")
    ordinals, verified = {}, {}
    for unit in sorted(result["instances"], key=_unit_position):
        position = _unit_position(unit)
        ordinal = ordinals.get(position, 0)
        ordinals[position] = ordinal + 1
        identity = instance_id(repository, unit["function"], ordinal=ordinal)
        if identity not in requested:
            continue
        expected = {"record_type": "instance", **{key: repository[key] for key in
                    ("repository_id", "repository_url", "commit_sha", "extracted_at")},
                    "instance_id": identity, **unit}
        if not _same_json(requested[identity], expected) or unit["decision"] != "approved":
            raise ValueError(f"Evidências ou critérios publicados divergem da unidade original: instance_id={identity}.")
        verified[identity] = deepcopy(expected)
    if set(verified) != set(requested):
        missing = sorted(set(requested) - set(verified))
        raise ValueError("Identidades/posições ordinais não encontradas no snapshot original: "
                         f"instance_ids={', '.join(missing)}.")
    return {"snapshot_artifact": artifact, "snapshot": artifact.reference(),
            "resource_manifest": {"format": "git-bare-v1", "repository_id": artifact.repository_id,
                                  "commit_sha": artifact.commit_sha, "tree_oid": artifact.tree_oid,
                                  "entries": list(projection.inventory)},
            "instances": {identity: verified[identity] for identity in requested},
            "context": deepcopy(context), "execution_requirements": deepcopy(requirements)}


def load_instance_context(instance, repository, reasons, store) -> dict:
    """Mantém o contrato individual usando o mesmo núcleo estrito do consumo em lote."""
    loaded = load_repository_context([instance], repository, reasons, store)
    return {key: value for key, value in loaded.items() if key != "instances"} | {
        "instance": loaded["instances"][instance["instance_id"]]}
