"""Protege o produtor real contra aprovações por classificação ou cobertura inventadas."""

from copy import deepcopy
import hashlib
import os

import pytest

from test_validation import controlled_fixture, bind_resource_snapshot
from robo_minerador.validation_recipe import resolve_recipe
from robo_minerador.validation_evaluation import evaluate_resources


def resource_fixture(source, extra=None):
    """Substitui somente textos controlados; não executa qualquer serviço ou candidato."""
    group, catalog, _, execution = controlled_fixture((4,))
    recipe = resolve_recipe(group["repository"], catalog)["recipe"]
    files = [{"path": "a.test.ts", "content": source},
             {"path": "vitest.config.ts", "content": "export default {test:{environment:'node'}};"},
             {"path": "package.json", "content": '{"scripts":{"test":"vitest run"}}'}]
    files.extend({"path": path, "content": text} for path, text in (extra or {}).items())
    execution["verified"]["context"]["files"] = files
    bind_resource_snapshot(execution["verified"])
    report = execution["observation"]["commands"][0]["report"]
    report["transforms"] = []
    report["resource_modules"] = [{"path": f["path"], "sha256": hashlib.sha256(f["content"].encode()).hexdigest(),
                                    "origin": "vite_transform"} for f in files if f["path"].endswith(".ts")]
    execution["environment"]["resource_bindings"] = {"vitest": [{"root": "/workspace/node_modules/vitest", "version": "4.1.10",
        "tree_sha256": "d3fd24af92fabda62731814693ab1fa47a77c91b1b48a7be343c45bf5213780e"}]}
    return recipe, execution


def produce(recipe, execution):
    """Chama o produtor público sobre fontes sem substituir sua análise."""
    from robo_minerador.validation_resources import produce_resource_audit
    return produce_resource_audit(execution["verified"], recipe, execution["observation"], execution["environment"])


@pytest.mark.parametrize("body,resource", [
    ("const value = {answer:42};", "none"),
    ("import {vi} from 'vitest'; const fake=vi.fn(()=>42);", "mock"),
    ("import {mkdtempSync,writeFileSync} from 'node:fs'; import {tmpdir} from 'node:os';"
     "import {join} from 'node:path'; const dir=mkdtempSync(join(tmpdir(),'fixture-'));"
     "writeFileSync(join(dir,'data.txt'),'fixture');", "temporary_files"),
    ("import {DatabaseSync} from 'node:sqlite'; const db=new DatabaseSync(':memory:'); db.close();", "embedded_database"),
    ("import {createServer} from 'node:http'; const server=createServer((req,res)=>{res.end('fixture');});"
     "server.listen(0,'127.0.0.1'); server.close();", "simulation_server"),
])
def test_producer_proves_modeled_resources(body, resource):
    """Exige derivação estrutural e replay da fonte para cada recurso permitido."""
    recipe, execution = resource_fixture(body)
    audit = produce(recipe, execution)
    result = evaluate_resources(recipe, execution["verified"], audit, execution["observation"], execution["environment"])
    assert result["status"] == "approved", audit
    assert resource in {f["resource"] for item in audit["files"] for f in item["findings"]}


def test_producer_rejects_unconditional_real_remote_service():
    """Import Node original e chamada incondicional observada provam serviço real."""
    recipe, execution = resource_fixture("import {get} from 'node:https'; get('https://api.github.com/repos');")
    execution["observation"]["commands"][0]["report"]["resource_effects"] = [{"path": "a.test.ts",
        "sha256": hashlib.sha256(execution["verified"]["context"]["files"][0]["content"].encode()).hexdigest(),
        "api": "node:https.get", "url": "https://api.github.com/repos", "line": 1, "column": 31, "origin": "native_get"}]
    audit = produce(recipe, execution)
    assert evaluate_resources(recipe, execution["verified"], audit, execution["observation"], execution["environment"])["status"] == "rejected"


def test_loaded_module_does_not_prove_remote_call_reached():
    """Operação que pode lançar antes do get impede alegar serviço efetivo só pelo import."""
    recipe, execution = resource_fixture("import {get} from 'node:https'; get('https://api.github.com/repos');")
    audit = produce(recipe, execution)
    assert not audit["complete"]
    assert evaluate_resources(recipe, execution["verified"], audit, execution["observation"], execution["environment"])["status"] == "not_evaluated"


