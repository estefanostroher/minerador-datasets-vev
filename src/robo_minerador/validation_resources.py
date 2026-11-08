"""Produz e rederiva certificados AST de recursos sem executar código candidato.

O replay portátil usa somente Node/TypeScript confiáveis do minerador e textos
incorporados. Não abre snapshots, Docker, caminhos de candidato ou arquivos TEMP.
Modelos e interpretador fazem parte da base de confiança versionada do minerador.
"""

from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from pathlib import PurePosixPath
import re
import subprocess

from .validation_recipe import _hash


ROOT = Path(__file__).parent
HELPER = ROOT / "typescript" / "validation-resources.mjs"
MODELS = json.loads((ROOT / "validation_resource_models.json").read_text(encoding="utf-8"))


def _method():
    """Identifica regras e bytes do interpretador exigidos tanto na produção como no replay."""
    return {"name": MODELS["name"], "version": MODELS["version"], "models": deepcopy(MODELS),
            "models_sha256": _hash(MODELS), "analyzer_sha256": hashlib.sha256(HELPER.read_bytes()).hexdigest(),
            "resolver_sha256": hashlib.sha256((ROOT / "typescript" / "validation-resource-resolution.mjs").read_bytes()).hexdigest()}


@lru_cache(maxsize=2)
def _analyze(payload):
    """Analisa JSON imutável; o cache inclui todos os textos e bindings, sem aceitar vereditos."""
    try:
        result = subprocess.run(["node", "--max-old-space-size=512", str(HELPER)], input=payload.encode(),
                                capture_output=True, timeout=180, shell=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("Auditor AST confiável indisponível.") from error
    if result.returncode:
        raise ValueError("Auditor AST indisponível: " + result.stderr.decode(errors="replace"))
    return json.loads(result.stdout)


def resource_paths(observation):
    """Une censo Vitest, sondas e carregamentos aditivos de módulos originais."""
    paths, modules, complete = set(), [], bool(observation.get("commands"))
    for command in observation.get("commands", []):
        report = command.get("report") or {}
        paths.update(module["file"] for suite in report.get("suites", []) for module in suite.get("planned_modules", []))
        paths.update(item["path"] for item in report.get("transforms", []))
        observed = report.get("resource_modules")
        if type(observed) is not list or not observed:
            complete = False
        else:
            modules.extend(observed)
            paths.update(item["path"] for item in observed)
    return sorted(paths), modules, complete


def _bindings(recipe, environment, observation):
    """Liga modelos Node à imagem/ferramenta efetiva e Vitest à árvore oficial de bytes."""
    node = environment.get("tools", {}).get("node")
    image = MODELS["node_images"].get(node)
    node_bound = bool(image and recipe["tools"].get("node") == node and recipe.get("image") == image
                      and environment.get("image") == image and environment.get("platform") == recipe["platform"]
                      and re.fullmatch(r"sha256:[0-9a-f]{64}", environment.get("image_id", "")))
    installations = environment.get("resource_bindings", {}).get("vitest", [])
    observed = observation.get("installations", [])
    vitest_bound = bool(installations) and all(
        item.get("version") == recipe["tools"].get("vitest")
        and MODELS["vitest_trees"].get(item.get("version")) == item.get("tree_sha256")
        and type(item.get("root")) is str and item["root"].startswith("/workspace/") for item in installations)
    vitest_bound = (vitest_bound and len({item.get("root") for item in installations}) == len(installations)
                    and {item.get("root") for item in installations} == {item.get("root") for item in observed}
                    and all(item.get("tools", {}).get("vitest") == recipe["tools"]["vitest"] for item in observed))
    bindings = {"node": {"version": node, "image": environment.get("image"), "image_id": environment.get("image_id"),
                         "platform": environment.get("platform"), "verified": node_bound},
                "vitest": deepcopy(installations), "vitest_verified": vitest_bound}
    return bindings, node_bound, vitest_bound


def produce_resource_audit(verified, recipe, observation, environment):
    """Deriva auditoria inteira e limitações; nenhum arquivo é considerado seguro por omissão."""
    from .validation_evaluation import resource_universe, _observation_complete
    sources = [{"path": file["path"], "content": file["content"]}
               for file in verified["context"]["files"] + verified.get("resource_sources", [])]
    return _produce(sources, resource_universe(verified), recipe, observation, environment, _observation_complete(observation))


def _produce(sources, universe, recipe, observation, environment, observation_complete):
    """Compartilha a derivação determinística entre execução e consumidor portátil."""
    paths, modules, module_complete = resource_paths(observation)
    bindings, node_bound, vitest_bound = _bindings(recipe, environment, observation)
    payload = {"files": sources, "models": MODELS, "vitest_bound": vitest_bound,
               "loaded_paths": sorted({item["path"] for item in modules})}
    analysis = deepcopy(_analyze(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))))
    hashes = {file["path"]: hashlib.sha256(file["content"].encode()).hexdigest() for file in sources}
    module_complete = module_complete and bool(paths) and set(paths) <= set(hashes) and all(
        item.get("sha256") == hashes.get(item.get("path")) and item.get("origin") in {"node_loader", "vite_transform"}
        for item in modules)
    limitations = []
    excluded = []
    manifest = universe.get("manifest")
    snapshot = universe["snapshot"]
    if (type(manifest) is not dict or _hash(manifest) != snapshot["manifest_sha256"]
            or any(manifest.get(key) != snapshot[key] for key in ("repository_id", "commit_sha", "tree_oid", "format"))):
        limitations.append("Inventário original sem vínculo verificável ao manifesto do snapshot.")
    else:
        entries = manifest.get("entries", [])
        texts = {file["path"]: file["content"].encode() for file in sources}
        for entry in entries:
            name = entry.get("path", "")
            inert = (PurePosixPath(name).name.upper() in MODELS["inert_file_names"]
                     or PurePosixPath(name).suffix.lower() in MODELS["inert_suffixes"])
            if (name not in texts and inert and name not in paths and entry.get("mode") == "100644"
                    and entry.get("type") == "blob" and not entry.get("lfs_pointer")):
                excluded.append({"path": name, "oid": entry["oid"], "rule": "closed_graph_inert", "resource": "none", "role": "not_used"})
        excluded_paths = {entry["path"] for entry in excluded}
        if (len(entries) != snapshot["file_count"] or len(entries) != len(texts) + len(excluded)
                or len({entry.get("path") for entry in entries}) != len(entries)
                or sum(entry.get("size", 0) for entry in entries) != snapshot["total_blob_bytes"]
                or any(entry.get("path") not in texts or entry.get("mode") not in {"100644", "100755"}
                       or entry.get("type") != "blob" or entry.get("lfs_pointer")
                       or entry.get("size") != len(texts.get(entry.get("path"), b""))
                       or entry.get("oid") != hashlib.sha1(b"blob " + str(len(texts.get(entry.get("path"), b""))).encode()
                           + b"\0" + texts.get(entry.get("path"), b"")).hexdigest() for entry in entries if entry.get("path") not in excluded_paths)):
            limitations.append("Arquivos originais omitidos, links ou conteúdo fora da linguagem modelada.")
    if recipe.get("environment"):
        limitations.append("Variáveis candidatas de ambiente sem modelo de efeitos.")
    for command in recipe["phases"]["unit_suite"]:
        argv = command["argv"]
        supported = argv in (["npm", "test"], ["pnpm", "test:unit"], ["pnpm", "run", "test:unit"])
        if argv[:3] == ["npm", "test", "--"] and len(argv) == 4:
            supported = bool(re.fullmatch(r"[\w./-]+", argv[3])) and not argv[3].startswith(("-", "/")) and ".." not in argv[3].split("/")
        if not supported:
            limitations.append("Argumentos do launcher unitário fora do modelo fechado.")
    if not node_bound:
        limitations.append("Runtime Node/imagem efetivos fora do modelo verificado.")
    if not vitest_bound:
        limitations.append("Árvore de implementação Vitest efetiva sem modelo verificado.")
    if not module_complete:
        limitations.append("Carregamentos ausentes, gerados ou divergentes das fontes originais.")
    if not observation_complete:
        limitations.append("Observação obrigatória da passagem unitária incompleta.")
    if not universe["analysis_complete"]:
        limitations.append("Projeção original incompleta.")
    effects = [effect for command in observation.get("commands", [])
               for effect in (command.get("report") or {}).get("resource_effects", [])]
    for file in analysis["files"]:
        for finding in file["findings"]:
            if finding["resource"] == "real_service" and not any(
                    effect.get("path") == file["path"] and effect.get("sha256") == file["sha256"]
                    and effect.get("api") == finding["api"] and effect.get("url") == finding["request_url"]
                    and effect.get("origin") == "native_get" and type(effect.get("line")) is int
                    and finding["start_line"] <= effect["line"] <= finding["end_line"] for effect in effects):
                limitations.append("Chamada remota aparente sem confirmação nativa vinculada à linha original.")
    complete = analysis["complete"] and not limitations
    return {"scope": "unit_suite", "scope_sha256": _hash(recipe["unit_scope"]), "snapshot": deepcopy(universe["snapshot"]),
            "universe_sha256": universe["sha256"], "method": _method(), "bindings": bindings,
            "sources": sources, "files": analysis["files"], "executed_paths": paths,
            "complete": complete, "limitations": limitations, "effects": deepcopy(effects), "excluded_files": excluded}


def verify_resource_audit(recipe, universe, audit, observation, environment):
    """Recusa classificações adulteradas por rederivação integral dos textos e bindings publicados."""
    from .validation_evaluation import _observation_complete
    if type(audit) is not dict or type(audit.get("method")) is not dict or _hash(audit["method"]) != _hash(_method()):
        return False
    sources = audit.get("sources")
    if type(sources) is not list or len(sources) != len(universe["files"]):
        return False
    expected = {file["path"]: file for file in universe["files"]}
    seen = set()
    for file in sources:
        name, text = file["path"], file["content"]
        if (name not in expected or name in seen or type(text) is not str
                or hashlib.sha256(text.encode()).hexdigest() != expected[name]["sha256"]
                or len(text.splitlines()) != expected[name]["line_count"]):
            return False
        seen.add(name)
    replay = _produce(sources, universe, recipe, observation, environment, _observation_complete(observation))
    return _hash(audit) == _hash(replay)
