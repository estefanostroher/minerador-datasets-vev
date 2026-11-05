"""Executa exclusivamente o auxiliar confiável e confere seu protocolo estático."""

import json
import re
import shutil
import subprocess
from pathlib import Path
from threading import Lock

from .extraction_input import STATES, aggregate, strict_float, strict_object, valid_error_context
from .triage import _invalid_constant
from .extraction_snapshot import SnapshotInventory

HELPER_DIRECTORY = Path(__file__).with_name("typescript")
CRITERIA = ("identifiable_implementation", "associated_documentation", "direct_test_association",
            "nontrivial_implementation", "preserved_local_context")
VALIDATION = {"status": "not_evaluated", "executed_test_cases": None,
              "suite_passed": None, "external_services": "not_evaluated"}
TIMEOUT_SECONDS = 180


class AnalyzerUnavailable(ValueError):
    """Representa falha operacional sanitizada do auxiliar, nunca rejeição científica."""


def _json(text: str):
    """Decodifica somente JSON estrito com chaves únicas."""
    return json.loads(text, parse_constant=_invalid_constant, object_pairs_hook=strict_object,
                      parse_float=strict_float)


def preflight() -> dict:
    """Confere Node >=22 e importa a dependência confiável na versão fixada.

    Não importa nem executa qualquer arquivo de candidato. Falhas são mensagens
    fixas para não revelar caminhos sensíveis ou conteúdo de subprocessos.
    """
    node = shutil.which("node")
    helper = HELPER_DIRECTORY / "analyze.mjs"
    if node is None or not helper.is_file():
        raise ValueError("Extração requer Node.js >=22 e o auxiliar TypeScript instalado.")
    try:
        package = _json((HELPER_DIRECTORY / "package.json").read_text(encoding="utf-8"))
        expected = package["dependencies"]["typescript"]
        result = subprocess.run(
            [node, "--input-type=module", "-e",
             "import { analyze } from './analyze.mjs'; import ts from 'typescript'; "
             "if(typeof analyze !== 'function') throw new Error(); "
             "console.log(JSON.stringify({node:process.versions.node,"
             "typescript:ts.version}));"],
            cwd=HELPER_DIRECTORY, capture_output=True, encoding="utf-8", timeout=15, check=False,
        )
        versions = _json(result.stdout)
        if (not isinstance(versions, dict) or not isinstance(versions.get("node"), str)
                or result.returncode != 0 or int(versions["node"].split(".")[0]) < 22
                or versions["typescript"] != expected):
            raise ValueError("Versão incompatível.")
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        raise ValueError("Extração requer Node.js >=22 e TypeScript na versão do package.json.") from error
    return {"node": node, "helper": str(helper),
            "analyzer": {"name": "typescript_compiler_api", "version": expected}}


def _require(condition: bool) -> None:
    """Interrompe protocolos malformados com mensagem fixa, sem ecoar conteúdo."""
    if not condition:
        raise AnalyzerUnavailable("Protocolo do analisador inválido ou inconsistente com o snapshot.")


def _fields(value, required, optional=()) -> None:
    """Exige objeto com campos conhecidos e obrigatórios presentes."""
    _require(isinstance(value, dict))
    _require(set(required) <= set(value) <= set(required) | set(optional))


def _string(value, *, empty=False) -> None:
    """Exige texto e, quando necessário, conteúdo não vazio."""
    _require(isinstance(value, str) and (empty or bool(value)))


def _range(value: dict, files: dict) -> str:
    """Confere caminho preservado e intervalo inclusivo de linhas da evidência."""
    path = value.get("path")
    _require(isinstance(path, str) and path in files)
    start, end = value.get("start_line"), value.get("end_line")
    lines = re.split(r"\r\n|[\n\r\u2028\u2029]", files[path])
    _require(type(start) is int and type(end) is int and 1 <= start <= end <= len(lines))
    return "\n".join(lines[start - 1:end])


