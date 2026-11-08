"""Verifica independência local e integridade portátil sem executar candidatos."""

from copy import deepcopy

import pytest

from test_validation import controlled_fixture, published_fixture
from robo_minerador.validation_evaluation import evaluate_repository, aggregate, _count_instance_v1
from robo_minerador.validation_format import validate_output_records
from robo_minerador.validation_recipe import resolve_recipe


def unrelated_failure(execution):
    """Acrescenta falha localizada fora dos módulos associados da fixture."""
    report = execution["observation"]["commands"][0]["report"]
    report["suites"].append({"suite_id": "other", "terminal": True, "planned_modules": [{"file": "other.test.ts", "project_id": "other"}],
        "modules": [], "tasks": [{"id": "other", "file": "other.test.ts", "project_id": "other", "result": {"state": "failed"}}],
        "module_results": [], "errors": []})
    report["original_exit"]["code"] = 1
    execution["phases"]["unit_suite"]["commands"][0]["exitcode"] = 1
    execution["observation"]["complete"] = False
    execution["observation"]["commands"][0]["complete"] = False
    execution["observation"]["commands"][0]["census"]["complete"] = False


def test_global_rejection_does_not_short_circuit_local_execution(monkeypatch, tmp_path):
    """Repositório rejeitado preserva contagem e passagem local independente."""
    rows, _, _ = published_fixture(monkeypatch, tmp_path, counts=(4,), mutate=unrelated_failure)
    final = validate_output_records(rows)
    repository, instance = rows[1:3]
    assert repository["decision"] == "rejected"
    assert instance["criteria"]["execution_prerequisites"]["status"] == "approved"
    assert instance["criteria"]["associated_tests_execution"]["status"] == "approved"
    assert instance["criteria"]["original_target_execution"]["status"] == "approved"
    assert instance["test_cases"]["valid_count"] == 4
    assert instance["criteria"]["no_external_services"]["code"] == "INSTANCE_RESOURCE_AUDIT"
    assert instance["criteria"]["no_external_services"]["status"] == "approved"
    assert instance["decision"] == "approved" and final["status"] == "partial"


def test_operational_partial_run_can_retain_approved_instances(monkeypatch, tmp_path):
    """Falha operacional de limpeza não apaga instância com todos os gates comprovados."""
    from test_validation import FixtureExecutor
    original = FixtureExecutor.close

    def close(executor):
        """Acrescenta impedimento posterior à prova científica da fixture."""
        original(executor)
        executor.cleanup_errors.append({"error": "cleanup unavailable"})

    monkeypatch.setattr(FixtureExecutor, "close", close)
    rows, _, _ = published_fixture(monkeypatch, tmp_path, counts=(4,))
    final = validate_output_records(rows)
    assert rows[2]["decision"] == "approved"
    assert final["status"] == "partial" and final["instance_decisions"]["approved"] == 1


def test_unrelated_case_in_same_module_is_global_diagnostic(monkeypatch, tmp_path):
    """Outro teste no mesmo arquivo não rejeita declaração associada que passou."""
    def fail_other(execution):
        """Falha a segunda declaração e preserva o fechamento de hooks do módulo."""
        command = execution["observation"]["commands"][0]
        case = list(command["evidence"]["cases"].values())[-1]
        case["finished"]["status"] = case["finished"]["attempts"][0]["status"] = "failed"
        suite = command["report"]["suites"][0]
        suite["tasks"][-1]["result"]["state"] = "failed"
        suite["module_results"][0]["state"] = "failed"

    rows, _, _ = published_fixture(monkeypatch, tmp_path, counts=(4, 1), mutate=fail_other)
    validate_output_records(rows)
    assert rows[1]["decision"] == "rejected"
    assert rows[2]["decision"] == "approved"
    assert rows[3]["decision"] == "rejected"


