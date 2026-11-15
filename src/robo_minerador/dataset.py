"""Monta registros completos a partir de entradas e medições verificadas."""

from copy import deepcopy
import hashlib

from .deduplication_representation import _snippet


METRIC_POLICY = {
    "name": "typescript_function_metrics_v1",
    "typescript": "6.0.3",
    "size": {
        "method": "original_declaration_physical_lines_v1",
        "rules": "Linhas físicas da declaração, inclusive comentários e linhas vazias; exclui documentação anterior e terminador após o fechamento. CRLF, LF, CR, U+2028 e U+2029 são terminadores.",
    },
    "cyclomatic_complexity": {
        "method": "typescript_ast_decisions_v1",
        "rules": "Base 1; soma if, for, for-in, for-of, while, do-while, case, catch, ternário e operadores &&, ||, ?? e suas atribuições. Conta cada nó uma vez; exclui default, else, switch, encadeamento opcional, operadores de tipos, parâmetros, decoradores e unidades ou classes aninhadas.",
    },
    "test_cases": "Cópia integral da validação, preservando contagem exata ou limite inferior.",
    "coverage": "Cópia integral da validação, mantendo os escopos associados e da suíte separados.",
}


def build_dataset(inputs: dict, measurements: dict, *, builder_version: str) -> list[dict]:
    """Produz uma linha independente por representante, sem modificar as entradas."""
    candidates = inputs["candidates"]
    identities = [item["unit"]["instance_id"] for item in candidates]
    if len(set(identities)) != len(identities) or set(identities) != set(measurements):
        raise ValueError("IDs de candidatas e medições incompatíveis.")

    records = []
    for candidate in sorted(candidates, key=lambda item: item["unit"]["instance_id"]):
        unit = candidate["unit"]
        files = candidate["files"]
        extraction = candidate["extraction_repository"]
        instance = candidate["validation_instance"]
        repository = candidate["validation_repository"]
        identity = unit["instance_id"]
        records.append(deepcopy({
            "schema_version": 1, "instance_id": identity, "language": "TypeScript",
            "function": unit["function"],
            "tests": [{**test, "original_text": _snippet(test, files)} for test in unit["tests"]],
            "documentation": unit["documentation"],
            "context": {
                "files": [{"path": path, "content": files[path],
                           "sha256": hashlib.sha256(files[path].encode("utf-8")).hexdigest()}
                          for path in sorted(unit["context_paths"])],
                "snapshot": extraction["context"]["snapshot"],
                "execution_requirements": extraction["execution_requirements"],
                "binary_policy": "references_only",
            },
            "metrics": {**measurements[identity], "test_cases": instance["test_cases"],
                        "coverage": instance["coverage"]},
            "source": candidate["source"],
            "environment": {key: repository[key] for key in ("recipe", "environment", "lockfiles", "execution")},
            "validation": {"instance": instance, "extraction_criteria": unit["criteria"],
                           "repository_evidence": repository},
            "deduplication": {"policy": inputs["deduplication_policy"], "representative": candidate["representative"],
                              "duplicate_origins": sorted(candidate["duplicate_origins"], key=lambda item: item["instance_id"])},
            "provenance": {"sources": inputs["sources"],
                           "builder": {"name": "robo-minerador", "version": builder_version, "policy": "dataset_build_v1"},
                           "metric_policy": METRIC_POLICY},
        }))
    return records
