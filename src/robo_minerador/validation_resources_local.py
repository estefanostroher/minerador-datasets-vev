"""Deriva e reavalia recursos do fechamento associado, sem herdar decisão global."""

from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

from .validation_recipe import _hash, _path
from .validation_resources import MODELS, _method, _bindings


ROOT = Path(__file__).parent
HELPER = ROOT / "typescript" / "validation-resource-closure.mjs"
LOCAL_MODELS = json.loads((ROOT / "validation_resource_local_models.json").read_text(encoding="utf-8"))


def local_resource_method():
    """Versiona fechamento/loader separadamente, preservando o interpretador global histórico."""
    return {"name": LOCAL_MODELS["name"], "version": LOCAL_MODELS["version"], "models": deepcopy(LOCAL_MODELS),
            "base_method": _method(), "analyzer_sha256": hashlib.sha256(HELPER.read_bytes()).hexdigest(),
            "producer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


@lru_cache(maxsize=2)
def _closure(payload):
    """Interpreta fontes em memória; cache depende de todos os textos, raízes e modelos."""
    try:
        result = subprocess.run(["node", "--max-old-space-size=512", str(HELPER)], input=payload.encode(),
                                capture_output=True, timeout=180, shell=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("Auxiliar confiável do fechamento indisponível.") from error
    if result.returncode:
        raise ValueError("Falha no fechamento AST: " + result.stderr.decode(errors="replace"))
    return json.loads(result.stdout)


def _tests(unit):
    """Retém metadados originais de associação sem duplicar trechos de código por função."""
    return [{key: value for key, value in test.items() if key not in {"code", "text", "source", "reference_code", "title"}}
            for test in unit.get("tests", [])]


def _relative(value):
    """Aceita somente endereços canônicos da montagem reproduzível ou do snapshot."""
    if value == "/workspace":
        return "."
    if value.startswith("/workspace/"):
        value = value[len("/workspace/"):]
    _path(value)
    return value


def _facts(unit, recipe, observation, inventory):
    """Rederiva contextos associados e configurações originais realmente selecionadas."""
    from .validation_evaluation import associated_tests_execution
    execution = associated_tests_execution(unit, observation)
    limitations = []
    if execution["status"] != "approved":
        limitations.append("Execução local sem fechamento verificável de declarações e hooks.")
    contexts = execution["evidence"].get("contexts", [])
    roots = {test["path"] for test in unit.get("tests", [])}
    target = observation.get("plan", {}).get("targets", {}).get(unit["instance_id"])
    if target:
        roots.add(target["path"])
    else:
        limitations.append("Alvo original sem plano verificado.")
    configurations, runtime = set(), []
    for context in contexts:
        command = observation["commands"][context["command_index"]]
        suites = [suite for suite in command["report"]["suites"] if suite["suite_id"] == context["suite_id"]]
        if len(suites) != 1:
            raise ValueError("Identidade de suíte ambígua.")
        suite = suites[0]
        matches = [fact for fact in suite.get("resource_contexts", []) if
                   (fact.get("file"), fact.get("project_id")) == (context["file"], context["project_id"])]
        if len(matches) != 1:
            limitations.append("Configuração/isolamento efetivos do módulo não observados.")
            continue
        fact = matches[0]
        root_configuration = suite.get("resource_root_configuration")
        runtime.append({"context": deepcopy(context), "configuration": deepcopy(fact),
                        "root_configuration": deepcopy(root_configuration),
                        "installation": deepcopy(suite.get("resource_installation", {}))})
        configuration_keys = ("project_id", "root", "config_file", "config_dependencies", "config_sources", "setup_files", "global_setup", "pool", "isolate", "environment")
        if (type(root_configuration) is not dict or any(key not in root_configuration for key in configuration_keys)
                or _hash({key: root_configuration[key] for key in configuration_keys}) != _hash({key: fact.get(key) for key in configuration_keys})):
            limitations.append("Configuração raiz compartilhada ausente ou composição de projetos não modelada.")
        if fact.get("pool") != "forks" or fact.get("isolate") is not True or fact.get("environment") != "node":
            limitations.append("Isolamento/ambiente fora do modelo local verificado.")
        cwd = _relative(suite["cwd"])
        root = _relative(fact["root"])
        if cwd != root:
            limitations.append("Raiz de projeto composta ou herdada não modelada.")
        candidates = {name for name in inventory if str(PurePosixPath(name).parent) == cwd
                      and PurePosixPath(name).name in LOCAL_MODELS["configuration_files"]}
        config = fact.get("config_file")
        if config is None:
            if candidates:
                limitations.append("Configuração original existente sem vínculo ao loader efetivo.")
        else:
            _path(config)
            if config not in candidates or len(candidates) != 1:
                limitations.append("Descoberta original de configuração ambígua ou não modelada.")
            configurations.add(config)
            roots.add(config)
        for key in ("config_dependencies", "setup_files", "global_setup"):
            values = fact.get(key)
            if type(values) is not list:
                limitations.append("Grafo efetivo de configuração/setup incompleto.")
                continue
            for name in values:
                _path(name)
                roots.add(name)
        # Dotenv e configurações de ambiente não podem desaparecer da projeção.
        for name in inventory:
            if str(PurePosixPath(name).parent) == cwd and PurePosixPath(name).name.startswith(".env"):
                roots.add(name)
    if recipe.get("unit_scope", {}).get("status") != "approved":
        limitations.append("Escopo unitário original não comprovado.")
    return target, contexts, runtime, roots, configurations, limitations


def _local_bindings(recipe, environment, observation, runtime):
    """Restringe bindings aos processos pertinentes e verifica os bytes oficiais do loader Vite."""
    roots = {item["installation"].get("vitest_root") for item in runtime}
    selected = [item for item in observation.get("installations", []) if item.get("root") in roots]
    effective = deepcopy(environment)
    records = environment.get("resource_bindings", {})
    effective["resource_bindings"] = {"vitest": [item for item in records.get("vitest", []) if item.get("root") in roots]}
    bindings, node, vitest = _bindings(recipe, effective, {"installations": selected})
    loader_roots = {item["installation"].get("vite_root") for item in runtime}
    loaders = [item for item in records.get("vite", []) if item.get("root") in loader_roots]
    vite = bool(runtime and loaders and selected) and None not in roots | loader_roots
    vite = vite and {item.get("root") for item in selected} == roots and {item.get("root") for item in loaders} == loader_roots
    for root in roots | loader_roots:
        if type(root) is not str or not root.startswith("/workspace/"):
            vite = False
        else:
            _relative(root)
    vite = vite and len({item.get("root") for item in loaders}) == len(loaders)
    for item in selected:
        matches = [loader for loader in loaders if loader["root"] == item.get("vite_root")]
        vite = vite and len(matches) == 1 and matches[0].get("version") == item.get("tools", {}).get("vite")
    vite = vite and all(type(item.get("version")) is str and item["version"] in LOCAL_MODELS["vite_trees"]
                        and LOCAL_MODELS["vite_trees"][item["version"]] == item.get("tree_sha256") for item in loaders)
    bindings.update(vite=deepcopy(loaders), vite_verified=bool(vite))
    return bindings, bool(node and vitest and vite)


def _source_pool(shared):
    """Lê a única tabela compartilhada de fontes; duplicidade impede resolução inequívoca."""
    sources = (shared or {}).get("sources", [])
    if type(sources) is not list:
        raise ValueError("Tabela compartilhada de fontes inválida.")
    table = {}
    for file in sources:
        name, content = file["path"], file["content"]
        _path(name)
        if name in table or type(content) is not str:
            raise ValueError("Fonte compartilhada duplicada ou sem texto original.")
        table[name] = content
    return table


def _produce(unit, recipe, universe, shared, observation, environment, method):
    """Fecha e comprova o conjunto local sem ler status global ou exigir fontes alheias."""
    snapshot, manifest = universe["snapshot"], universe.get("manifest")
    limitations = []
    if (type(manifest) is not dict or _hash(manifest) != snapshot["manifest_sha256"]
            or any(manifest.get(key) != snapshot[key] for key in ("repository_id", "commit_sha", "tree_oid", "format"))):
        raise ValueError("Manifesto local não confere com snapshot original.")
    entries = manifest["entries"]
    if (len(entries) != snapshot["file_count"] or len({item.get("path") for item in entries}) != len(entries)
            or sum(item.get("size", 0) for item in entries) != snapshot["total_blob_bytes"]):
        raise ValueError("Inventário original incompleto ou ambíguo.")
    inventory = {item.get("path"): item for item in entries}
    if None in inventory:
        limitations.append("Inventário contém nome não representável; resolução global de caminhos não comprovada.")
    sources = _source_pool(shared)
    target, contexts, runtime, roots, config_paths, gaps = _facts(unit, recipe, observation, inventory)
    limitations.extend(gaps)
    bindings, bound = _local_bindings(recipe, environment, observation, runtime)
    if not bound:
        limitations.append("Implementações efetivas de Node/Vitest/Vite não conferem com os modelos locais.")
    loaded = []
    for context in contexts:
        report = observation["commands"][context["command_index"]]["report"]
        loaded.extend(item["path"] for item in report.get("resource_modules", []) if item.get("suite_id") == context["suite_id"])
    payload = {"files": [{"path": name, "content": text} for name, text in sources.items()], "inventory_paths": sorted(name for name in inventory if name is not None),
               "roots": sorted(roots), "configuration_paths": sorted(config_paths), "loaded_paths": sorted(set(loaded)),
               "models": MODELS, "vitest_bound": bindings["vitest_verified"]}
    analysis = deepcopy(_closure(json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))))
    limitations.extend(analysis["limitations"])
    closure = set(analysis["closure_paths"])
    original = {file["path"]: file for file in universe["files"]}
    for name in closure:
        text, entry, expected = sources.get(name), inventory.get(name), original.get(name)
        if text is None or entry is None or expected is None:
            limitations.append("Fonte necessária fora das evidências originais: " + name)
            continue
        raw = text.encode()
        if (entry.get("mode") not in {"100644", "100755"} or entry.get("type") != "blob" or entry.get("lfs_pointer")
                or entry.get("size") != len(raw) or entry.get("oid") != hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
                or expected["sha256"] != hashlib.sha256(raw).hexdigest() or expected["line_count"] != len(text.splitlines())):
            limitations.append("Bytes/modo da fonte necessária divergem do snapshot: " + name)
    for item in runtime:
        fact = item["configuration"]
        configs = [config for config in analysis["configurations"] if config["path"] == fact.get("config_file")]
        expected_setup = configs[0]["setupFiles"] if len(configs) == 1 else []
        expected_global = configs[0]["globalSetup"] if len(configs) == 1 else []
        if fact.get("config_file") is not None and len(configs) != 1:
            limitations.append("Configuração original sem derivação verificável de setups/hooks: " + fact["config_file"])
        elif (sorted(fact.get("setup_files", [])) != sorted(expected_setup)
                or sorted(fact.get("global_setup", [])) != sorted(expected_global)):
            limitations.append("Setups/hooks efetivos divergem da configuração original rederivada.")
        if fact.get("config_file") is not None and fact["config_file"] not in fact.get("config_dependencies", []):
            limitations.append("Loader não declarou a dependência da configuração original.")
    observed, effects = [], []
    # O hash atesta bytes, não compreensão da configuração; AST opaca segue pendente separadamente.
    hashes = {name: hashlib.sha256(sources[name].encode()).hexdigest() for name in closure if name in sources}
    for command_index, suite_id in sorted({(ctx["command_index"], ctx["suite_id"]) for ctx in contexts}):
        report = observation["commands"][command_index]["report"]
        coverage = set()
        for module_index, module in enumerate(report.get("resource_modules", [])):
            if module.get("suite_id") != suite_id or module.get("path") not in closure:
                continue
            if module.get("sha256") != hashes.get(module["path"]) or module.get("origin") not in {"node_loader", "vite_transform"}:
                limitations.append("Módulo local carregado com bytes/origem divergentes: " + module["path"])
            else:
                coverage.add(module["path"])
                observed.append({"command_index": command_index, "module_index": module_index})
        for item in runtime:
            if (item["context"]["command_index"], item["context"]["suite_id"]) != (command_index, suite_id):
                continue
            for source in item["configuration"].get("config_sources", []):
                if source["path"] not in closure or source.get("sha256") != hashes.get(source["path"]):
                    limitations.append("Fonte do loader sem vínculo ao fechamento original.")
                else:
                    coverage.add(source["path"])
        executable = {name for name in closure if re.search(r"\.(?:[cm]?[jt]s|[jt]sx)$", name)}
        if not executable <= coverage:
            limitations.append("Configuração, setup ou import local sem cobertura de carregamento verificável: "
                               + ", ".join(sorted(executable - coverage)))
        for effect_index, effect in enumerate(report.get("resource_effects", [])):
            if effect.get("path") not in closure or effect.get("suite_id") != suite_id:
                continue
            effects.append({"command_index": command_index, "effect_index": effect_index})
            matching = [finding for file in analysis["files"] if file["path"] == effect["path"] for finding in file["findings"]
                        if finding["resource"] == "real_service" and finding.get("api") == effect.get("api")
                        and finding.get("request_url") == effect.get("url") and type(effect.get("line")) is int
                        and finding["start_line"] <= effect["line"] <= finding["end_line"]]
            if effect.get("sha256") != hashes.get(effect["path"]) or effect.get("origin") != "native_get" or not matching:
                limitations.append("Efeito local observado sem derivação/origem correspondente.")
    for file in analysis["files"]:
        text = sources[file["path"]]
        lines = {line for finding in file["findings"] for line in range(finding["start_line"], finding["end_line"] + 1)}
        if lines != set(range(1, len(text.splitlines()) + 1)):
            limitations.append("Cobertura estrutural de linhas locais incompleta.")
        for finding in file["findings"]:
            if finding["resource"] == "real_service" and not any(
                    observation["commands"][ref["command_index"]]["report"]["resource_effects"][ref["effect_index"]].get("path") == file["path"]
                    and observation["commands"][ref["command_index"]]["report"]["resource_effects"][ref["effect_index"]].get("api") == finding.get("api")
                    and observation["commands"][ref["command_index"]]["report"]["resource_effects"][ref["effect_index"]].get("url") == finding.get("request_url")
                    and type(observation["commands"][ref["command_index"]]["report"]["resource_effects"][ref["effect_index"]].get("line")) is int
                    and finding["start_line"] <= observation["commands"][ref["command_index"]]["report"]["resource_effects"][ref["effect_index"]]["line"] <= finding["end_line"] for ref in effects):
                limitations.append("Serviço local aparente sem confirmação nativa pertinente.")
    if recipe.get("environment"):
        limitations.append("Ambiente candidato com efeitos não modelados.")
    for index in {ctx["command_index"] for ctx in contexts}:
        argv = recipe["phases"]["unit_suite"][index]["argv"]
        valid = argv in (["npm", "test"], ["pnpm", "test:unit"], ["pnpm", "run", "test:unit"])
        if len(argv) == 4 and argv[:3] == ["npm", "test", "--"]:
            valid = bool(re.fullmatch(r"[\w./-]+", argv[3])) and not argv[3].startswith(("-", "/")) and ".." not in argv[3].split("/")
        if not valid:
            limitations.append("Launcher associado não modelado.")
    return {"scope": "associated_closure", "instance_id": unit["instance_id"], "scope_sha256": _hash(recipe["unit_scope"]),
            "recipe_sha256": _hash(recipe), "universe_sha256": universe["sha256"], "method_sha256": _hash(method),
            "source_ref": "resource_evidence.sources", "target": deepcopy(target), "associations_sha256": _hash(_tests(unit)),
            "contexts": contexts, "configuration_contexts": runtime, "roots": sorted(roots), "bindings": bindings,
            "closure_paths": analysis["closure_paths"], "edges": analysis["edges"], "files": analysis["files"],
            "observed_modules": observed, "observed_effects": effects,
            "complete": bool(contexts) and analysis["complete"] and not limitations, "limitations": sorted(set(limitations))}