def _snippet(value: dict, key: str, files: dict) -> list[tuple[int, int]]:
    """Retorna posições originais do trecho compatíveis com as linhas declaradas."""
    _range(value, files)
    snippet = value.get(key)
    _string(snippet)
    text = files[value["path"]]
    positions = [match.span() for match in re.finditer(re.escape(snippet), text)
        if len(re.split(r"\r\n|[\n\r\u2028\u2029]", text[:match.start()])) == value["start_line"]
        and len(re.split(r"\r\n|[\n\r\u2028\u2029]", text[:match.end()])) == value["end_line"]
    ]
    _require(bool(positions))
    return positions


def _run_helper(payload: str) -> str:
    """Repete o auxiliar confiável para validações avulsas do protocolo preservado."""
    node = shutil.which("node")
    _require(node is not None)
    replay = subprocess.run(
        [node, "--max-old-space-size=512", str(HELPER_DIRECTORY / "analyze.mjs")],
        input=payload, capture_output=True, encoding="utf-8", errors="strict",
        timeout=TIMEOUT_SECONDS, check=False,
    )
    _require(replay.returncode == 0)
    return replay.stdout


def _verify_context(result: dict, preserved: list[dict], runner) -> None:
    """Recalcula estaticamente aprovações e grafo com o parser confiável, sem executar candidatos.

    A lista publicada não constitui prova de completude: a repetição independente
    detecta arestas removidas, referências apagadas e raízes adulteradas. Issues
    produzidas pelo auxiliar são recalculadas; as demais conservam seu impacto.
    """
    approved = [unit for unit in result["instances"]
                if unit["criteria"]["preserved_local_context"]["status"] == "approved"]
    if not approved:
        return
    generated = {"INVALID_SOURCE", "UNRESOLVED_LOCAL_IMPORT", "UNRESOLVED_REFERENCE_PATH",
                 "INVALID_MANIFEST", "INVALID_TSCONFIG", "UNSUPPORTED_TSCONFIG",
                 "MULTIPLE_TSCONFIGS", "AMBIGUOUS_MODULE_RESOLUTION",
                 "UNCERTAIN_TEST_EXECUTION", "AMBIGUOUS_DOCUMENTATION",
                 "UNUSABLE_TEST_EVIDENCE", "UNPROVEN_IMPLEMENTATION"}
    generated.update({"UNRESOLVED_AMBIENT_REFERENCE", "UNRESOLVED_CONFIG_TYPES",
                      "UNPROVEN_RESOURCE_SCOPE", "UNRESOLVED_PROJECT_REFERENCE"})
    payload = {"files": [{"path": item["path"], "content": item["content"]} for item in preserved],
               "issues": [issue for issue in result["issues"] if issue["code"] not in generated]}
    try:
        expected = _json(runner(json.dumps(payload, ensure_ascii=False)))
        _require(expected["local_dependencies"] == result["local_dependencies"])
        _require(expected["unresolved_references"] == result["unresolved_references"])
        _require(expected["issues"] == result["issues"])
        _require([unit["function"] for unit in result["instances"]]
                 == [unit["function"] for unit in expected["instances"]])
        for unit, item in zip(result["instances"], expected["instances"]):
            if unit["criteria"]["preserved_local_context"]["status"] != "approved":
                continue
            _require(item["criteria"]["preserved_local_context"]["status"] == "approved"
                     and item["documentation"] == unit["documentation"]
                     and item["tests"] == unit["tests"]
                     and item["context_paths"] == unit["context_paths"])
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        raise AnalyzerUnavailable("Não foi possível conferir o impacto estático do contexto.") from error


