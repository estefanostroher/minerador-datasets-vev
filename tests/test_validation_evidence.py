"""Protege associação e completude contra trace corrompido ou falsamente vazio."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import os
import hashlib

import pytest

from robo_minerador.validation_evidence import case_identity, decode_events, validate_events


@pytest.mark.parametrize("count,complete,status", [(3, True, "rejected"), (4, True, "approved"),
                                                    (3, False, "not_evaluated"), (4, False, "approved")])
def test_scientific_count_requires_cases_and_independent_completeness(count, complete, status):
    """Quatro testemunhas e fechamento local bastam sem exatidão global; chamadas não duplicam caso."""
    from test_validation import controlled_fixture
    from robo_minerador.validation_evidence import evaluate_repository
    from robo_minerador.validation_recipe import resolve_recipe
    group, catalog, _, execution = controlled_fixture((count,))
    execution["observation"]["complete"] = complete
    repository, instances = evaluate_repository(group, resolve_recipe(group["repository"], catalog), execution)
    assert instances[0]["decision"] == status
    assert instances[0]["test_cases"]["valid_count"] == count
    assert len(instances[0]["case_evidence"]) == count
    assert instances[0]["criteria"]["minimum_valid_cases"]["status"] == ("approved" if count >= 4 else status)


@pytest.mark.parametrize("cause,expected", [("retry", "rejected"), ("hook", "rejected"), ("lock", "rejected"),
                                           ("registry", "not_evaluated"), ("oom", "not_evaluated"),
                                           ("opaque", "not_evaluated"), ("ambiguous", "not_evaluated")])
def test_evaluation_does_not_confuse_candidate_failure_with_infrastructure(cause, expected):
    """Preserva falhas identificadas e não usa código bruto como causa científica."""
    from test_validation import controlled_fixture
    from robo_minerador.validation_evidence import evaluate_repository
    from robo_minerador.validation_format import encode_bytes
    from robo_minerador.validation_recipe import resolve_recipe
    group, catalog, _, execution = controlled_fixture((4,))
    if cause == "retry":
        evidence = execution["observation"]["commands"][0]["evidence"]
        case = next(iter(evidence["cases"].values()))
        case["finished"]["attempts"].insert(0, {"index": 0, "retry": 0, "repeat": 0, "status": "failed", "expected_failure": False})
        case["finished"]["attempts"][1].update(index=1, retry=1)
    elif cause == "hook":
        execution["observation"]["commands"][0]["report"]["suites"] = [{"module_results": [{"state": "failed", "errors": [{"message": "beforeAll timed out"}]}]}]
    else:
        execution["observation"]["complete"] = False
        phase = execution["phases"]["installation" if cause in {"lock", "registry"} else "unit_suite"]
        phase["status"] = "failed"
        phase["commands"][0]["exitcode"] = 86 if cause == "opaque" else 1
        if cause in {"lock", "registry"}:
            phase["commands"][0]["stderr"] = encode_bytes(b"npm error EUSAGE package lock out of sync" if cause == "lock" else b"npm error EAI_AGAIN registry unavailable")
        if cause == "oom":
            phase["commands"][0]["oom"] = True
    repository, instances = evaluate_repository(group, resolve_recipe(group["repository"], catalog), execution)
    assert repository["decision"] == expected
    # Flags exclusivamente globais não invalidam os streams locais fechados
    # desta fixture. Uma interrupção do worker associado é coberta em test_validation_local.
    local_expected = "approved" if cause in {"oom", "opaque", "ambiguous"} else "not_evaluated" if cause == "hook" else expected
    assert instances[0]["decision"] == local_expected
    if cause == "hook":
        assert instances[0]["criteria"]["associated_tests_execution"]["status"] == "not_evaluated"
    if cause == "lock":
        assert instances[0]["criteria"]["execution_prerequisites"]["status"] == "rejected"
    assert instances[0]["test_cases"]["valid_count"] is not None


@pytest.mark.parametrize("resource,role,complete", [("real_service", "effective_dependency", True),
    ("real_service", "not_used", True), ("simulation_server", "effective_dependency", True),
    ("embedded_database", "effective_dependency", True), ("mock", "effective_dependency", True),
    ("unknown", "unknown", True), ("none", "not_used", False)])
def test_resource_audit_requires_effective_scope_and_verified_sources(resource, role, complete):
    """Classificação editada sem derivação não permite aprovação nem rejeição científica."""
    from test_validation import controlled_fixture
    from robo_minerador.validation_evidence import evaluate_repository
    from robo_minerador.validation_recipe import resolve_recipe
    group, catalog, _, execution = controlled_fixture((4,))
    execution["resource_evidence"]["complete"] = complete
    execution["resource_evidence"]["files"][0]["findings"][0].update(resource=resource, role=role)
    repository, instances = evaluate_repository(group, resolve_recipe(group["repository"], catalog), execution)
    assert repository["criteria"]["no_external_services"]["status"] == "not_evaluated"
    assert instances[0]["decision"] == "approved"
    # A classificação global não é herdada; adulterar a derivação LOCAL,
    # mesmo com fontes intactas e flags coerentes, deve impedir aprovação.
    local = execution["instance_resource_evidence"][instances[0]["instance_id"]]
    local["complete"] = complete
    local["files"][0]["findings"][0].update(resource=resource, role=role)
    _, instances = evaluate_repository(group, resolve_recipe(group["repository"], catalog), execution)
    assert instances[0]["decision"] == "not_evaluated"
    execution["resource_evidence"]["files"][0]["sha256"] = "0" * 64
    repository, _ = evaluate_repository(group, resolve_recipe(group["repository"], catalog), execution)
    assert repository["criteria"]["no_external_services"]["status"] == "not_evaluated"


def test_coverage_maps_only_verified_original_function_and_never_associated_percentage():
    """Provider global com mapa original pode informar função, mas não testes associados."""
    from test_validation import controlled_fixture, attach_function_coverage
    from robo_minerador.validation_evidence import evaluate_repository
    from robo_minerador.validation_recipe import resolve_recipe
    group, catalog, _, execution = controlled_fixture((4,))
    provider = attach_function_coverage(execution)
    _, instances = evaluate_repository(group, resolve_recipe(group["repository"], catalog), execution)
    coverage = instances[0]["coverage"]
    assert coverage["associated_tests"]["status"] == "unavailable" and coverage["associated_tests"]["lines"] is None
    assert coverage["full_unit_suite"]["lines"]["percentage"] == 100
    assert coverage["full_unit_suite"]["branches"] == {"status": "not_applicable", "covered": 0, "total": 0, "percentage": None}
    provider["s"]["0"] = 0
    _, instances = evaluate_repository(group, resolve_recipe(group["repository"], catalog), execution)
    assert instances[0]["coverage"]["full_unit_suite"]["lines"]["percentage"] == 0
    provider["inputSourceMap"]["sourcesContent"] = ["conteúdo alterado"]
    _, instances = evaluate_repository(group, resolve_recipe(group["repository"], catalog), execution)
    assert instances[0]["coverage"]["full_unit_suite"]["lines"] is None


@pytest.mark.parametrize("excluded", ["skipped", "todo", "expected_failure", "call_not_reached"])
def test_nonpositive_or_unreached_case_never_completes_four_witnesses(excluded):
    """Skip, todo, falha esperada e mock sem entrada original não viram casos positivos."""
    from test_validation import controlled_fixture, reseal_fixture
    from robo_minerador.validation_evidence import evaluate_repository
    from robo_minerador.validation_recipe import resolve_recipe
    group, catalog, _, execution = controlled_fixture((4,))
    evidence = execution["observation"]["commands"][0]["evidence"]
    case_id, case = next(iter(evidence["cases"].items()))
    if excluded in {"skipped", "todo"}:
        case["finished"].update(status=excluded, attempts=[])
        evidence["calls"] = [c for c in evidence["calls"] if c["direct"]["case_id"] != case_id]
    elif excluded == "expected_failure":
        case["started"]["case"]["modifiers"] = ["fails"]
        case["finished"]["attempts"][0].update(status="failed", expected_failure=True)
    else:
        for call in evidence["calls"]:
            if call["direct"]["case_id"] == case_id:
                call["entered"] = None
    if excluded != "expected_failure":
        reseal_fixture(execution)
        task = execution["observation"]["commands"][0]["report"]["suites"][0]["tasks"][0]
        if excluded in {"skipped", "todo"}:
            task["result"]["state"] = "skipped"
    repository, instances = evaluate_repository(group, resolve_recipe(group["repository"], catalog), execution)
    assert repository["decision"] == "approved"
    if excluded == "expected_failure":
        # Trocar modificador sem identidade/declaração correspondente corrompe a prova.
        assert instances[0]["decision"] == "not_evaluated"
        assert not instances[0]["case_evidence"]
        return
    assert instances[0]["decision"] == "rejected" and instances[0]["test_cases"]["valid_count"] == 3
    assert any(c["status"] == excluded for a in instances[0]["test_cases"]["associations"] for c in a["cases"])


def test_raw_report_rejected_by_t3_cannot_reject_candidate_or_prove_coverage():
    """Material bruto sem protocolo validado não recebe autoridade científica."""
    from test_validation import controlled_fixture, attach_function_coverage
    from robo_minerador.validation_evidence import evaluate_repository
    from robo_minerador.validation_recipe import resolve_recipe
    group, catalog, _, execution = controlled_fixture((4,))
    attach_function_coverage(execution)
    _, positive = evaluate_repository(group, resolve_recipe(group["repository"], catalog), execution)
    assert positive[0]["coverage"]["full_unit_suite"]["lines"]["percentage"] == 100
    command = execution["observation"]["commands"][0]
    command.pop("evidence")
    command.update(complete=False, limitations=["Identidade adulterada no relatório."])
    command["report"]["suites"][0]["errors"] = [{"message": "falha não confiável"}]
    execution["observation"]["complete"] = False
    repository, instances = evaluate_repository(group, resolve_recipe(group["repository"], catalog), execution)
    assert repository["decision"] == "not_evaluated"
    assert instances[0]["test_cases"]["valid_count"] is None
    assert instances[0]["coverage"]["full_unit_suite"]["status"] == "unavailable"
    assert instances[0]["coverage"]["full_unit_suite"]["lines"] is None
    assert "protocolo" in instances[0]["coverage"]["full_unit_suite"]["reason"]


def trace():
    """Constrói dois callsites do mesmo caso com retry verde e falha preservada."""
    identity = {"protocol_version": 1, "session_id": "session", "repository_id": 1,
                "commit_sha": "a" * 40, "instrument_id": "instrument"}
    expected = {**{key: identity[key] for key in ("session_id", "repository_id", "commit_sha", "instrument_id")},
                "emitters": {"worker": "worker", "main": "coordinator"}, "census_complete": True,
                "declarations": {"decl": {"file": "a.test.ts", "start": 10, "end": 100, "modifiers": []}},
                "points": {"point": {"instance_id": "target", "declaration_id": "decl"}}}
    value = {"project_id": "project", "file": "a.test.ts", "declaration_id": "decl", "occurrence": 0,
             "worker_id": "worker", "runtime_id": "runtime", "title": "igual ç", "modifiers": []}
    case_id = case_identity(value)
    rows = [
        {"event": "run_started", "role": "worker", "tools": {"vitest": "5.0.5"}},
        {"event": "case_started", "case_id": case_id, "case": value},
        {"event": "direct_call", "case_id": case_id, "attempt": 0, "call_id": "first", "point_id": "point", "instance_id": "target"},
        {"event": "target_entered", "case_id": case_id, "attempt": 0, "call_id": "first", "instance_id": "target"},
        {"event": "direct_call", "case_id": case_id, "attempt": 1, "call_id": "second", "point_id": "point", "instance_id": "target"},
        {"event": "target_entered", "case_id": case_id, "attempt": 1, "call_id": "second", "instance_id": "target"},
        {"event": "case_finished", "case_id": case_id, "status": "passed", "attempts": [
            {"index": 0, "retry": 0, "repeat": 0, "status": "failed", "expected_failure": False},
            {"index": 1, "retry": 1, "repeat": 0, "status": "passed", "expected_failure": False}]},
        {"event": "run_finished", "complete": True, "emitter_ids": [], "limitations": []},
    ]
    events = [{**identity, "emitter_id": "worker", "sequence": index, **event} for index, event in enumerate(rows, 1)]
    events.extend([
        {**identity, "emitter_id": "main", "sequence": 1, "event": "run_started", "role": "coordinator", "tools": {}},
        {**identity, "emitter_id": "main", "sequence": 2, "event": "run_finished", "complete": True,
         "emitter_ids": ["worker", "main"], "limitations": []},
    ])
    return events, expected


def test_preserves_same_case_calls_and_failed_attempt():
    """Não perde falha anterior nem converte tentativas/chamadas em novos casos."""
    events, expected = trace()
    result = validate_events(decode_events(("\n".join(json.dumps(row) for row in events) + "\n").encode()), expected)
    assert result["complete"] is True
    assert len(result["cases"]) == 1 and len(result["calls"]) == 2
    case = next(iter(result["cases"].values()))
    assert case["finished"]["status"] == "passed"
    assert case["finished"]["attempts"][0]["status"] == "failed"
    assert all(call["entered"] is not None for call in result["calls"])


def test_node_emitter_and_python_validator_share_identity_and_call_tokens():
    """Exercita protocolo real entre linguagens, incluindo título Unicode e retry."""
    events, expected = trace()
    module = (Path(__file__).parents[1] / "src/robo_minerador/typescript/validation-runtime.mjs").as_uri()
    identity = {key: expected[key] for key in ("session_id", "repository_id", "commit_sha", "instrument_id")}
    script = f"""
