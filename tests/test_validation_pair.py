"""Exercita a política de comportamento com provas próprias, sem candidatos."""

import pytest

from test_validation import controlled_fixture, published_fixture, FixtureExecutor, reseal_fixture
from test_validation_local import unrelated_failure
from robo_minerador.validation_evaluation import evaluate_repository
from robo_minerador.validation_format import validate_output_records
from robo_minerador.validation_recipe import resolve_recipe


@pytest.mark.parametrize("audit", ["disabled", "unknown", "rejected", "error"])
def test_pair_approval_is_independent_of_resource_diagnostics(monkeypatch, tmp_path, audit):
    """Publica/reabre quatro testemunhas mesmo sem qualquer produtor de recursos."""
    def forbidden(*args):
        """Detecta execução automática indevida, inclusive helper indisponível."""
        raise RuntimeError("Node helper unavailable / timeout")

    monkeypatch.setattr(FixtureExecutor, "observe_resources", forbidden)
    group, catalog, _, execution = controlled_fixture((4,))
    execution["resource_diagnostics"] = {"mode": audit, "status": "rejected" if audit == "rejected" else "unknown"}
    import robo_minerador.validation_evaluation as evaluation
    monkeypatch.setattr(evaluation, "evaluate_resources", forbidden)
    repository, instances = evaluate_repository(group, resolve_recipe(group["repository"], catalog), execution, policy_version=2)
    assert repository["resource_diagnostics"] == execution["resource_diagnostics"]
    assert instances[0]["decision"] == "approved"
    rows, executor, _ = published_fixture(monkeypatch, tmp_path, counts=(4, 3), policy_version=2)
    final = validate_output_records(rows)
    assert rows[0]["schema_version"] == 3
    assert rows[0]["policy"]["version"] == 2
    assert rows[2]["decision"] == "approved"
    assert rows[3]["decision"] == "rejected"
    assert "no_external_services" not in rows[2]["criteria"]
    assert final["status"] == "completed" and final["operational_errors"] == 0
    assert executor.closed


def test_pair_can_be_approved_with_global_rejection(monkeypatch, tmp_path):
    """Não herda falha alheia nem exige contagem global exata."""
    rows, _, _ = published_fixture(monkeypatch, tmp_path, counts=(4,), mutate=unrelated_failure, policy_version=2)
    assert validate_output_records(rows)["status"] == "partial"
    assert rows[1]["decision"] == "rejected"
    assert rows[2]["decision"] == "approved"
    assert rows[2]["test_cases"]["count_kind"] == "lower_bound"


@pytest.mark.parametrize("gap", ["mock", "not_run", "skip", "todo", "retry", "hook", "instrument"])
def test_pair_does_not_fabricate_positive_witnesses(gap):
    """Retirar entrada/execução ou introduzir falha local impede aprovação."""
    group, catalog, _, execution = controlled_fixture((4,))
    command = execution["observation"]["commands"][0]
    evidence = command["evidence"]
    if gap == "mock":
        for call in evidence["calls"]:
            call["entered"] = None
        reseal_fixture(execution)
    elif gap == "not_run":
        execution["observation"]["commands"] = []
    elif gap in {"skip", "todo", "retry"}:
        for case in evidence["cases"].values():
            if gap == "retry":
                case["finished"]["attempts"][0]["status"] = "failed"
                case["finished"]["attempts"].append({"index": 1, "retry": 1, "repeat": 0, "status": "passed", "expected_failure": False})
            else:
                case["finished"].update(status="skipped" if gap == "skip" else "todo", attempts=[])
        if gap != "retry":
            evidence["calls"] = []
        reseal_fixture(execution)
    elif gap == "hook":
        command["report"]["suites"][0]["module_results"][0].update(state="failed", errors=[{"message": "afterAll failed"}])
    else:
        command["report"]["suites"][0]["errors"] = [{"message": "ROBO_INSTRUMENT_FINALIZATION_FAILURE"}]
    _, instances = evaluate_repository(group, resolve_recipe(group["repository"], catalog), execution, policy_version=2)
    assert instances[0]["decision"] != "approved"