def produce_instance_resource_audits(verified, recipe, observation, environment, shared):
    """Produz mapa local usando uma única tabela de fontes compartilhada no repositório."""
    from .validation_evaluation import resource_universe
    method, audits = local_resource_method(), {}
    for instance_id, unit in verified.get("instances", {}).items():
        try:
            audits[instance_id] = _produce(unit, recipe, resource_universe(verified), shared, observation, environment, method)
        except (ValueError, KeyError, TypeError, AttributeError, UnicodeError, OSError, RuntimeError, RecursionError) as error:
            audits[instance_id] = {"scope": "associated_closure", "instance_id": instance_id, "complete": False,
                                   "method_sha256": _hash(method), "limitations": ["Prova local indisponível: " + str(error)]}
    return {"resource_local_method": method, "instance_resource_evidence": audits}


def evaluate_instance_resources(unit, recipe, universe, shared, method, audit, observation, environment):
    """Rederiva o escopo local; integridade/flags sozinhas nunca demonstram elegibilidade."""
    from .validation_evaluation import criterion
    pending = criterion(code="INSTANCE_RESOURCE_PROOF_UNAVAILABLE", message="Recursos do conjunto associado ainda sem prova local suficiente.")
    if not recipe or not universe or type(audit) is not dict:
        return pending
    try:
        if _hash(method) != _hash(local_resource_method()):
            return pending
        replay = _produce(unit, recipe, universe, shared, observation, environment, method)
        if _hash(audit) != _hash(replay):
            return criterion(code="INSTANCE_RESOURCE_AUDIT_INVALID", message="Fechamento, modelos ou efeitos locais divergem da prova rederivada.")
        reference = {"instance_id": unit["instance_id"], "audit_sha256": _hash(audit), "method_sha256": _hash(method),
                     "universe_sha256": universe["sha256"], "scope": "associated_closure"}
        if replay["complete"] is not True:
            return criterion(code="INSTANCE_RESOURCE_PROOF_UNAVAILABLE", message="Recursos do conjunto associado ainda sem prova local suficiente.", evidence=reference)
        forbidden = any(finding["resource"] == "real_service" and finding["role"] == "effective_dependency"
                        for file in replay["files"] for finding in file["findings"])
        return criterion("rejected" if forbidden else "approved", "INSTANCE_PROHIBITED_SERVICE" if forbidden else "INSTANCE_RESOURCE_AUDIT",
                         "Serviço real necessário ao fechamento associado comprovado." if forbidden else "Fechamento associado demonstra somente recursos permitidos.", reference)
    except (ValueError, KeyError, TypeError, AttributeError, UnicodeError, OSError, RuntimeError, RecursionError):
        return pending
