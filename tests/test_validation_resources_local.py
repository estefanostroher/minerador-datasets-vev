"""Protege o fechamento local contra herança global, omissões e provas inventadas."""

from copy import deepcopy
import hashlib

import pytest

from test_validation import controlled_fixture, bind_resource_snapshot
from robo_minerador.validation_evaluation import resource_universe
from robo_minerador.validation_recipe import resolve_recipe


def local_fixture(extra=None, config=None):
    """Mantém provas de casos reais da fixture e acrescenta fontes originais controladas."""
    group, catalog, _, execution = controlled_fixture((4,))
    recipe = resolve_recipe(group["repository"], catalog)["recipe"]
    files = execution["verified"]["context"]["files"]
    files.extend({"path": path, "content": content} for path, content in (extra or {}).items())
    if config is not None:
        next(file for file in files if file["path"] == "vitest.config.ts")["content"] = config
    bind_resource_snapshot(execution["verified"])
    # A fonte comum é independente do veredito global; uma fonte alheia pode ser opaca.
    execution["resource_evidence"]["sources"] = [{"path": f["path"], "content": f["content"]} for f in files]
    return recipe, execution


def produce_local(recipe, execution):
    """Usa o produtor e a derivação reais, sem substituir AST ou modelos."""
    from robo_minerador.validation_resources_local import produce_instance_resource_audits
    return produce_instance_resource_audits(execution["verified"], recipe, execution["observation"],
                                           execution["environment"], execution["resource_evidence"])


def verdict(recipe, execution, bundle=None):
    """Reavalia a prova local somente com os artefatos portáteis publicados."""
    from robo_minerador.validation_resources_local import evaluate_instance_resources
    bundle = bundle or produce_local(recipe, execution)
    unit = next(iter(execution["verified"]["instances"].values()))
    audit = bundle["instance_resource_evidence"][unit["instance_id"]]
    result = evaluate_instance_resources(unit, recipe, resource_universe(execution["verified"]),
        execution["resource_evidence"], bundle["resource_local_method"], audit,
        execution["observation"], execution["environment"])
    return result, audit


def test_unrelated_opaque_or_real_service_file_does_not_gate_local_resources():
    """Arquivo de outra unidade não entra por enumeração global nem por decisão alheia."""
    recipe, execution = local_fixture({"unrelated.test.ts": "import {get} from 'node:https'; get('https://api.github.com/repos');",
                                       "opaque.ts": "eval(process.env.UNMODELED);"})
    result, audit = verdict(recipe, execution)
    assert result["status"] == "approved", audit
    assert "unrelated.test.ts" not in audit["closure_paths"]
    assert "opaque.ts" not in audit["closure_paths"]


@pytest.mark.parametrize("body,expected", [
    ("import {vi} from 'vitest'; const fake=vi.fn(()=>42);", "approved"),
    ("import {DatabaseSync} from 'node:sqlite'; const db=new DatabaseSync(':memory:'); db.close();", "approved"),
    ("import {createServer} from 'node:http'; const server=createServer((req,res)=>{res.end('fixture');}); server.listen(0,'127.0.0.1'); server.close();", "approved"),
    ("import {mkdtempSync,writeFileSync} from 'node:fs'; import {tmpdir} from 'node:os'; import {join} from 'node:path';"
     "const dir=mkdtempSync(join(tmpdir(),'fixture-')); writeFileSync(join(dir,'data.txt'),'fixture');", "approved"),
    ("import {unknown} from 'unmodeled-sdk'; unknown();", "not_evaluated"),
    ("import {beforeAll} from 'vitest'; beforeAll(()=>import('unmodeled-sdk'));", "not_evaluated"),
])
def test_shared_setup_and_hooks_belong_to_closure(body, expected):
    """Setups e hooks compartilhados são analisados, não descartados pelos IDs dos casos."""
    recipe, execution = local_fixture({"setup.ts": "import './transitive';", "transitive.ts": body},
        "export default {test:{include:['a.test.ts'],setupFiles:['./setup.ts']}};")
    facts = execution["observation"]["commands"][0]["report"]["suites"][0]["resource_contexts"][0]
    facts["setup_files"] = ["setup.ts"]
    add_observed(execution, ["setup.ts", "transitive.ts"])
    refresh_config(execution)
    result, audit = verdict(recipe, execution)
    assert result["status"] == expected, audit
    assert {"setup.ts", "transitive.ts"} <= set(audit["closure_paths"])


