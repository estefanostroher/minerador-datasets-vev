"""Carrega receitas declarativas e distingue erro de configuração de pendência.

load_recipes retorna {schema_version, sha256, recipes}, com recipes indexado por
(repository_id, commit_sha). resolve_recipe retorna {resolution_status, code,
recipe, recipe_sha256, catalog_sha256}; recipe é uma cópia integral sem metadados
do host. A resolução não baixa imagens nem executa ferramentas ou candidatos.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re

from .extraction_input import strict_float, strict_object
from .triage import _invalid_constant
from . import config

DEFAULT_CATALOG = Path(__file__).with_name("validation_recipes.json")
PHASES = ("installation", "preparation", "unit_suite")
DEFAULT_LIMITS = {"cpus": config.VALIDATION_CPUS, "memory_bytes": config.VALIDATION_MEMORY_BYTES,
                  "installation_seconds": config.VALIDATION_INSTALLATION_SECONDS,
                  "preparation_seconds": config.VALIDATION_PREPARATION_SECONDS,
                  "unit_suite_seconds": config.VALIDATION_UNIT_SUITE_SECONDS}


def _require(condition, message="Receita de validação malformada."):
    """Recusa configuração inválida antes de permitir qualquer execução."""
    if not condition:
        raise ValueError(message)


def _path(value):
    """Exige caminho POSIX relativo e canônico contido no workspace."""
    _require(isinstance(value, str) and bool(value) and "\\" not in value and ":" not in value
             and not any(ord(c) < 32 for c in value))
    path = PurePosixPath(value)
    _require(not path.is_absolute() and not PureWindowsPath(value).drive
             and ".." not in path.parts and str(path) == value)


def _hash(value):
    """Produz fingerprint da receita efetiva com ordenação e tipos JSON estáveis."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _validate_recipe(recipe):
    """Confere identidade, comandos, evidências, ambiente e orçamentos declarados."""
    fields = {"repository_id", "repository_url", "commit_sha", "recipe_version", "image", "platform", "tools",
              "lockfiles", "environment", "phases", "unit_scope", "limits", "evidence", "resolution_status", "resolution"}
    _require(isinstance(recipe, dict) and fields <= set(recipe) <= fields | {"tool_bootstrap"})
    if "tool_bootstrap" in recipe:
        bootstrap = recipe["tool_bootstrap"]
        _require(isinstance(bootstrap, dict) and set(bootstrap) == {"package", "version", "tarball", "integrity"})
        _require(bootstrap["package"] == "pnpm" and bootstrap["version"] == recipe["tools"].get("pnpm"))
        _require(bootstrap["tarball"] == "https://registry.npmjs.org/pnpm/-/pnpm-" + bootstrap["version"] + ".tgz")
        _require(isinstance(bootstrap["integrity"], str) and re.fullmatch(r"sha512-[A-Za-z0-9+/]{86}==", bootstrap["integrity"]) is not None)
    _require(type(recipe["repository_id"]) is int and recipe["repository_id"] > 0)
    _require(isinstance(recipe["repository_url"], str) and re.fullmatch(
        r"https://github\.com/[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", recipe["repository_url"]) is not None)
    _require(isinstance(recipe["commit_sha"], str) and re.fullmatch(r"[a-f0-9]{40}", recipe["commit_sha"]) is not None)
    _require(type(recipe["recipe_version"]) is int and recipe["recipe_version"] > 0)
    _require(recipe["resolution_status"] in {"resolved", "not_evaluated"})
    resolved = recipe["resolution_status"] == "resolved"
    image = recipe["image"]
    _require(image is None and not resolved or isinstance(image, str) and re.fullmatch(
        r"[a-z0-9][a-z0-9./_-]*@sha256:[a-f0-9]{64}", image) is not None,
        "Imagem deve ter digest imutável ou resolução explicitamente pendente.")
    _require(recipe["platform"] in {"linux/amd64", "linux/arm64"})
    tools = recipe["tools"]
    _require(isinstance(tools, dict) and {"node", "vitest"} < set(tools) <= {"node", "vitest", "npm", "pnpm"}
             and len(set(tools) & {"npm", "pnpm"}) == 1)
    for name, version in tools.items():
        _require(version is None and not resolved and name != "vitest" or isinstance(version, str)
                 and re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", version) is not None,
                 "Ferramenta deve ter versão exata ou resolução explicitamente pendente.")
    _require(isinstance(recipe["lockfiles"], list) and bool(recipe["lockfiles"]))
    paths = set()
    for lock in recipe["lockfiles"]:
        _require(isinstance(lock, dict) and set(lock) == {"path", "sha256", "manager"})
        _path(lock["path"])
        _require(lock["path"] not in paths and lock["manager"] in tools and lock["manager"] in {"npm", "pnpm"})
        _require(isinstance(lock["sha256"], str) and re.fullmatch(r"[a-f0-9]{64}", lock["sha256"]) is not None)
        paths.add(lock["path"])
    environment = recipe["environment"]
    _require(isinstance(environment, dict) and set(environment) <= {"CI", "TZ", "LANG", "LC_ALL", "NODE_ENV"})
    _require(all(isinstance(v, str) and not any(ord(c) < 32 for c in v) for v in environment.values()))
    phases = recipe["phases"]
    _require(isinstance(phases, dict) and set(phases) == set(PHASES))
    for phase in phases.values():
        _require(isinstance(phase, list))
        for command in phase:
            _require(isinstance(command, dict) and set(command) == {"argv", "cwd"})
            _path(command["cwd"])
            argv = command["argv"]
            _require(isinstance(argv, list) and bool(argv))
            _require(all(isinstance(a, str) and a and not any(ord(c) < 32 for c in a) for a in argv))
            _require(argv[0] in tools and argv[0] in {"node", "npm", "pnpm"})
            for argument in argv[1:]:
                _require(not any(c in argument for c in (";", "|", "&", "`")))
                value = argument.split("=", 1)[-1]
                _require("\\" not in value and not PureWindowsPath(value).drive
                         and not value.startswith("/") and ".." not in PurePosixPath(value).parts)
    _require(bool(phases["installation"]))
    for command in phases["installation"]:
        argv = command["argv"]
        _require(argv in (["npm", "ci"], ["pnpm", "install", "--frozen-lockfile"]),
                 "Instalação exige comando frozen original, sem opções que alterem scripts ou dependências.")
    scope = recipe["unit_scope"]
    _require(isinstance(scope, dict) and set(scope) == {"status", "code", "packages", "evidence", "external_services"})
    _require(scope["status"] in {"approved", "not_evaluated"})
    _require(scope["code"] is None if scope["status"] == "approved" else scope["code"] == "UNIT_SCOPE_UNRESOLVED")
    _require(isinstance(scope["packages"], list) and bool(scope["packages"]))
    package_paths = set()
    for package in scope["packages"]:
        _require(isinstance(package, dict) and set(package) == {"path", "name", "scope"})
        _path(package["path"])
        _require(package["path"] not in package_paths and isinstance(package["name"], str) and bool(package["name"])
                 and isinstance(package["scope"], str) and bool(package["scope"]))
        package_paths.add(package["path"])
    _require(scope["status"] != "approved" or bool(phases["unit_suite"]))
    _require(not resolved or scope["status"] == "approved")
    services = scope["external_services"]
    _require(isinstance(services, dict) and set(services) == {"status", "message", "evidence"}
             and services["status"] in {"approved", "rejected", "not_evaluated"}
             and isinstance(services["message"], str) and bool(services["message"]))
    _require(not (scope["status"] != "approved" and services["status"] == "rejected"),
             "Serviço da suíte mista não comprova dependência do escopo unitário.")
    limits = recipe["limits"]
    _require(isinstance(limits, dict) and set(limits) == set(DEFAULT_LIMITS))
    _require(all(type(v) is int and v > 0 for v in limits.values()))
    evidence = recipe["evidence"]
    _require(isinstance(evidence, list) and bool(evidence))
    evidence_paths = set()
    for item in evidence:
        _require(isinstance(item, dict) and set(item) == {"path", "detail"})
        _path(item["path"])
        _require(isinstance(item["detail"], str) and bool(item["detail"]))
        evidence_paths.add(item["path"])
    resolution = recipe["resolution"]
    _require(isinstance(resolution, dict) and set(resolution) == {"code", "message", "evidence"}
             and isinstance(resolution["message"], str) and bool(resolution["message"]))
    _require(resolution["code"] is None if resolved else isinstance(resolution["code"], str) and bool(resolution["code"]))
    for references in (scope["evidence"], services["evidence"], resolution["evidence"]):
        _require(isinstance(references, list) and bool(references)
                 and all(isinstance(p, str) and p in evidence_paths for p in references))
    _require(paths <= evidence_paths)


