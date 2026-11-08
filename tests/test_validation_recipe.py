"""Protege identidade e contenção das receitas antes da preparação de workspaces."""

from copy import deepcopy
import json

import pytest

from robo_minerador.validation_recipe import DEFAULT_CATALOG, load_recipes, resolve_recipe


def test_catalog_resolves_exact_origins_and_records_operational_gaps():
    """Ausência/resolução incompleta não vira erro científico; URL divergente é corrupção."""
    catalog = load_recipes()
    assert len(catalog["recipes"]) == 6
    for recipe in catalog["recipes"].values():
        repository = {key: recipe[key] for key in ("repository_id", "commit_sha", "repository_url")}
        result = resolve_recipe(repository, catalog)
        assert len(result["recipe_sha256"]) == 64 and result["catalog_sha256"] == catalog["sha256"]
        assert result["recipe"] == recipe and result["recipe"] is not recipe
        repository["commit_sha"] = "0" * 40
        assert resolve_recipe(repository, catalog)["code"] == "RECIPE_NOT_FOUND"
        repository["commit_sha"] = recipe["commit_sha"]
        repository["repository_url"] = "https://github.com/other/origin"
        with pytest.raises(ValueError, match="diverge"):
            resolve_recipe(repository, catalog)


@pytest.mark.parametrize("tamper", ["duplicate", "sha", "cwd", "absolute", "windows", "argv", "digest",
                                     "version", "budget", "environment", "unfrozen", "mixed_service",
                                     "--ignore-scripts", "--ignore-scripts=true", "--dry-run", "--dry-run=true",
                                     "--omit=dev", "--production", "--only=prod", "--omit dev", "pnpm_ignore_scripts"])
def test_refuses_malformed_or_escaping_recipes(tmp_path, tamper):
    """Recusa receitas que escapem do diretório ou mascarem requisitos não comprovados."""
    document = json.loads(DEFAULT_CATALOG.read_text(encoding="utf-8"))
    recipe = document["recipes"][0]
    if tamper == "duplicate":
        document["recipes"].append(deepcopy(recipe))
    elif tamper == "sha":
        recipe["commit_sha"] = "main"
    elif tamper in {"cwd", "absolute", "windows"}:
        recipe["phases"]["installation"][0]["cwd"] = {"cwd": "../outside", "absolute": "/tmp", "windows": "C:\\tmp"}[tamper]
    elif tamper == "argv":
        recipe["phases"]["unit_suite"][0]["argv"].append("--config=../outside.ts")
    elif tamper == "digest":
        recipe["image"] = "node:22"
    elif tamper == "version":
        recipe["tools"]["node"] = ">=22"
    elif tamper == "budget":
        recipe["limits"]["cpus"] = True
    elif tamper == "environment":
        recipe["environment"]["GITHUB_TOKEN"] = "host-token"
    elif tamper == "unfrozen":
        recipe["phases"]["installation"][0]["argv"] = ["npm", "install"]
    elif tamper.startswith("--"):
        recipe["phases"]["installation"][0]["argv"].extend(tamper.split())
    elif tamper == "pnpm_ignore_scripts":
        document["recipes"][-1]["phases"]["installation"][0]["argv"].append("--ignore-scripts=true")
    else:
        document["recipes"][2]["unit_scope"]["external_services"]["status"] = "rejected"
    path = tmp_path / "recipes.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="Catálogo"):
        load_recipes(path)
