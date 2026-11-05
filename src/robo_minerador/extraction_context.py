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


def load_instance_context(instance, repository, reasons, store) -> dict:
    """Retorna {snapshot_artifact, snapshot, instance, context, execution_requirements}.

    instance contém a unidade expandida e reanalisada; context contém os textos
    originais projetados e referências locais verificadas. snapshot_artifact dá
    acesso aos bytes integrais, inclusive binários, pelo store. Nenhum fetch,
    instalação, transformação de código ou execução candidata é realizado.
    Legados podem ser inspecionados separadamente com expand_instance, mas não
    constituem entradas reproduzíveis deste consumidor. Cache ausente é erro.
    """
    if instance.get("schema_version") != 3 or repository.get("schema_version") != 3:
        raise ValueError("O consumidor exige instância e repositório V3.")
    validate_snapshot_repository(repository)
    expanded = expand_instance(instance, repository, reasons)
    if expanded.get("decision") != "approved":
        raise ValueError("Somente instâncias approved podem seguir à validação.")
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
    ordinals = {}
    for unit in sorted(result["instances"], key=_unit_position):
        position = _unit_position(unit)
        ordinal = ordinals.get(position, 0)
        ordinals[position] = ordinal + 1
        identity = instance_id(repository, unit["function"], ordinal=ordinal)
        if identity != expanded.get("instance_id"):
            continue
        expected = {"record_type": "instance", **{key: repository[key] for key in
                    ("repository_id", "repository_url", "commit_sha", "extracted_at")},
                    "instance_id": identity, **unit}
        if not _same_json(expanded, expected) or unit["decision"] != "approved":
            raise ValueError("Evidências ou critérios publicados divergem da unidade original.")
        return {"snapshot_artifact": artifact, "snapshot": artifact.reference(),
                "instance": deepcopy(expected), "context": deepcopy(context),
                "execution_requirements": deepcopy(requirements)}
    raise ValueError("Identidade/posição ordinal não encontrada no snapshot original.")