def test_effective_real_service_in_shared_setup_rejects_only_local_closure():
    """Serviço real em setup pertinente exige confirmação nativa; não é herança do repositório."""
    text = "import {get} from 'node:https'; get('https://api.github.com/repos');"
    recipe, execution = local_fixture({"setup.ts": text}, "export default {test:{setupFiles:['./setup.ts']}};")
    facts = execution["observation"]["commands"][0]["report"]["suites"][0]["resource_contexts"][0]
    facts["setup_files"] = ["setup.ts"]
    add_observed(execution, ["setup.ts"])
    refresh_config(execution)
    effect = {"path": "setup.ts", "sha256": hashlib.sha256(text.encode()).hexdigest(), "api": "node:https.get",
              "url": "https://api.github.com/repos", "line": 1, "column": 31, "origin": "native_get", "suite_id": "suite"}
    execution["observation"]["commands"][0]["report"]["resource_effects"] = [effect]
    assert verdict(recipe, execution)[0]["status"] == "rejected"
    execution["observation"]["commands"][0]["report"]["resource_effects"] = []
    assert verdict(recipe, execution)[0]["status"] == "not_evaluated"


def test_confirmed_service_outside_local_closure_is_not_inherited():
    """Auditoria global pode rejeitar serviço comprovado em outro módulo sem rejeitar esta função."""
    from robo_minerador.validation_resources import produce_resource_audit
    from robo_minerador.validation_evaluation import evaluate_resources
    text = "import {get} from 'node:https'; get('https://api.github.com/repos');"
    recipe, execution = local_fixture({"unrelated.ts": text})
    add_observed(execution, ["unrelated.ts"])
    execution["observation"]["commands"][0]["report"]["resource_effects"] = [{"path": "unrelated.ts",
        "sha256": hashlib.sha256(text.encode()).hexdigest(), "api": "node:https.get", "url": "https://api.github.com/repos",
        "line": 1, "origin": "native_get", "suite_id": "suite"}]
    execution["resource_evidence"] = produce_resource_audit(execution["verified"], recipe, execution["observation"], execution["environment"])
    assert evaluate_resources(recipe, execution["verified"], execution["resource_evidence"], execution["observation"], execution["environment"])["status"] == "rejected"
    assert verdict(recipe, execution)[0]["status"] == "approved"


def test_missing_loader_version_and_hash_cannot_emit_verified_binding():
    """Ausência simultânea não pode ser aceita por igualdade None == None."""
    recipe, execution = local_fixture()
    loader = execution["environment"]["resource_bindings"]["vite"][0]
    loader.pop("version")
    loader.pop("tree_sha256")
    execution["observation"]["installations"][0]["tools"].pop("vite")
    result, audit = verdict(recipe, execution)
    assert audit["bindings"]["vite_verified"] is False
    assert result["status"] == "not_evaluated"


@pytest.mark.parametrize("root_gap", ["missing", "shared_setup"])
def test_root_configuration_cannot_hide_shared_global_effects(root_gap):
    """Projeto filho isolado não comprova por si ausência de hooks da configuração raiz."""
    recipe, execution = local_fixture()
    suite = execution["observation"]["commands"][0]["report"]["suites"][0]
    if root_gap == "missing":
        suite.pop("resource_root_configuration")
    else:
        suite["resource_root_configuration"] = deepcopy(suite["resource_root_configuration"])
        suite["resource_root_configuration"]["global_setup"] = ["unmodeled-root-hook.ts"]
    result, audit = verdict(recipe, execution)
    assert result["status"] == "not_evaluated", audit


def add_observed(execution, paths):
    """Liga módulos controlados ao mesmo comando/suíte por hash original."""
    texts = {file["path"]: file["content"] for file in execution["verified"]["context"]["files"]}
    modules = execution["observation"]["commands"][0]["report"]["resource_modules"]
    for path in paths:
        modules.append({"path": path, "sha256": hashlib.sha256(texts[path].encode()).hexdigest(),
                        "origin": "vite_transform", "suite_id": "suite"})


def refresh_config(execution):
    """Atualiza somente a captura da configuração da fixture, preservando seus bytes originais."""
    text = next(f["content"] for f in execution["verified"]["context"]["files"] if f["path"] == "vitest.config.ts")
    facts = execution["observation"]["commands"][0]["report"]["suites"][0]["resource_contexts"][0]
    facts["config_sources"] = [{"path": "vitest.config.ts", "sha256": hashlib.sha256(text.encode()).hexdigest()}]
    for module in execution["observation"]["commands"][0]["report"]["resource_modules"]:
        if module["path"] == "vitest.config.ts":
            module["sha256"] = hashlib.sha256(text.encode()).hexdigest()


