"""Métricas estáticas calculadas pelo compilador TypeScript confiável."""

import json
import shutil
import subprocess
from pathlib import Path

from .extraction_input import strict_float, strict_object
from .triage import _invalid_constant

HELPER_DIRECTORY = Path(__file__).with_name("typescript")
HELPER = HELPER_DIRECTORY / "dataset-metrics.mjs"


class DatasetToolError(RuntimeError):
    """Representa impedimento operacional do auxiliar confiável."""


def _json(value):
    return json.loads(value, object_pairs_hook=strict_object, parse_float=strict_float,
                      parse_constant=_invalid_constant)


def preflight():
    """Confere a ferramenta instalada sem carregar arquivos candidatos."""
    node = shutil.which("node")
    if not node or not HELPER.is_file():
        raise DatasetToolError("Auxiliar de métricas indisponível.")
    try:
        result = subprocess.run([node, "--max-old-space-size=512", "--input-type=module", "-e",
            "import {measureTargets} from './dataset-metrics.mjs'; import ts from 'typescript'; "
            "console.log(JSON.stringify({node:process.versions.node,typescript:ts.version,helper:typeof measureTargets}));"],
            cwd=HELPER_DIRECTORY, capture_output=True, timeout=15, check=False)
        versions = _json(result.stdout)
        if (result.returncode != 0 or type(versions) is not dict or versions.get("helper") != "function"
                or versions.get("typescript") != "6.0.3" or type(versions.get("node")) is not str
                or int(versions["node"].split(".")[0]) < 22):
            raise ValueError()
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        raise DatasetToolError("Auxiliar de métricas indisponível ou incompatível.") from None
    return {"node": node}


def measure_instances(candidates) -> dict:
    """Agrupa fontes por origem e valida integralmente as medições devolvidas."""
    if not candidates:
        return {}
    batches, ids = {}, set()
    for candidate in candidates:
        unit = candidate["unit"]
        identity = unit["instance_id"]
        if identity in ids:
            raise ValueError("Alvo de métricas duplicado.")
        ids.add(identity)
        key = (unit["repository_id"], unit["commit_sha"])
        batch = batches.setdefault(key, {"files": {}, "targets": []})
        function = unit["function"]
        target = candidate["validation_repository"]["unit_suite"]["plan"]["targets"][identity]
        path = function["path"]
        if target["id"] != identity or target["path"] != path or path not in candidate["files"]:
            raise ValueError("Alvo de métricas inconsistente.")
        content = candidate["files"][path]
        if path in batch["files"] and batch["files"][path] != content:
            raise ValueError("Fonte de métricas inconsistente.")
        batch["files"][path] = content
        batch["targets"].append({"instance_id": identity, "path": path, "start": target["start"],
                                 "end": target["end"], "kind": function["kind"],
                                 "reference_code": function["reference_code"]})
    settings = preflight()
    measured = {}
    for batch in batches.values():
        payload = {"files": [{"path": path, "content": content} for path, content in sorted(batch["files"].items())],
                   "targets": batch["targets"]}
        try:
            process = subprocess.Popen([settings["node"], "--max-old-space-size=512", str(HELPER)],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                stdout, _ = process.communicate(json.dumps(payload, ensure_ascii=True).encode("utf-8"), timeout=180)
            except BaseException:
                process.kill()
                process.communicate()
                raise
            if process.returncode == 2:
                raise ValueError("Fonte ou alvo de métricas inválido.")
            if process.returncode != 0:
                raise DatasetToolError("Falha no auxiliar de métricas.")
            try:
                result = _json(stdout)
            except (ValueError, UnicodeError):
                raise DatasetToolError("Protocolo de métricas inválido.") from None
            if (type(result) is not dict or set(result) != {"policy", "typescript", "measurements"}
                    or result["policy"] != "typescript_function_metrics_v1" or result["typescript"] != "6.0.3"
                    or type(result["measurements"]) is not list):
                raise DatasetToolError("Protocolo de métricas inválido.")
            expected = {target["instance_id"] for target in batch["targets"]}
            seen = set()
            for item in result["measurements"]:
                if (type(item) is not dict or set(item) != {"instance_id", "physical_lines", "cyclomatic_complexity"}
                        or type(item["instance_id"]) is not str or item["instance_id"] not in expected
                        or item["instance_id"] in seen or type(item["physical_lines"]) is not int
                        or item["physical_lines"] < 1 or type(item["cyclomatic_complexity"]) is not int
                        or item["cyclomatic_complexity"] < 1):
                    raise DatasetToolError("Protocolo de métricas inválido.")
                seen.add(item["instance_id"])
                measured[item["instance_id"]] = {
                    "size": {"status": "available", "physical_lines": item["physical_lines"],
                             "method": "original_declaration_physical_lines_v1", "reason": None},
                    "cyclomatic_complexity": {"status": "available", "value": item["cyclomatic_complexity"],
                                              "method": "typescript_ast_decisions_v1", "reason": None}}
            if seen != expected:
                raise DatasetToolError("Protocolo de métricas inválido.")
        except (OSError, subprocess.TimeoutExpired, UnicodeError, json.JSONDecodeError):
            raise DatasetToolError("Falha operacional no auxiliar de métricas.") from None
    return measured