@pytest.mark.parametrize("source,extra", [
    ("import {connect} from 'unmodeled-sdk'; connect();", {}),
    ("import './hook';", {"hook.ts": "import('./unknown');"}),
    ("const moduleName='node:fs'; import(moduleName);", {}),
    ("import {expect} from 'vitest'; expect(()=>import('unmodeled-sdk')).toBe(null);", {}),
    ("import {readFileSync} from 'node:fs'; readFileSync('/var/run/service.sock');", {}),
    ("export default {plugins:[]};", {}),
])
def test_unknown_effect_or_transitive_import_stays_pending(source, extra):
    """SDK, carregamento dinâmico, caminho opaco e config desconhecida não viram ausência."""
    recipe, execution = resource_fixture(source, extra)
    if source.startswith("export default"):
        execution["verified"]["context"]["files"][1]["content"] = source
    audit = produce(recipe, execution)
    assert not audit["complete"]
    assert evaluate_resources(recipe, execution["verified"], audit, execution["observation"], execution["environment"])["status"] == "not_evaluated"


@pytest.mark.parametrize("change", ["model", "model_boolean", "missing_model", "model_catalog", "source", "findings", "binding", "vitest_tree", "vitest_root", "paths", "unobserved", "incomplete", "manifest", "environment"])
def test_replay_refuses_forged_proof(change):
    """Editar certificado sem rederivação e ligação runtime não mantém aprovação."""
    recipe, execution = resource_fixture("const value=42;")
    audit = deepcopy(produce(recipe, execution))
    if change == "model":
        audit["method"]["version"] = 999
    elif change == "model_boolean":
        audit["method"]["version"] = True
    elif change == "missing_model":
        audit.pop("method")
    elif change == "model_catalog":
        audit["method"]["models"]["rules"]["real_service"] = "permitido"
    elif change == "source":
        audit["sources"][0]["content"] = "fetch('https://real.example');"
    elif change == "findings":
        audit["files"][0]["findings"][0]["resource"] = "simulation_server"
    elif change == "binding":
        execution["environment"]["tools"]["node"] = "0.0.0"
    elif change == "vitest_tree":
        execution["environment"]["resource_bindings"]["vitest"][0]["tree_sha256"] = "0" * 64
    elif change == "vitest_root":
        execution["environment"]["resource_bindings"]["vitest"][0]["root"] = "/workspace/other/vitest"
    elif change == "paths":
        audit["executed_paths"] = []
    elif change == "unobserved":
        execution["observation"]["commands"][0]["report"]["resource_modules"].append(
            {"path": "generated.js", "sha256": "0" * 64, "origin": "node_loader"})
    elif change == "manifest":
        execution["verified"]["resource_manifest"]["entries"] = []
    elif change == "environment":
        recipe["environment"] = {"NODE_OPTIONS": "--require ./hidden.js"}
    else:
        execution["observation"]["commands"][0]["evidence"]["complete"] = False
    assert evaluate_resources(recipe, execution["verified"], audit, execution["observation"], execution["environment"])["status"] == "not_evaluated"


def test_opaque_module_prevents_isolated_negative_and_scope_is_not_package_list():
    """Uma substituição opaca impede atribuir chamada aparente a serviço real."""
    recipe, execution = resource_fixture("import {get} from 'node:https'; get('https://api.github.com/repos');",
                                         {"hook.ts": "import {vi} from 'vitest'; vi.mock('node:https');"})
    audit = produce(recipe, execution)
    assert not audit["complete"]
    assert evaluate_resources(recipe, execution["verified"], audit, execution["observation"], execution["environment"])["status"] == "not_evaluated"


def test_original_nonprojected_configuration_prevents_approval():
    """Inventário original com npmrc omitido não é suprido por projeção completa."""
    recipe, execution = resource_fixture("const value=42;")
    verified = execution["verified"]
    verified["context"]["files"].append({"path": ".npmrc", "content": "script-shell=/unmodeled/wrapper\n"})
    bind_resource_snapshot(verified)
    verified["context"]["files"].pop()
    audit = produce(recipe, execution)
    assert not audit["complete"]
    assert any("omitidos" in limitation for limitation in audit["limitations"])


def test_configuration_hooks_and_transitive_sources_have_structural_proof():
    """Config original, hook e import transitivo entram no fechamento sem selecionar candidata."""
    recipe, execution = resource_fixture("import {value} from './helper'; const result=value+1;",
        {"helper.ts": "export const value=42;", "setup.ts": "import {beforeAll,vi} from 'vitest'; beforeAll(()=>{const fake=vi.fn(()=>42);});"})
    config = execution["verified"]["context"]["files"][1]
    config["content"] = "export default {test:{environment:'node',setupFiles:['./setup.ts']}};"
    bind_resource_snapshot(execution["verified"])
    for module in execution["observation"]["commands"][0]["report"]["resource_modules"]:
        if module["path"] == config["path"]:
            module["sha256"] = hashlib.sha256(config["content"].encode()).hexdigest()
    audit = produce(recipe, execution)
    assert evaluate_resources(recipe, execution["verified"], audit, execution["observation"], execution["environment"])["status"] == "approved", audit
    assert any(item.get("configuration") == "setupFiles" and item["path"] == "setup.ts"
               for file in audit["files"] for item in file["imports"])