def test_unknown_statement_in_same_associated_module_stays_pending():
    """Não se presume isolamento entre callbacks por serem declarações diferentes."""
    recipe, execution = local_fixture()
    file = next(f for f in execution["verified"]["context"]["files"] if f["path"] == "a.test.ts")
    file["content"] += "\nimport {vi} from 'vitest'; vi.stubGlobal('fetch',()=>42);\n"
    bind_resource_snapshot(execution["verified"])
    execution["resource_evidence"]["sources"] = deepcopy(execution["verified"]["context"]["files"])
    result, _ = verdict(recipe, execution)
    assert result["status"] == "not_evaluated"


def test_opaque_config_does_not_invent_loader_hash_or_setup_mismatch():
    """Configuração opaca não invalida bytes observados nem comprova ausência de setups."""
    recipe, execution = local_fixture(config="import plugin from 'opaque-plugin'; export default {plugins:[plugin()]};")
    refresh_config(execution)
    result, audit = verdict(recipe, execution)
    assert result["status"] == "not_evaluated"
    assert any("sem derivação" in item for item in audit["limitations"])
    assert not any("Fonte do loader sem vínculo" in item or "Setups/hooks efetivos divergem" in item
                   or "sem cobertura de carregamento" in item for item in audit["limitations"])


def test_new_method_does_not_accept_old_method_signature():
    """Mudança de versão/hash não pode recertificar auditoria antiga como atual."""
    recipe, execution = local_fixture()
    bundle = produce_local(recipe, execution)
    bundle["resource_local_method"]["version"] = 1
    assert verdict(recipe, execution, bundle)[0]["status"] == "not_evaluated"


def test_snapshot_json_read_closes_original_bytes_and_refuses_tampering():
    """Dado lido por filesystem precisa pertencer ao fechamento e ao snapshot, sem inventar load JS."""
    recipe, execution = local_fixture({
        "setup.ts": "import {readFileSync} from 'node:fs'; const values=JSON.parse(readFileSync(new URL('./data.json',import.meta.url),'utf8'));",
        "data.json": '{"values":[1,2]}',
    }, "export default {test:{setupFiles:['./setup.ts']}};")
    execution["observation"]["commands"][0]["report"]["suites"][0]["resource_contexts"][0]["setup_files"] = ["setup.ts"]
    add_observed(execution, ["setup.ts"])
    refresh_config(execution)
    bundle = produce_local(recipe, execution)
    result, audit = verdict(recipe, execution, bundle)
    assert result["status"] == "approved", audit
    assert "data.json" in audit["closure_paths"]
    next(file for file in execution["resource_evidence"]["sources"] if file["path"] == "data.json")["content"] = '{"values":[9]}'
    assert verdict(recipe, execution, bundle)[0]["status"] == "not_evaluated"


@pytest.mark.parametrize("observed", [True, False])
def test_remapped_import_requires_original_target_loading(observed):
    """Encontrar uma fonte TS não substitui a prova de carregamento original por hash."""
    recipe, execution = local_fixture({"setup.ts": "import {value} from './value.js'; const result=value;",
                                       "value.ts": "export const value=42;"},
                                      "export default {test:{setupFiles:['./setup.ts']}};")
    execution["observation"]["commands"][0]["report"]["suites"][0]["resource_contexts"][0]["setup_files"] = ["setup.ts"]
    add_observed(execution, ["setup.ts"] + (["value.ts"] if observed else []))
    refresh_config(execution)
    result, audit = verdict(recipe, execution)
    assert result["status"] == ("approved" if observed else "not_evaluated"), audit
    assert "value.ts" in audit["closure_paths"]


def test_resolver_hash_is_part_of_portable_method_contract():
    """Auxiliar transitivo alterado não conserva a assinatura do método confiável."""
    recipe, execution = local_fixture()
    bundle = produce_local(recipe, execution)
    bundle["resource_local_method"]["base_method"]["resolver_sha256"] = "0" * 64
    assert verdict(recipe, execution, bundle)[0]["status"] == "not_evaluated"