import {{createRuntime}} from {json.dumps(module)};
const events=[];
/** Retém eventos sem serialização intermediária com perda. */
function write(event){{events.push(event);}}
const identity={json.dumps(identity)};
const runtime=createRuntime({{...identity,emitter_id:'worker'}},write);
runtime.start('worker',{{node:process.versions.node}});
const caseId=runtime.startCase({json.dumps(events[1]['case'])});
/** Alvo original cuja identidade é comprovada na entrada. */
function target(value){{runtime.enter('target',target);return value+1;}}
const invoke=runtime.prepare('point','target',target,undefined);
/** Representa primeira tentativa sem criar caso adicional. */
function first(){{invoke(1);}}
/** Representa retry reconhecido do mesmo caso. */
function retry(){{invoke(2);}}
runtime.runCase(caseId,0,first);runtime.runCase(caseId,1,retry);
runtime.finishCase(caseId,'passed',{json.dumps(events[6]['attempts'])});
runtime.finish(true);
const main=createRuntime({{...identity,emitter_id:'main'}},write);
main.start('coordinator',{{node:process.versions.node}});main.finish(true,['worker','main']);
/** Serializa somente o evento, sem argumentos implícitos de Array.map. */
function serialize(event){{return JSON.stringify(event);}}
console.log(events.map(serialize).join('\\n'));
"""
    result = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    evidence = validate_events(decode_events(result.stdout), expected)
    assert evidence["complete"] is True and len(evidence["cases"]) == 1
    assert len(evidence["calls"]) == 2 and all(call["entered"] for call in evidence["calls"])


@pytest.mark.parametrize("corruption", ["identity", "duplicate", "unknown", "orphan", "target", "case", "attempt",
                                        "point", "nonfinite", "special", "final_status", "retry_order"])
def test_rejects_false_association(corruption):
    """Não aceita fabricar associação original ou apagar fracasso por incoerência."""
    events, expected = trace()
    if corruption == "identity":
        events[3]["session_id"] = "other"
    elif corruption == "duplicate":
        events.insert(4, deepcopy(events[3]))
    elif corruption == "unknown":
        events[3]["event"] = "coverage_is_proof"
    elif corruption == "orphan":
        events[3]["call_id"] = "unknown"
    elif corruption == "target":
        events[3]["instance_id"] = "other"
    elif corruption == "case":
        events[3]["case_id"] = "other"
    elif corruption == "attempt":
        events[3]["attempt"] = 1
    elif corruption == "point":
        events[2]["point_id"] = "unknown"
    elif corruption == "nonfinite":
        events[0]["tools"]["metric"] = float("nan")
    elif corruption == "special":
        events[6]["attempts"][0]["expected_failure"] = True
    elif corruption == "final_status":
        events[6]["attempts"][1]["status"] = "failed"
    else:
        events[6]["attempts"][1]["retry"] = 9
    with pytest.raises(ValueError):
        validate_events(events, expected)


@pytest.mark.parametrize("missing", ["worker", "coordinator", "census", "gap", "finish", "empty"])
def test_missing_observation_is_incomplete_not_zero(missing):
    """Uma captura parcial nunca autoriza publicar contagem exata de zero chamadas."""
    events, expected = trace()
    if missing == "worker":
        events = events[8:]
    elif missing == "coordinator":
        events = events[:8]
    elif missing == "census":
        expected["census_complete"] = False
    elif missing == "gap":
        for event in events[4:8]:
            event["sequence"] += 1
    elif missing == "finish":
        events = events[:6]
    else:
        events = []
    result = validate_events(events, expected)
    assert result["complete"] is False and result["limitations"]


@pytest.mark.parametrize("raw", [b'{"event":"run_started","event":"run_finished"}', b'{"value":NaN}',
                                  b'{"value":1e999}', b'{}\n\n', b'\xff'])
def test_rejects_lossy_json_input(raw):
    """Preserva bytes de evidência recusando decoder permissivo antes da associação."""
    with pytest.raises(ValueError):
        decode_events(raw)


@pytest.mark.skipif(os.environ.get("ROBO_TEST_DOCKER") != "1", reason="Ponte Docker exige opt-in; skip não comprova integração.")
@pytest.mark.parametrize("recipe_index,vite,configuration", [(0, "7.3.6", "ts"), (1, "8.3.0", "none"),
                                                            (3, "8.3.4", "none"), (5, "8.3.5", "projects"),
                                                            (0, "7.3.6", "coverage"), (0, "7.3.6", "early-failure"),
                                                            (0, "7.3.6", "opaque-shell"), (0, "7.3.6", "opaque-env")])
def test_real_t1_t2_bridge_preserves_npm_lifecycle_args_and_census(tmp_path, recipe_index, vite, configuration):
    """Executa apenas Git/receita de fixture confiável pela ponte completa, sem candidato."""
    from robo_minerador.validation_docker import DockerExecutor
    from robo_minerador.validation_recipe import load_recipes
    from robo_minerador.validation_workspace import prepare_workspace
    from robo_minerador.validation_instrument import configure_instrument
    from robo_minerador.extraction_context import load_repository_context
    from test_extraction_context import make_local_snapshot, published_case

    recipe = deepcopy(list(load_recipes()["recipes"].values())[recipe_index])
    vitest = recipe["tools"]["vitest"]
    # Node26 usa npm só nesta fixture; a receita científica pnpm permanece intacta.
    if "pnpm" in recipe["tools"]:
        recipe["tools"] = {"node": "26.13.0", "npm": "11.20.0", "vitest": vitest}
        recipe.pop("tool_bootstrap", None)
    manifest = {"name": "trusted-validation-fixture", "version": "1.0.0", "private": True, "type": "module",
                "scripts": {"pretest": "node pretest.mjs", "test": "vitest run", "posttest": "node posttest.mjs"},
                "devDependencies": {"vitest": vitest, "vite": vite}}
    if configuration == "coverage":
        manifest["devDependencies"]["@vitest/coverage-v8"] = vitest
    executor = DockerExecutor()
    try:
        executor.preflight()
        script = r"""