@pytest.mark.parametrize("failure", ["case", "hook", "retry"])
def test_associated_failure_rejects_even_with_four_other_witnesses(monkeypatch, tmp_path, failure):
    """Falha local não desaparece após quatro positivos ou retry verde."""
    def fail(execution):
        """Mantém runner/reporter alinhados para a falha associada escolhida."""
        command = execution["observation"]["commands"][0]
        if failure == "hook":
            command["report"]["suites"][0]["module_results"][0].update(state="failed", errors=[{"message": "afterAll failed"}])
        else:
            case = next(iter(command["evidence"]["cases"].values()))
            case["finished"]["attempts"][0]["status"] = "failed"
            if failure == "case":
                case["finished"]["status"] = "failed"
                command["report"]["suites"][0]["tasks"][0]["result"]["state"] = "failed"
            else:
                case["finished"]["attempts"].append({"index": 1, "retry": 1, "repeat": 0, "status": "passed", "expected_failure": False})

    rows, _, _ = published_fixture(monkeypatch, tmp_path, counts=(5,), mutate=fail)
    validate_output_records(rows)
    instance = rows[2]
    assert instance["test_cases"]["valid_count"] >= 4
    assert instance["criteria"]["associated_tests_execution"]["status"] == "rejected"
    assert instance["decision"] == "rejected"


def test_four_witnesses_prove_identity_with_independent_local_closure(monkeypatch, tmp_path):
    """Fechamento local continua válido sem declarar exatidão de censo global."""
    def incomplete(execution):
        """Simula outro worker não observado, preservando o módulo local fechado."""
        observation = execution["observation"]
        command = observation["commands"][0]
        observation["complete"] = command["complete"] = command["census"]["complete"] = command["evidence"]["complete"] = False
        evidence = command["evidence"]
        other = {**evidence["emitters"]["worker"]["started"], "emitter_id": "other-worker"}
        evidence["emitters"]["other-worker"] = {"sequence": 1, "started": other, "finished": None}
        evidence["emitters"]["main"]["finished"]["emitter_ids"].append("other-worker")

    rows, _, _ = published_fixture(monkeypatch, tmp_path, counts=(4,), mutate=incomplete)
    assert validate_output_records(rows)["status"] == "partial"
    instance = rows[2]
    assert instance["test_cases"]["count_kind"] == "lower_bound"
    assert instance["test_cases"]["counting_complete"] is False
    for key in ("minimum_valid_cases", "original_target_execution", "associated_tests_execution"):
        assert instance["criteria"][key]["status"] == "approved"
    assert instance["criteria"]["no_external_services"]["status"] == "approved"
    assert instance["decision"] == "approved"


@pytest.mark.parametrize("error", [
    {"message": "ROBO_INSTRUMENT_FINALIZATION_FAILURE: Caso não finalizado pelo runner: case"},
    {"message": "Caso não finalizado pelo runner: case", "stack": "at ValidationRunner.onAfterRunFiles (/instrument/validation-runner.mjs:152:17)"},
])
def test_instrument_finalization_is_operational_pending(monkeypatch, tmp_path, error):
    """Erro conhecido do minerador não vira FRAMEWORK_SUITE_FAILURE candidato."""
    def instrument_failure(execution):
        """Preserva o erro bruto no reporter da passagem original."""
        execution["observation"]["commands"][0]["report"]["suites"][0]["errors"] = [error]

    rows, _, _ = published_fixture(monkeypatch, tmp_path, counts=(4,), mutate=instrument_failure)
    final = validate_output_records(rows)
    assert rows[1]["criteria"]["complete_unit_suite"]["code"] == "INSTRUMENT_FINALIZATION_FAILURE"
    assert rows[1]["decision"] == rows[2]["decision"] == "not_evaluated"
    assert final["status"] == "partial" and final["operational_errors"] > 0


@pytest.mark.parametrize("gap", ["open_worker", "sequence", "identity", "collision", "alias", "missing_module_result", "missing_module_result_complete", "unknown_error", "async_context", "transform"])
def test_unproven_local_closure_cannot_approve(gap):
    """Streams abertos, adulteração e falhas sem localização não demonstram passagem."""
    group, catalog, _, execution = controlled_fixture((4,))
    observation = execution["observation"]
    command = observation["commands"][0]
    evidence = command["evidence"]
    if gap == "open_worker":
        evidence["emitters"]["worker"]["finished"] = None
    elif gap == "sequence":
        evidence["calls"][0]["direct"]["sequence"] += 1
    elif gap == "identity":
        next(iter(evidence["cases"].values()))["started"]["case"]["runtime_id"] = "forged"
    elif gap == "collision":
        observation["commands"].append(deepcopy(command))
    elif gap == "alias":
        observation["plan"]["limitations"] = [{"instance_id": group["instances"][0]["instance_id"], "reason": "alias ambiguous"}]
    elif gap in {"missing_module_result", "missing_module_result_complete"}:
        command["report"]["suites"][0]["module_results"] = []
        observation["complete"] = gap == "missing_module_result_complete"
    elif gap == "async_context":
        evidence["calls"][0]["entered"]["case_id"] = "unknown"
    elif gap == "transform":
        command["report"]["transforms"][0]["sha256"] = "0" * 64
    else:
        command["report"]["suites"][0]["errors"] = [{"message": "unlocalized failure"}]
    _, instances = evaluate_repository(group, resolve_recipe(group["repository"], catalog), execution)
    assert instances[0]["criteria"]["associated_tests_execution"]["status"] == "not_evaluated"
    assert instances[0]["decision"] != "approved"