@pytest.mark.parametrize("relevant", [False, True])
def test_missing_source_blocks_only_when_original_graph_can_reach_it(relevant):
    """O manifesto completo permite distinguir omissão alheia de setup necessário sem texto."""
    config = "export default {test:{setupFiles:['./missing.ts']}};" if relevant else None
    recipe, execution = local_fixture({"missing.ts": "eval(process.env.UNKNOWN);"}, config)
    execution["resource_evidence"]["sources"] = [file for file in execution["resource_evidence"]["sources"] if file["path"] != "missing.ts"]
    execution["verified"]["context"]["analysis_complete"] = False
    if relevant:
        execution["observation"]["commands"][0]["report"]["suites"][0]["resource_contexts"][0]["setup_files"] = ["missing.ts"]
        refresh_config(execution)
    result, _ = verdict(recipe, execution)
    assert result["status"] == ("not_evaluated" if relevant else "approved")


@pytest.mark.parametrize("tamper", ["closure", "setup", "import", "model", "loader", "isolation", "effect"])
def test_local_replay_refuses_reduced_or_forged_scope(tamper):
    """Reduções de fechamento, configuração, implementação e efeitos são rederivadas."""
    recipe, execution = local_fixture({"setup.ts": "import './transitive';", "transitive.ts": "export const value=42;"},
        "export default {test:{setupFiles:['./setup.ts']}};")
    facts = execution["observation"]["commands"][0]["report"]["suites"][0]["resource_contexts"][0]
    facts["setup_files"] = ["setup.ts"]
    add_observed(execution, ["setup.ts", "transitive.ts"])
    refresh_config(execution)
    bundle = produce_local(recipe, execution)
    audit = next(iter(bundle["instance_resource_evidence"].values()))
    assert verdict(recipe, execution, bundle)[0]["status"] == "approved"
    if tamper == "closure":
        audit["closure_paths"].remove("transitive.ts")
    elif tamper == "setup":
        facts["setup_files"] = []
    elif tamper == "import":
        next(f for f in execution["resource_evidence"]["sources"] if f["path"] == "setup.ts")["content"] = "export const value=42;"
    elif tamper == "model":
        bundle["resource_local_method"]["version"] = 999
    elif tamper == "loader":
        execution["environment"]["resource_bindings"]["vite"][0]["tree_sha256"] = "0" * 64
    elif tamper == "isolation":
        facts["isolate"] = False
    else:
        execution["observation"]["commands"][0]["report"]["resource_effects"] = [{"path": "setup.ts", "sha256": "0" * 64,
            "api": "node:https.get", "url": "https://api.github.com/repos", "line": 1, "origin": "native_get", "suite_id": "suite"}]
    assert verdict(recipe, execution, bundle)[0]["status"] == "not_evaluated"


@pytest.mark.parametrize("tamper", ["closure_paths", "edges", "config_sources"])
def test_portable_publication_refuses_consistent_forged_local_audit(monkeypatch, tmp_path, tamper):
    """Atualizar hashes e cópias do critério não dispensa a rederivação do fechamento."""
    from test_validation import published_fixture
    from robo_minerador.validation_format import validate_output_records
    from robo_minerador.validation_recipe import _hash

    def setup(execution):
        """Acrescenta setup real às fontes e à captura da fixture controlada."""
        files = execution["verified"]["context"]["files"]
        files.append({"path": "setup.ts", "content": "import {beforeAll,vi} from 'vitest'; beforeAll(()=>{const fake=vi.fn(()=>42);});"})
        next(f for f in files if f["path"] == "vitest.config.ts")["content"] = "export default {test:{setupFiles:['./setup.ts']}};"
        bind_resource_snapshot(execution["verified"])
        execution["resource_evidence"]["sources"] = deepcopy(files)
        fact = execution["observation"]["commands"][0]["report"]["suites"][0]["resource_contexts"][0]
        fact["setup_files"] = ["setup.ts"]
        refresh_config(execution)
        add_observed(execution, ["setup.ts"])

    rows, _, _ = published_fixture(monkeypatch, tmp_path, counts=(4,), mutate=setup)
    assert validate_output_records(rows)["instance_decisions"]["approved"] == 1
    audit = rows[1]["instance_resource_evidence"][rows[2]["instance_id"]]
    if tamper == "closure_paths":
        audit["closure_paths"].remove("setup.ts")
    elif tamper == "edges":
        audit["edges"] = []
    else:
        audit["configuration_contexts"][0]["configuration"]["config_sources"] = []
    rows[2]["criteria"]["no_external_services"]["evidence"]["audit_sha256"] = _hash(audit)
    with pytest.raises(ValueError, match="Recursos locais"):
        validate_output_records(rows)