const fs=require('node:fs'),cp=require('node:child_process');
fs.mkdirSync('/tmp/fixture');process.chdir('/tmp/fixture');fs.writeFileSync('package.json',process.argv[1]);
const result=cp.spawnSync('npm',['install','--package-lock-only','--legacy-peer-deps','--ignore-scripts','--no-audit','--no-fund'],{encoding:'utf8'});
if(result.status!==0)throw Error(result.stderr);console.log(fs.readFileSync('package-lock.json').toString('base64'));
"""
        locked = executor._trusted(recipe, ["node", "-e", script, json.dumps(manifest)], network="bridge")
        import base64
        lock = base64.b64decode(executor.read_log(locked).strip())
        contents = {
            "package.json": json.dumps(manifest), "package-lock.json": lock, ".npmrc": "legacy-peer-deps=true\n",
            "pretest.mjs": "/** Marca lifecycle original. */\nimport fs from 'node:fs'; fs.writeFileSync('pretest.marker','original');\n",
            "posttest.mjs": "/** Marca lifecycle posterior original. */\nimport fs from 'node:fs'; fs.writeFileSync('posttest.marker','original-post');\n",
            "target.ts": "/** Soma uma unidade ao valor recebido. */\nexport function compute(value: number){ return value+1; }\n",
            "target.test.ts": "import {test,expect,describe} from 'vitest'; import {compute} from './target';\n"
                              "test('duas',/** Executa duas chamadas do mesmo caso. */function twice(){expect(compute(1)).toBe(2);expect(compute(2)).toBe(3);});\n"
                              "test.each([1,2,3,4])('igual',/** Registra ocorrências com mesmo título. */function each(value){expect(compute(value)).toBe(value+1);});\n"
                              "let attempt=0;test('retry',{retry:1},/** Preserva a primeira falha. */function retry(){compute(1);if(++attempt===1)throw Error('primeira');});\n"
                              "test.skip('skip',/** Não executa o corpo. */function ignored(){compute(9);});test.todo('todo');\n"
                              "describe.skip('wrapper',/** Mantém skip herdado. */function wrapper(){\n"
                              "test('nested skip',/** Não deve desaparecer do censo. */function skipped(){compute(10);});\n"
                              "});\n"
                              "test('unassociated',/** Censa caso sem chamada à candidata. */function unrelated(){expect(2+2).toBe(4);});\n",
        }
        if configuration == "ts":
            contents["vitest.config.ts"] = "import {defineConfig} from 'vitest/config';export default defineConfig({test:{environment:'node'}});\n"
        elif configuration == "projects":
            contents["vitest.config.ts"] = "import {defineConfig} from 'vitest/config';export default defineConfig({test:{projects:[{extends:true,test:{name:'unit',include:['target.test.ts']}}]}});\n"
        elif configuration == "early-failure":
            contents["failed.test.ts"] = "import {test,beforeAll} from 'vitest';import {compute} from './target';\n" \
                "beforeAll(/** Falha antes do corpo, sem apagar o caso coletado. */function fail(){throw Error('beforeAll fixture');});\n" \
                "test('early failure',/** Corpo não executado. */function unexecuted(){compute(1);});\n"
        elif configuration in {"opaque-shell", "opaque-env"}:
            contents["hidden.mjs"] = "/** Registra descendente sem preload, exclusivamente nesta fixture. */\n" \
                "import fs from 'node:fs';fs.writeFileSync('hidden.json',JSON.stringify({pid:process.pid,node_options:process.env.NODE_OPTIONS??null}));\n"
            launcher = ("'/bin/sh',['-c','env -u NODE_OPTIONS node hidden.mjs']" if configuration == "opaque-shell"
                        else "'/usr/bin/env',['-u','NODE_OPTIONS','node','hidden.mjs']")
            contents["pretest.mjs"] += "import {spawnSync} from 'node:child_process';\n" \
                f"const child=spawnSync({launcher},{{stdio:'inherit'}});if(child.status!==0)throw Error('fixture child failed');\n"
        state = make_local_snapshot(tmp_path, contents)
        repository, reasons, units = published_case(state)
        approved = [unit for unit in units if unit["decision"] == "approved"]
        assert len(approved) == 1, units
        verified = load_repository_context(approved, repository, reasons, state.store)
        recipe.update(repository_id=1, repository_url=state.candidate["repository_url"], commit_sha=state.candidate["commit_sha"])
        recipe["lockfiles"] = [{"path": "package-lock.json", "sha256": hashlib.sha256(lock).hexdigest(), "manager": "npm"}]
        if not any(item["path"] == "package-lock.json" for item in recipe["evidence"]):
            recipe["evidence"].append({"path": "package-lock.json", "detail": "Lock da fixture confiável, gerado antes do snapshot."})
        recipe["phases"] = {"installation": [{"argv": ["npm", "ci"], "cwd": "."}], "preparation": [],
                            "unit_suite": [{"argv": ["npm", "test", "--", "--reporter=dot"], "cwd": "."}]}
        recipe["unit_scope"]["packages"] = [{"path": ".", "name": manifest["name"], "scope": "Suíte completa da fixture confiável."}]
        workspace = prepare_workspace(state.store, verified, recipe, executor)
        installation = executor.run_phase(workspace, "installation", recipe)
        assert installation["status"] == "completed", installation
        executor.run_phase(workspace, "preparation", recipe)
        instrument = configure_instrument(workspace, verified, recipe, executor)
        assert not instrument.plan["limitations"], instrument.plan["limitations"]
        suite = executor.run_phase(workspace, "unit_suite", recipe)
        collection = instrument.collect(executor, suite)
        logs = executor.read_log(suite["commands"][0]).decode(errors="replace") + executor.read_log(suite["commands"][0], "stderr").decode(errors="replace")
        opaque = configuration in {"opaque-shell", "opaque-env"}
        if collection["complete"] != (not opaque):
            debug = collection["commands"][0].get("report") or {}
            print(json.dumps({"limitations": debug.get("limitations"), "suites": debug.get("suites"),
                              "workers": debug.get("workers"), "diagnostics": debug.get("diagnostics")}, ensure_ascii=True))
        assert collection["complete"] is (not opaque), (collection["commands"][0].get("limitations"),
                                        (collection["commands"][0].get("report") or {}).get("limitations"), logs)
        assert suite["status"] == ("failed" if opaque or configuration == "early-failure" else "completed"), logs
        assert executor.read_workspace_files(workspace, ["pretest.marker"])["pretest.marker"] == b"original"
        if configuration != "early-failure":
            assert executor.read_workspace_files(workspace, ["posttest.marker"])["posttest.marker"] == b"original-post"
        report = collection["commands"][0]["report"]
        if opaque:
            from robo_minerador.validation_evidence import validate_census
            hidden = json.loads(executor.read_workspace_files(workspace, ["hidden.json"])["hidden.json"])
            assert hidden["node_options"] is None
            assert hidden["pid"] not in {process["pid"] for process in report["processes"]}
            assert report["original_exit"] == {"code": 0, "signal": None}
            assert report["complete"] is False and suite["commands"][0]["exitcode"] == 86
            child = next(item for item in report["synchronous_children"] if item["file"] == ("/bin/sh" if configuration == "opaque-shell" else "/usr/bin/env"))
            assert child["preload_inherited"] is True
            assert child["process_observed"] is False
            assert child["delegation_observable"] is False
            assert child["opaque_shell"] is (configuration == "opaque-shell")
            assert validate_census(report, instrument.command_packages[0], instrument.plan)["complete"] is False
            # Nem um process_observed=true adulterado legitima uma delegação opaca.
            tampered_census = deepcopy(report)
            for item in tampered_census["synchronous_children"]:
                item["process_observed"] = True
            assert validate_census(tampered_census, instrument.command_packages[0], instrument.plan)["complete"] is False
        assert report["original_argv"] == ["npm", "test", "--", "--reporter=dot"]
        assert len(report["suites"]) == 1 and len(report["suites"][0]["tasks"]) == (11 if configuration == "early-failure" else 10)
        cases = collection["commands"][0]["evidence"]["cases"]
        retry = next(case for case in cases.values() if case["started"]["case"]["title"] == "retry")
        assert [attempt["status"] for attempt in retry["finished"]["attempts"]] == ["failed", "passed"]
        assert len({case["started"]["case_id"] for case in cases.values() if case["started"]["case"]["title"] == "igual"}) == 4
        nested = next(case for case in cases.values() if case["started"]["case"]["title"] == "nested skip")
        assert nested["finished"]["status"] == "skipped" and not nested["finished"]["attempts"]
        if configuration == "early-failure":
            failed = next(case for case in cases.values() if case["started"]["case"]["title"] == "early failure")
            # Vitest 4 marca o corpo como skipped e atribui a falha ao módulo/hook.
            assert failed["finished"]["status"] == "skipped" and not failed["finished"]["attempts"]
            assert any(module["state"] == "failed" and module["errors"] for module in report["suites"][0]["module_results"])
        coverage = collection["commands"][0]["coverage"]
        assert coverage["associated_tests"]["supported"] is False
        if configuration == "coverage":
            assert coverage["full_unit_suite"][0]["supported"] is True, (instrument.installations, coverage)
            assert coverage["full_unit_suite"][0]["provider_data"]
        else:
            assert coverage["full_unit_suite"][0]["supported"] is False
        # Omissão de um caso censado não pode deixar a passagem completa.
        from robo_minerador.validation_evidence import validate_census
        original_report = deepcopy(report)
        omitted = original_report["suites"][0]["tasks"].pop()
        assert omitted["id"] in {case["started"]["case"]["runtime_id"] for case in cases.values()}
        assert validate_census(original_report)["complete"] is False
        missing_transform = deepcopy(report)
        missing_transform["transforms"] = []
        assert validate_census(missing_transform, instrument.command_packages[0], instrument.plan)["complete"] is False
        if configuration == "ts":
            tampered = deepcopy(report)
            direct = next(event for event in tampered["events"] if event["event"] == "direct_call")
            fake = deepcopy(tampered["expected"]["points"][direct["point_id"]])
            direct["point_id"] = "forged-point"
            tampered["expected"]["points"]["forged-point"] = fake

            class ReportReader:
                """Simula catálogo adulterado no relatório sem alterar plano confiável ou executar suíte."""

                def read_workspace_files(self, requested_workspace, paths):
                    """Entrega bytes adulterados para testar fronteira de autoridade T1/runtime."""
                    return {paths[0]: json.dumps(tampered).encode()}

            assert instrument.collect(ReportReader(), suite)["complete"] is False
        print(json.dumps({"fixture": configuration, "tools": instrument.installations[0]["tools"],
                          "census_complete": collection["complete"], "cases": len(cases), "network": suite["commands"][0]["network"]}))
    finally:
        executor.close()
    assert not executor.cleanup_errors


def _nested_snapshot(tmp_path, contents):
    """Cria árvore Git confiável de workspace sem checkout, com subdiretórios reais."""
    from types import SimpleNamespace
    from robo_minerador.git_transport import GitTransport
    from robo_minerador.snapshot_store import SnapshotStore
    from test_git_transport import LocalTransport

    transport = GitTransport()
    origin = tmp_path / "origin.git"
    transport.run(["init", "--bare", "--template=", str(origin)])

    def tree(prefix):
        """Constrói cada nível com blobs UTF-8/bytes sem filtros Git do host."""
        names = {path[len(prefix):].split("/", 1)[0] for path in contents if path.startswith(prefix)}
        rows = []
        for name in sorted(names):
            full = prefix + name
            if full in contents:
                content = contents[full]
                raw = content.encode() if isinstance(content, str) else content
                oid = transport.run(["hash-object", "-w", "--stdin"], repository=origin, input_data=raw).strip()
                rows.append(b"100644 blob " + oid + b"\t" + name.encode() + b"\0")
            else:
                rows.append(b"040000 tree " + tree(full + "/") + b"\t" + name.encode() + b"\0")
        return transport.run(["mktree", "-z"], repository=origin, input_data=b"".join(rows)).strip()

    sha = transport.run(["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit-tree", tree("").decode()],
                        repository=origin, input_data=b"Fixture confiavel\n").decode().strip()
    transport.run(["update-ref", "refs/heads/main", sha], repository=origin)
    local = LocalTransport(origin)

    def get_repo(identity):
        """Fornece somente identidade local de fixture, sem API remota."""
        return SimpleNamespace(id=1, private=False, full_name="fixture/public")

    return SimpleNamespace(store=SnapshotStore(tmp_path / "cache", local), client=SimpleNamespace(get_repo=get_repo),
                           candidate={"repository_id": 1, "repository_url": "https://github.com/fixture/public", "commit_sha": sha})


@pytest.mark.skipif(os.environ.get("ROBO_TEST_DOCKER") != "1", reason="Pnpm/Docker exige opt-in; skip não comprova integração.")
def test_real_t1_t2_pnpm_recursive_original_processes_and_projects(tmp_path):
    """Comprova dois processos Vitest pela recursão pnpm original, sem reexecutar pacote."""
    import base64
    from robo_minerador.validation_docker import DockerExecutor, BOOTSTRAP
    from robo_minerador.validation_recipe import load_recipes
    from robo_minerador.validation_workspace import prepare_workspace
    from robo_minerador.validation_instrument import configure_instrument
    from robo_minerador.extraction_context import load_repository_context
    from test_extraction_context import published_case

    recipe = deepcopy(list(load_recipes()["recipes"].values())[-1])
    root = {"name": "trusted-recursive-fixture", "version": "1.0.0", "private": True, "type": "module",
            "packageManager": "pnpm@10.34.7", "scripts": {"pretest:unit": "node pretest.mjs", "test:unit": "pnpm -r test:unit",
                                                            "posttest:unit": "node posttest.mjs"}}
    contents = {"package.json": json.dumps(root), "pnpm-workspace.yaml": "packages:\n  - 'packages/*'\n",
                ".npmrc": "enable-pre-post-scripts=true\n",
                "pretest.mjs": "/** Marca lifecycle da raiz. */\nimport fs from 'node:fs';fs.writeFileSync('pretest.marker','original-pnpm');\n",
                "posttest.mjs": "/** Marca lifecycle posterior da raiz. */\nimport fs from 'node:fs';fs.writeFileSync('posttest.marker','original-pnpm-post');\n"}
    for name in ("a", "b"):
        prefix = "packages/" + name + "/"
        contents[prefix + "package.json"] = json.dumps({"name": "trusted-" + name, "version": "1.0.0", "type": "module",
            "scripts": {"test:unit": "vitest run --project unit" if name == "a" else "vitest run"},
            "devDependencies": {"vitest": "5.0.5", "vite": "8.3.5"}})
        contents[prefix + "target.ts"] = "/** Soma uma unidade à entrada. */\nexport function compute(value: number){return value+1;}\n"
        contents[prefix + "target.test.ts"] = "import {test,expect} from 'vitest';import {compute} from './target';\n" \
            "test.each([1,2,3,4])('igual',/** Executa ocorrência parametrizada original. */function each(value){expect(compute(value)).toBe(value+1);});\n"
    contents["packages/a/vitest.config.ts"] = "import {defineConfig} from 'vitest/config';export default defineConfig({test:{projects:[" \
        "{extends:true,test:{name:'unit',include:['target.test.ts']}},{extends:true,test:{name:'integration',include:['forbidden.spec.ts']}}]}});\n"
    contents["packages/a/forbidden.spec.ts"] = "import {test} from 'vitest';test('fora do escopo',/** Não deve executar integração. */function forbidden(){throw Error('fora do escopo');});\n"
    executor = DockerExecutor()
    try:
        executor.preflight()
        tools = executor._volume()
        executor._trusted(recipe, ["node", "-e", BOOTSTRAP, json.dumps(recipe["tool_bootstrap"])],
                          tool_volume=tools, writable_tools=True, network="bridge")
        script = r"""
const fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process');
fs.mkdirSync('/tmp/fixture');process.chdir('/tmp/fixture');
for(const [file,content] of Object.entries(JSON.parse(process.argv[1]))){fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,content);}
const result=cp.spawnSync('pnpm',['install','--lockfile-only','--ignore-scripts'],{encoding:'utf8'});
if(result.status!==0)throw Error(result.stdout+result.stderr);console.log(fs.readFileSync('pnpm-lock.yaml').toString('base64'));
"""
        locked = executor._trusted(recipe, ["node", "-e", script, json.dumps(contents)], tool_volume=tools, network="bridge")
        lock = base64.b64decode(executor.read_log(locked).strip())
        contents["pnpm-lock.yaml"] = lock
        state = _nested_snapshot(tmp_path, contents)
        repository, reasons, units = published_case(state)
        approved = [unit for unit in units if unit["decision"] == "approved"]
        assert len(approved) == 2, units
        verified = load_repository_context(approved, repository, reasons, state.store)
        recipe.update(repository_id=1, repository_url=state.candidate["repository_url"], commit_sha=state.candidate["commit_sha"])
        recipe["lockfiles"][0]["sha256"] = hashlib.sha256(lock).hexdigest()
        recipe["unit_scope"]["packages"] = [{"path": path, "name": "fixture-" + path, "scope": "Folha original de fixture confiável."}
                                           for path in (".", "packages/a", "packages/b")]
        workspace = prepare_workspace(state.store, verified, recipe, executor)
        install = executor.run_phase(workspace, "installation", recipe)
        assert install["status"] == "completed", install
        executor.run_phase(workspace, "preparation", recipe)
        instrument = configure_instrument(workspace, verified, recipe, executor)
        assert not instrument.plan["limitations"], instrument.plan["limitations"]
        suite = executor.run_phase(workspace, "unit_suite", recipe)
        collection = instrument.collect(executor, suite)
        command = collection["commands"][0]
        if not collection["complete"]:
            debug = command.get("report") or {}
            print(json.dumps({"workers": debug.get("workers"), "starts": [event for event in debug.get("events", [])
                             if event["event"] == "case_started"], "points": instrument.plan["points"]}, ensure_ascii=True))
        assert collection["complete"], (command.get("limitations"), (command.get("report") or {}).get("limitations"),
                                       executor.read_log(suite["commands"][0], "stderr"))
        assert suite["status"] == "completed"
        assert executor.read_workspace_files(workspace, ["pretest.marker"])["pretest.marker"] == b"original-pnpm"
        assert executor.read_workspace_files(workspace, ["posttest.marker"])["posttest.marker"] == b"original-pnpm-post"
        assert len(command["report"]["suites"]) == 2
        assert len(command["evidence"]["cases"]) == 8
        assert {item["cwd"] for item in command["report"]["suites"]} == {"/workspace/packages/a", "/workspace/packages/b"}
        assert command["report"]["original_argv"] == ["pnpm", "test:unit"]
        assert all(item["original_exit"] == {"code": 0, "signal": None} for item in [command["report"]])
        print(json.dumps({"fixture": "pnpm-recursive", "tools": instrument.installations[0]["tools"],
                          "processes_vitest": 2, "cases": 8, "complete": collection["complete"]}))
    finally:
        executor.close()
    assert not executor.cleanup_errors