def validate_output(result, preserved: list[dict], analyzer: dict, *, snapshot=None,
                    inventory=None, analysis_complete=None, _runner=None) -> dict:
    """Confere evidências e replay; V3 exige inventário de project_snapshot offline.

    Loader V3 deve reabrir o store e reprojetar antes desta chamada, passando os
    três campos keyword da projeção. Nunca inferir o modo pelos campos da saída.
    """
    snapshot_mode = snapshot is not None
    if snapshot_mode:
        _verified_inputs(preserved, inventory, snapshot, analysis_complete)
    else:
        _require(inventory is None and analysis_complete is None)
    required = ("analyzer", "instances", "local_dependencies", "unresolved_references", "issues")
    _fields(result, required + (("snapshot", "inventory", "analysis_complete", "execution_requirements") if snapshot_mode else ()))
    if snapshot_mode:
        _require(result['snapshot'] == snapshot and result['inventory'] == inventory
                 and type(result['analysis_complete']) is bool
                 and result['analysis_complete'] == analysis_complete)
        _require(isinstance(result['execution_requirements'], list))
        paths = {entry.get('path', entry.get('path_base64')) for entry in inventory}
        for requirement in result['execution_requirements']:
            _fields(requirement, ('type', 'path', 'status'))
            _require(requirement['type'] in {'test_configuration', 'environment', 'external_dependency',
                     'dynamic_loading', 'external_resource', 'gitlink', 'lfs_pointer'}
                     and requirement['status'] == 'not_evaluated'
                     and isinstance(requirement['path'], str) and requirement['path'] in paths)
    _require(result["analyzer"] == analyzer)
    files = {item["path"]: item["content"] for item in preserved}
    for key in ("instances", "local_dependencies", "unresolved_references", "issues"):
        _require(isinstance(result[key], list))
    for issue in result["issues"]:
        _fields(issue, ("code", "message"), ("path", "http_status", "rate_limit_headers"))
        _string(issue["code"])
        _string(issue["message"])
        _require(valid_error_context(issue))
        if "path" in issue:
            _string(issue["path"])
        _require(issue["code"] != "ANALYZER_FAILURE")
    for key in ("local_dependencies", "unresolved_references"):
        for dependency in result[key]:
            _fields(dependency, ("from", "specifier", "resolved_path") if key == "local_dependencies"
                    else ("from", "specifier"))
            _require(isinstance(dependency["from"], str) and dependency["from"] in files)
            _string(dependency["specifier"])
            if key == "local_dependencies":
                target = dependency["resolved_path"]
                _require(target is None or isinstance(target, str)
                         and (target in files or snapshot_mode and target in paths))
                specifier = dependency["specifier"]
                local = specifier.startswith((".", "/", "\\")) or bool(re.match(r"^[A-Za-z]:[\\/]", specifier))
                _require(target is not None or not local or snapshot_mode
                         and bool(re.search(r'(^|/)node_modules/', specifier.replace('\\', '/'))))
    seen = set()
    for unit in result["instances"]:
        _fields(unit, ("decision", "function", "documentation", "tests", "context_paths",
                       "criteria", "validation", "reasons"))
        function = unit["function"]
        _fields(function, ("kind", "name", "class_name", "path", "start_line", "end_line",
                           "signature", "reference_code"))
        _require(function["kind"] in ("function_declaration", "class_method"))
        _string(function["name"])
        _require(function["class_name"] is None or isinstance(function["class_name"], str)
                 and bool(function["class_name"]))
        if function["kind"] == "function_declaration":
            _require(function["class_name"] is None)
        positions = _snippet(function, "reference_code", files)
        _string(function["signature"])
        _require(function["reference_code"].startswith(function["signature"]))
        identity = next(((function["path"], start, end) for start, end in positions
                         if (function["path"], start, end) not in seen), None)
        _require(identity is not None)
        seen.add(identity)
        _fields(unit["criteria"], CRITERIA)
        for criterion in unit["criteria"].values():
            _fields(criterion, ("status", "reason"))
            _require(isinstance(criterion["status"], str) and criterion["status"] in STATES)
            _string(criterion["reason"])
        _require(unit["decision"] == aggregate(c["status"] for c in unit["criteria"].values()))
        _require(unit["validation"] == VALIDATION)
        _require(isinstance(unit["context_paths"], list))
        _require(all(isinstance(path, str) and path in files for path in unit["context_paths"]))
        _require(len(unit["context_paths"]) == len(set(unit["context_paths"])))
        _require(function["path"] in unit["context_paths"])
        _require(isinstance(unit["reasons"], list) and all(isinstance(r, str) for r in unit["reasons"]))
        _require(unit["reasons"] == [c["reason"] for c in unit["criteria"].values()
                                     if c["status"] != "approved"])
        _require(isinstance(unit["documentation"], list) and isinstance(unit["tests"], list))
        for doc in unit["documentation"]:
            _fields(doc, ("kind", "path", "start_line", "end_line", "original_text", "association"))
            _require(doc["kind"] in ("attached_comment", "external"))
            _snippet(doc, "original_text", files)
            association = doc["association"]
            _require(isinstance(association, dict))
            if doc["kind"] == "attached_comment":
                _require(association == {"kind": "attached"} and doc["path"] == function["path"])
            elif association.get("kind") == "source_link":
                _fields(association, ("kind", "source_path"))
                _require(association["source_path"] == function["path"])
            else:
                _fields(association, ("kind", "target"))
                _require(association["kind"] == "explicit_reference")
                _require(association["target"] == f"{function['path']}#{function['name']}")
        for test in unit["tests"]:
            _fields(test, ("path", "framework", "name", "start_line", "end_line", "parameterized",
                           "modifiers", "association"), ("skipped",))
            _range(test, files)
            _require(test["framework"] in ("vitest", "jest", "mocha", "node:test"))
            _string(test["name"], empty=True)
            _require(type(test["parameterized"]) is bool)
            _require("skipped" not in test or type(test["skipped"]) is bool)
            _require(isinstance(test["modifiers"], list)
                     and all(isinstance(item, str) for item in test["modifiers"]))
            association = test["association"]
            _fields(association, ("kind", "call_line", "expression", "resolved_target"))
            _require(association["kind"] == "direct_call")
            line = association["call_line"]
            _require(type(line) is int and test["start_line"] <= line <= test["end_line"])
            _string(association["expression"])
            text = files[test["path"]]
            _require(any(len(re.split(r"\r\n|[\n\r\u2028\u2029]", text[:match.start()])) == line
                         for match in re.finditer(re.escape(association["expression"]), text)))
            _fields(association["resolved_target"], ("path", "start_line", "name"))
            _require(type(association["resolved_target"]["start_line"]) is int)
            _require(association["resolved_target"] == {
                "path": function["path"], "start_line": function["start_line"], "name": function["name"]})
        if unit["criteria"]["associated_documentation"]["status"] == "approved":
            _require(bool(unit["documentation"]))
        if unit["criteria"]["direct_test_association"]["status"] == "approved":
            _require(any(test.get("skipped") is False for test in unit["tests"]))
    if snapshot_mode:
        # Replay único e não recursivo: inclusive critérios promovidos ou modo removido.
        payload = _snapshot_payload(preserved, inventory._issues, snapshot, inventory, analysis_complete)
        try:
            expected = _json((_runner or _run_helper)(json.dumps(payload, ensure_ascii=False)))
            _require(json.dumps(expected, sort_keys=True, ensure_ascii=True)
                     == json.dumps(result, sort_keys=True, ensure_ascii=True))
        except (OSError, UnicodeError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
            raise AnalyzerUnavailable('Não foi possível conferir as evidências estáticas do snapshot.') from error
    else:
        _verify_context(result, preserved, _runner or _run_helper)
    return result


def _verified_inputs(files, inventory, snapshot, complete):
    """Exige atestado Python e hashes dos bytes do reader, nunca declaração do auxiliar."""
    try:
        _require(isinstance(files, list) and isinstance(snapshot, dict))
        _fields(snapshot, ('format', 'cache_key', 'repository_id', 'commit_sha', 'tree_oid',
                           'manifest_sha256', 'file_count', 'total_blob_bytes', 'status'))
        for item in files:
            _fields(item, ('path', 'content', 'sha256'))
            _string(item['path'])
            _string(item['content'], empty=True)
            _string(item['sha256'])
        _require(isinstance(inventory, SnapshotInventory)
                 and inventory.verifies(snapshot, files, complete))
    except (KeyError, TypeError, UnicodeError, ValueError) as error:
        raise AnalyzerUnavailable('Entradas não correspondem à projeção verificada do snapshot.') from error


def _snapshot_payload(files, issues, snapshot, inventory, complete):
    """Constrói a entrada V3 exata compartilhada entre análise e conferência."""
    return {'files': files, 'issues': issues, 'snapshot': snapshot,
            'inventory': inventory, 'analysis_complete': complete}


class Analyzer:
    """Mantém processos confiáveis canceláveis, com heap de 512 MiB e prazo de 180 s."""

    def __init__(self, settings: dict):
        """Inicializa registro compartilhado de processos ativos sem iniciar subprocessos."""
        self.settings = settings
        self._lock = Lock()
        self._processes = set()
        self._cancelled = False

    def cancel(self) -> None:
        """Interrompe processos ativos; cada worker recolhe seu filho em finally."""
        with self._lock:
            self._cancelled = True
            for process in self._processes:
                if process.poll() is None:
                    process.kill()

    def analyze(self, files: list[dict], issues: list[dict], *, snapshot=None,
                inventory=None, analysis_complete=None) -> dict:
        """Analisa legado ou V3 com campos de project_snapshot; não executa validação.

        V3 retorna os campos antigos mais snapshot, inventory, analysis_complete e
        execution_requirements. O inventário de saída é JSON, não novo atestado.
        """
        if snapshot is not None:
            _verified_inputs(files, inventory, snapshot, analysis_complete)
            _require(issues == inventory._issues)
            data = _snapshot_payload(files, issues, snapshot, inventory, analysis_complete)
        else:
            _require(inventory is None and analysis_complete is None)
            data = {"files": [{"path": item["path"], "content": item["content"]}
                              for item in files], "issues": issues}
        payload = json.dumps(data, ensure_ascii=False)
        try:
            result = validate_output(_json(self._invoke(payload)), files, self.settings["analyzer"],
                                     snapshot=snapshot, inventory=inventory,
                                     analysis_complete=analysis_complete, _runner=self._invoke)
            _require(all(issue in result["issues"] for issue in issues))
            return result
        except (UnicodeError, ValueError) as error:
            if isinstance(error, AnalyzerUnavailable):
                raise
            raise AnalyzerUnavailable("Auxiliar TypeScript emitiu saída inválida.") from error

    def _invoke(self, payload: str) -> str:
        """Executa uma análise ou conferência com o mesmo controle de cancelamento e heap."""
        process = None
        try:
            with self._lock:
                if self._cancelled:
                    raise AnalyzerUnavailable("Análise cancelada.")
                process = subprocess.Popen(
                    [self.settings["node"], "--max-old-space-size=512", self.settings["helper"]],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    encoding="utf-8", errors="strict",
                )
                self._processes.add(process)
            stdout, _ = process.communicate(payload, timeout=TIMEOUT_SECONDS)
            if process.returncode:
                raise AnalyzerUnavailable("O auxiliar TypeScript não concluiu a análise.")
            return stdout
        except (OSError, UnicodeError, ValueError, subprocess.TimeoutExpired) as error:
            if isinstance(error, AnalyzerUnavailable):
                raise
            raise AnalyzerUnavailable("Auxiliar TypeScript indisponível, expirado ou com saída inválida.") from error
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait()
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
                with self._lock:
                    self._processes.discard(process)