def load_recipes(path=DEFAULT_CATALOG) -> dict:
    """Valida o catálogo inteiro e indexa identidades imutáveis, sem resolver pela rede."""
    raw = Path(path).read_bytes()
    try:
        catalog = json.loads(raw.decode("utf-8"), parse_constant=_invalid_constant,
                             object_pairs_hook=strict_object, parse_float=strict_float)
        _require(isinstance(catalog, dict) and set(catalog) == {"schema_version", "recipes"})
        _require(type(catalog["schema_version"]) is int and catalog["schema_version"] == 1)
        _require(isinstance(catalog["recipes"], list))
        recipes, ids, urls = {}, set(), set()
        for recipe in catalog["recipes"]:
            _validate_recipe(recipe)
            key = recipe["repository_id"], recipe["commit_sha"]
            _require(key not in recipes and recipe["repository_id"] not in ids
                     and recipe["repository_url"].lower() not in urls, "Receita de origem duplicada ou divergente.")
            recipes[key] = recipe
            ids.add(recipe["repository_id"])
            urls.add(recipe["repository_url"].lower())
        return {"schema_version": 1, "sha256": hashlib.sha256(raw).hexdigest(), "recipes": recipes}
    except (UnicodeError, ValueError, TypeError, KeyError) as error:
        raise ValueError("Catálogo de receitas inválido.") from error


def resolve_recipe(repository, catalog) -> dict:
    """Resolve somente ID/SHA/URL exatos; ausência e ambiente incerto são operacionais.

    O workspace ainda deve conferir os hashes dos lockfiles contra o artefato
    verificado. resolved não substitui a conferência das ferramentas instaladas.
    """
    recipe = catalog["recipes"].get((repository["repository_id"], repository["commit_sha"]))
    if recipe is None:
        return {"resolution_status": "not_evaluated", "code": "RECIPE_NOT_FOUND", "recipe": None,
                "recipe_sha256": None, "catalog_sha256": catalog["sha256"]}
    _validate_recipe(recipe)
    _require(recipe["repository_url"].lower() == repository["repository_url"].lower(),
             "Identidade da receita diverge da origem publicada.")
    return {"resolution_status": recipe["resolution_status"], "code": recipe["resolution"]["code"],
            "recipe": deepcopy(recipe), "recipe_sha256": _hash(recipe), "catalog_sha256": catalog["sha256"]}
