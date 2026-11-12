"""Constrói a representação exata das evidências publicadas por instância."""

import hashlib
import json
import re


REPRESENTATION_VERSION = "exact_instance_context_v1"
POLICY = {
    "version": 1,
    "representation": REPRESENTATION_VERSION,
    "selection": "validation_instance_approved",
    "context_scope": "all_published_instance_files",
    "text_normalization": "none",
    "validation_environment_compared": False,
    "equality_confirmation": "canonical_bytes",
    "representative_selection": "lexicographic_min_instance_id",
}


def canonical_bytes(value) -> bytes:
    """Serializa JSON de forma determinística sem normalizar textos."""
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _snippet(value: dict, files: dict[str, str]) -> str:
    """Extrai linhas inclusivas preservando seus terminadores internos."""
    content = files[value["path"]]
    breaks = list(re.finditer(r"\r\n|[\n\r\u2028\u2029]", content))
    starts = [0, *(match.end() for match in breaks)]
    ends = [*(match.start() for match in breaks), len(content)]
    start, end = value["start_line"], value["end_line"]
    if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(starts):
        raise ValueError("Intervalo de linhas inválido.")
    return content[starts[start - 1]:ends[end - 1]]


def build_representation(unit: dict, files: dict[str, str]) -> bytes:
    """Reúne função, documentação, vínculos de testes e contexto integral."""
    function = unit["function"]
    _snippet(function, files)
    function_fields = ("kind", "name", "class_name", "path", "signature", "reference_code")
    documentation = []
    for doc in unit["documentation"]:
        _snippet(doc, files)
        documentation.append({key: doc[key] for key in ("kind", "path", "original_text", "association")})

    tests = []
    for test in unit["tests"]:
        item = {key: test[key] for key in ("path", "framework", "name", "parameterized", "modifiers")}
        if "skipped" in test:
            item["skipped"] = test["skipped"]
        item["original_text"] = _snippet(test, files)
        association = test["association"]
        target = association["resolved_target"]
        files[target["path"]]
        item["association"] = {
            "kind": association["kind"], "expression": association["expression"],
            "resolved_target": {key: target[key] for key in ("path", "name")},
        }
        tests.append(item)

    representation = {
        "representation_version": REPRESENTATION_VERSION,
        "function": {key: function[key] for key in function_fields},
        "documentation": sorted(documentation, key=canonical_bytes),
        "tests": sorted(tests, key=canonical_bytes),
        "context_files": [{"path": path, "content": files[path]} for path in sorted(unit["context_paths"])],
    }
    return canonical_bytes(representation)


def representation_digest(raw: bytes) -> str:
    """Calcula o índice SHA-256 dos bytes integrais da representação."""
    return hashlib.sha256(raw).hexdigest()