def test_inert_original_file_requires_closed_graph_and_manifest_binding():
    """Licença não projetada é excluída por regra explícita; importá-la invalida a exclusão."""
    recipe, execution = resource_fixture("const value=42;")
    verified = execution["verified"]
    verified["context"]["files"].append({"path": "LICENSE", "content": "MIT license fixture\n"})
    bind_resource_snapshot(verified)
    verified["context"]["files"].pop()
    audit = produce(recipe, execution)
    assert audit["excluded_files"][0]["path"] == "LICENSE"
    assert evaluate_resources(recipe, verified, audit, execution["observation"], execution["environment"])["status"] == "approved", audit
    verified["context"]["files"][0]["content"] = "import './LICENSE';"
    audit = produce(recipe, execution)
    assert not audit["complete"]


def test_portable_reader_rederives_model_instead_of_trusting_copied_decision(monkeypatch, tmp_path):
    """Veredito e referências coerentemente adulterados não substituem o replay AST."""
    from test_validation import published_fixture
    from robo_minerador.validation_evaluation import resource_audit_reference
    from robo_minerador.validation_format import validate_output_records
    rows, _, _ = published_fixture(monkeypatch, tmp_path)
    audit = rows[1]["resource_evidence"]
    audit["method"]["version"] = 999
    for record in rows:
        if record["record_type"] in {"repository", "instance"}:
            record["criteria"]["no_external_services"]["evidence"] = resource_audit_reference(audit)
    with pytest.raises(ValueError, match="recursos"):
        validate_output_records(rows)


def test_orchestrator_uses_docker_producer_and_publishes_replay(monkeypatch, tmp_path):
    """Usa método real Docker/AST e escritor; substitui só transporte administrativo."""
    from test_validation import FixtureExecutor, published_fixture
    from robo_minerador.validation_docker import DockerExecutor
    from robo_minerador.validation_format import validate_output_records
    import json

    def observer(executor, workspace, verified, recipe, observation):
        """Entrega apenas bytes do inventário controlado ao método real do executor."""
        workspace.volume = "fixture-volume"
        workspace.manifest = []
        executor._trusted = lambda *args, **kwargs: {"inventory": json.dumps(
            executor.execution["environment"]["resource_bindings"]["vitest"] + executor.execution["environment"]["resource_bindings"]["vite"]).encode()}
        executor.read_log = lambda event, stream="stdout": event.get("inventory", b"ok")
        return DockerExecutor.observe_resources(executor, workspace, verified, recipe, observation)

    monkeypatch.setattr(FixtureExecutor, "observe_resources", observer)
    rows, _, _ = published_fixture(monkeypatch, tmp_path)
    assert validate_output_records(rows)["status"] == "completed"
    assert rows[1]["resource_evidence"]["method"]["name"] == "closed_unit_effects"
    assert rows[1]["criteria"]["no_external_services"]["status"] == "approved"