@pytest.mark.parametrize("tamper", ["policy", "boolean_version", "legacy_schema", "witness", "origin", "hook", "minimum_metadata", "identity_metadata"])
def test_pair_portable_policy_and_witness_integrity(monkeypatch, tmp_path, tamper):
    """Trocar política, metadados ou provas não conserva publicação aprovada."""
    rows, _, _ = published_fixture(monkeypatch, tmp_path, counts=(4,), policy_version=2)
    validate_output_records(rows)
    if tamper == "policy":
        rows[0]["policy"]["resource_gate_scope"] = "associated_closure_v1"
    elif tamper == "boolean_version":
        rows[0]["policy"]["version"] = True
    elif tamper == "legacy_schema":
        for row in rows:
            row["schema_version"] = 2
    elif tamper == "witness":
        rows[2]["case_evidence"][0]["calls"][0]["entered"]["instance_id"] = "0" * 64
    elif tamper == "origin":
        rows[1]["unit_suite"]["commands"][0]["evidence"]["emitters"]["worker"]["started"]["repository_id"] = 999
    elif tamper == "minimum_metadata":
        rows[2]["criteria"]["minimum_valid_cases"]["evidence"]["valid_count"] = 999
    elif tamper == "identity_metadata":
        rows[2]["criteria"]["original_target_execution"]["evidence"]["case_ids"] = []
    else:
        rows[1]["unit_suite"]["commands"][0]["report"]["suites"][0]["module_results"][0]["errors"] = [{"message": "hook failed"}]
    with pytest.raises(ValueError):
        validate_output_records(rows)


def test_replay_rederives_every_instance_and_reopens_portably(monkeypatch, tmp_path):
    """Replay explícito muda contrato sem editar decisões nem invocar auditor AST."""
    from robo_minerador.validation_replay import replay_pair_records
    from robo_minerador.jsonl import write_jsonl
    from robo_minerador.validation_evidence import decode_events
    rows, _, _ = published_fixture(monkeypatch, tmp_path, counts=(4, 3))
    original = rows[2]["decision"]
    replay = replay_pair_records(rows, source_path="historical.jsonl", source_sha256="a" * 64)
    output = tmp_path / "pair.jsonl"
    write_jsonl(output, replay, ensure_ascii=True, compact=True)
    published = decode_events(output.read_bytes())
    final = validate_output_records(published)
    assert final["instance_decisions"] == {"approved": 1, "rejected": 1, "not_evaluated": 0}
    assert published[0]["replay"]["sha256"] == "a" * 64
    assert published[0]["replay"]["kind"] == "posthoc_saved_evidence"
    assert rows[2]["decision"] == original and rows[0]["schema_version"] == 2
    assert published[2]["case_evidence"] == rows[2]["case_evidence"]
    published[2]["case_evidence"][0]["instrument_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        validate_output_records(published)


def test_pair_reader_never_requires_resource_helper(monkeypatch, tmp_path):
    """Mesmo prova histórica de recursos rejeitada/desconhecida dispensa Node."""
    rows, _, _ = published_fixture(monkeypatch, tmp_path, counts=(4,), policy_version=2)
    import robo_minerador.validation_resources as resources
    import robo_minerador.validation_resources_local as local

    def unavailable(*args, **kwargs):
        """Representa indisponibilidade, exceção ou timeout do auxiliar opcional."""
        raise TimeoutError("resource helper unavailable")

    monkeypatch.setattr(resources, "_analyze", unavailable)
    monkeypatch.setattr(local, "evaluate_instance_resources", unavailable)
    rows[1]["resource_diagnostics"] = {"mode": "historical_not_recertified", "status": "rejected"}
    assert validate_output_records(rows)["instance_decisions"]["approved"] == 1


def test_pair_unrelated_failure_in_same_file_is_not_inherited(monkeypatch, tmp_path):
    """Falha em outra declaração não herda reprovação por compartilhar arquivo."""
    def fail_other(execution):
        """Conserva o fechamento local dos hooks e a falha alheia no reporter."""
        command = execution["observation"]["commands"][0]
        case = list(command["evidence"]["cases"].values())[-1]
        case["finished"]["status"] = case["finished"]["attempts"][0]["status"] = "failed"
        suite = command["report"]["suites"][0]
        suite["tasks"][-1]["result"]["state"] = "failed"
        suite["module_results"][0]["state"] = "failed"

    rows, _, _ = published_fixture(monkeypatch, tmp_path, counts=(4, 1), mutate=fail_other, policy_version=2)
    validate_output_records(rows)
    assert rows[1]["decision"] == rows[3]["decision"] == "rejected"
    assert rows[2]["decision"] == "approved"


@pytest.mark.parametrize("tamper", ["hash", "candidate_execution", "source_policy", "source_schema"])
def test_replay_provenance_is_strictly_validated(monkeypatch, tmp_path, tamper):
    """Replay não pode anunciar nova execução ou esconder a política de origem."""
    from robo_minerador.validation_replay import replay_pair_records
    rows, _, _ = published_fixture(monkeypatch, tmp_path, counts=(4,))
    replay = replay_pair_records(rows, source_path="original.jsonl", source_sha256="a" * 64)
    provenance = replay[0]["replay"]
    if tamper == "hash":
        provenance["sha256"] = "invalid"
    elif tamper == "candidate_execution":
        provenance["candidate_execution"] = True
    elif tamper == "source_schema":
        provenance["schema_version"] = True
    else:
        provenance["policy"] = replay[0]["policy"]
    with pytest.raises(ValueError):
        validate_output_records(replay)