@pytest.mark.parametrize("tamper", ["worker", "hook", "prerequisites", "count_kind", "criteria", "schema", "origin", "raw_events"])
def test_portable_reader_rederives_local_contract(monkeypatch, tmp_path, tamper):
    """Alterar provas e manter decisão verde não engana o leitor sem Docker."""
    rows, _, _ = published_fixture(monkeypatch, tmp_path, counts=(4,))
    assert validate_output_records(rows)["status"] == "completed"
    if tamper == "worker":
        rows[1]["unit_suite"]["commands"][0]["evidence"]["emitters"]["worker"]["finished"] = None
    elif tamper == "hook":
        rows[1]["unit_suite"]["commands"][0]["report"]["suites"][0]["module_results"][0]["errors"] = [{"message": "hook failed"}]
    elif tamper == "prerequisites":
        rows[2]["criteria"]["execution_prerequisites"]["evidence"] = {}
    elif tamper == "count_kind":
        rows[2]["test_cases"]["count_kind"] = "lower_bound"
    elif tamper == "criteria":
        rows[2]["criteria"].pop("associated_tests_execution")
    elif tamper == "origin":
        rows[1]["unit_suite"]["commands"][0]["evidence"]["emitters"]["worker"]["started"]["repository_id"] = 9
    elif tamper == "raw_events":
        rows[1]["unit_suite"]["commands"][0]["report"]["events"] = []
    else:
        rows[2]["schema_version"] = 1
    with pytest.raises(ValueError):
        validate_output_records(rows)


def test_schema_one_keeps_historical_global_contract(monkeypatch, tmp_path):
    """Arquivo histórico uniforme v1 permanece legível com seus critérios originais."""
    rows, _, _ = published_fixture(monkeypatch, tmp_path, counts=(4,))
    repository = rows[1]
    for row in rows:
        row["schema_version"] = 1
    rows[0]["policy"].pop("instance_execution_scope")
    rows[0]["policy"].pop("resource_gate_scope")
    instance = rows[2]
    criteria = instance["criteria"]
    criteria.pop("execution_prerequisites")
    criteria.pop("associated_tests_execution")
    criteria["repository_execution"] = {"status": repository["decision"], "code": "REPOSITORY_RESULT", "message": "Decisão global histórica.", "evidence": {"repository_id": 1}}
    criteria["no_external_services"] = deepcopy(repository["criteria"]["no_external_services"])
    instance["test_cases"], instance["case_evidence"] = _count_instance_v1({"instance_id": instance["instance_id"],
        "tests": [item["test"] for item in instance["test_cases"]["associations"]]}, repository["unit_suite"])
    instance["decision"] = aggregate(criteria)
    assert validate_output_records(rows)["status"] == "completed"


def test_schema_two_provisional_policy_keeps_its_historical_meaning(monkeypatch, tmp_path):
    """v2 provisório não é reinterpretado como se tivesse auditoria local publicada."""
    from robo_minerador.validation_evaluation import provisional_resources
    rows, _, _ = published_fixture(monkeypatch, tmp_path, counts=(4,))
    rows[0]["policy"]["resource_gate_scope"] = "global_provisional"
    rows[1].pop("instance_resource_evidence")
    rows[1].pop("resource_local_method")
    rows[2]["criteria"]["no_external_services"] = provisional_resources(rows[1]["criteria"]["no_external_services"])
    assert validate_output_records(rows)["status"] == "completed"