@pytest.mark.skipif(os.environ.get("ROBO_TEST_DOCKER") != "1", reason="Fixture Docker de recursos exige opt-in.")
@pytest.mark.parametrize("local_unrelated", [False, True])
def test_real_resource_producer_with_original_snapshot_and_vitest(tmp_path, local_unrelated):
    """Uma passagem de fixture real comprova captura de módulos e recursos temporários."""
    import base64
    import json
    from robo_minerador.validation_docker import DockerExecutor
    from robo_minerador.validation_recipe import load_recipes
    from robo_minerador.validation_workspace import prepare_workspace
    from robo_minerador.validation_instrument import configure_instrument
    from robo_minerador.extraction_context import load_repository_context
    from test_extraction_context import make_local_snapshot, published_case

    recipe = deepcopy(next(iter(load_recipes()["recipes"].values())))
    manifest = {"name": "trusted-resource-fixture", "version": "1.0.0", "type": "module", "private": True,
                "scripts": {"test": "vitest run"}, "devDependencies": {"vitest": "4.1.10", "vite": "7.3.6"}}
    executor = DockerExecutor()
    try:
        executor.preflight()
        script = r"""
const fs=require('node:fs'),cp=require('node:child_process');
fs.mkdirSync('/tmp/fixture');process.chdir('/tmp/fixture');fs.writeFileSync('package.json',process.argv[1]);
const result=cp.spawnSync('npm',['install','--package-lock-only','--legacy-peer-deps','--ignore-scripts','--no-audit','--no-fund'],{encoding:'utf8'});
if(result.status!==0)throw Error(result.stderr);console.log(fs.readFileSync('package-lock.json').toString('base64'));
"""
        event = executor._trusted(recipe, ["node", "-e", script, json.dumps(manifest)], network="bridge")
        lock = base64.b64decode(executor.read_log(event).strip())
        contents = {"package.json": json.dumps(manifest), "package-lock.json": lock, ".npmrc": "legacy-peer-deps=true\n",
                    "target.ts": "/** Soma uma unidade ao argumento para esta fixture. */\nexport function compute(value:number){return value+1;}\n",
                    "target.test.ts": "import {test,expect,vi} from 'vitest';import {compute} from './target';\n"
                    "import {mkdtempSync,writeFileSync,rmSync} from 'node:fs';import {tmpdir} from 'node:os';import {join} from 'node:path';\n"
                    "test.each([1,2,3,4])('original',/** Usa exclusivamente diretório temporário novo. */(value)=>{\n"
                    "const dir=mkdtempSync(join(tmpdir(),'resource-fixture-'));\nwriteFileSync(join(dir,'data.txt'),'fixture');\n"
                    "rmSync(dir,{recursive:true,force:true});\nconst fake=vi.fn(()=>42);\nexpect(fake()).toBe(42);\nexpect(compute(value)).toBe(value+1);\n});\n"}
        if local_unrelated:
            contents["vitest.config.ts"] = "import {defineConfig} from 'vitest/config'; export default defineConfig({test:{environment:'node',pool:'forks',isolate:true,setupFiles:['./setup.ts']}});\n"
            contents["setup.ts"] = "import {beforeEach,vi} from 'vitest'; beforeEach(()=>{const fake=vi.fn(()=>42);});\n"
            contents["unrelated.test.ts"] = "import {test} from 'vitest'; test('unrelated',()=>{throw new Error('unrelated failure');});\n"
            contents["unrelated-service.ts"] = "import {get} from 'node:https'; get('https://api.github.com/repos');\n"
        state = make_local_snapshot(tmp_path, contents)
        repository, reasons, units = published_case(state)
        approved = [unit for unit in units if unit["decision"] == "approved"]
        assert len(approved) == 1, units
        verified = load_repository_context(approved, repository, reasons, state.store)
        recipe.update(repository_id=1, repository_url=state.candidate["repository_url"], commit_sha=state.candidate["commit_sha"])
        recipe["lockfiles"] = [{"path": "package-lock.json", "sha256": hashlib.sha256(lock).hexdigest(), "manager": "npm"}]
        recipe["phases"] = {"installation": [{"argv": ["npm", "ci"], "cwd": "."}], "preparation": [],
                            "unit_suite": [{"argv": ["npm", "test"], "cwd": "."}]}
        recipe["unit_scope"]["evidence"] = ["package.json"]
        recipe["unit_scope"]["packages"] = [{"path": ".", "name": manifest["name"], "scope": "Escopo integral da fixture controlada."}]
        workspace = prepare_workspace(state.store, verified, recipe, executor)
        installation = executor.run_phase(workspace, "installation", recipe)
        assert installation["status"] == "completed"
        preparation = executor.run_phase(workspace, "preparation", recipe)
        session = configure_instrument(workspace, verified, recipe, executor)
        phase = executor.run_phase(workspace, "unit_suite", recipe)
        observation = session.collect(executor, phase)
        if not observation["complete"]:
            command = observation["commands"][0]
            print(json.dumps({"limitations": command.get("limitations"), "census": command.get("census"),
                              "report_limitations": (command.get("report") or {}).get("limitations"),
                              "stdout": executor.read_log(phase["commands"][0]).decode(errors="replace"),
                              "stderr": executor.read_log(phase["commands"][0], "stderr").decode(errors="replace")}, ensure_ascii=True))
        assert observation["complete"], observation
        bundle = executor.observe_resources(workspace, verified, recipe, observation)
        audit = bundle["resource_evidence"]
        result = evaluate_resources(recipe, verified, audit, observation, workspace.environment)
        assert result["status"] == ("not_evaluated" if local_unrelated else "approved"), audit
        from robo_minerador.validation_resources_local import evaluate_instance_resources
        unit = next(iter(verified["instances"].values()))
        from robo_minerador.validation_evaluation import resource_universe
        local = evaluate_instance_resources(unit, recipe, resource_universe(verified), audit, bundle["resource_local_method"],
            bundle["instance_resource_evidence"][unit["instance_id"]], observation, workspace.environment)
        if local["status"] != "approved":
            print(json.dumps({"local": local, "audit": bundle["instance_resource_evidence"][unit["instance_id"]]}, ensure_ascii=True))
        assert local["status"] == "approved", bundle
        local_audit = bundle["instance_resource_evidence"][unit["instance_id"]]
        assert "unrelated.test.ts" not in local_audit["closure_paths"] and "unrelated-service.ts" not in local_audit["closure_paths"]
        if local_unrelated:
            assert phase["status"] == "failed" and observation["commands"][0]["report"]["original_exit"]["code"] == 1
            assert {"setup.ts", "vitest.config.ts"} <= set(local_audit["closure_paths"])
        assert {"target.ts", "target.test.ts"} <= set(audit["executed_paths"])
        assert workspace.environment["resource_bindings"]["vitest"][0]["tree_sha256"] == audit["method"]["models"]["vitest_trees"]["4.1.10"]
        from robo_minerador.validation import _incorporate, _record
        from robo_minerador.validation_evaluation import evaluate_repository, operational_issues
        from robo_minerador.validation_format import validate_output_records, decision_counts, run_result
        from robo_minerador.validation_recipe import _hash
        from robo_minerador.jsonl import write_jsonl
        from robo_minerador.validation_evidence import decode_events
        resolution = {"recipe": recipe, "recipe_sha256": _hash(recipe), "catalog_sha256": "d" * 64, "resolution_status": "resolved"}
        execution = {"verified": verified, "environment": workspace.environment, "observation": observation, **bundle,
            "phases": {name: _incorporate(value, executor) for name, value in
                       (("installation", installation), ("preparation", preparation), ("unit_suite", phase))}}
        group = {"repository": repository, "instances": approved}
        repo_record, instance_records = evaluate_repository(group, resolution, execution)
        assert instance_records[0]["decision"] == "approved", instance_records
        assert instance_records[0]["test_cases"]["valid_count"] == 4
        if local_unrelated:
            assert repo_record["decision"] == "rejected"
        source = {"path": "controlled-resource-fixture", "sha256": "e" * 64, "schema_version": 3, "status": "completed",
            "input_count": 1, "repository_count": 1, "eligible_count": 1, "eligible_repository_count": 1,
            "decisions": {"approved": 1, "rejected": 0, "not_evaluated": 0}}
        records = [_record("run_started", source=source, policy={"suite_passes": 1, "minimum_valid_cases": 4,
            "no_corrective_retries": True, "instance_execution_scope": "associated_declarations_and_module_hooks",
            "resource_gate_scope": "associated_closure_v1"}, limits=[{"repository_id": repository["repository_id"], "limits": recipe["limits"]}],
            miner_version="0.1.0", instrument_protocol_version=1)]
        records.extend(_record(item["record_type"], **{key: value for key, value in item.items() if key != "record_type"})
                       for item in [repo_record, *instance_records])
        issues = operational_issues(repo_record)
        if issues:
            records.append(_record("diagnostic", repository_id=repository["repository_id"], code="FIXTURE_OPERATIONAL_ISSUES",
                operational=True, message="Impedimentos da fixture controlada.", evidence={"issues": issues}))
        records.append(_record("run_finished", source_sha256=source["sha256"], repository_count=1, instance_count=1,
            repository_decisions=decision_counts([repo_record]), instance_decisions=decision_counts(instance_records),
            operational_errors=int(bool(issues)), status=run_result([repo_record], instance_records, int(bool(issues)))))
        output = tmp_path / "local-resource-fixture.jsonl"
        write_jsonl(output, records, ensure_ascii=True, compact=True)
        assert validate_output_records(decode_events(output.read_bytes()))["instance_decisions"]["approved"] == 1
        print(json.dumps({"fixture": "resource-audit", "unrelated_failure": local_unrelated, "global_resources": result["status"],
                          "local_resources": local["status"], "complete": local_audit["complete"], "closure": local_audit["closure_paths"],
                          "cases": len(observation["commands"][0]["evidence"]["cases"]), "valid_cases": instance_records[0]["test_cases"]["valid_count"],
                          "instance_decision": instance_records[0]["decision"], "portable_roundtrip": True}))
    finally:
        executor.close()
    assert not executor.cleanup_errors
