"""Protege a publicação da deduplicação e a composição das etapas anteriores."""

from copy import deepcopy
import hashlib
import json
import subprocess
from types import SimpleNamespace

import pytest

from robo_minerador import cli, config, deduplication_input, validation
from robo_minerador.deduplication_format import load_deduplication
from robo_minerador.extraction import _extract_candidate
from robo_minerador.extraction_analyzer import Analyzer, preflight
from robo_minerador.extraction_context import load_repository_context
from robo_minerador.extraction_format import compact_records
from robo_minerador.jsonl import write_jsonl
from robo_minerador.validation_evidence import case_identity, validate_events
from robo_minerador.validation_instrument import HELPER
from robo_minerador.validation_input import load_extraction
from robo_minerador.validation_format import validate_output_records
from test_extraction_context import make_local_snapshot
from test_extraction_format import diagnostic
from test_validation import controlled_fixture, FixtureExecutor
from test_validation_input import extraction_rows, empty_repository_rows, write_rows  # noqa: F401


@pytest.fixture
def paths(monkeypatch, tmp_path):
    """Limita as entradas e a saída a caminhos temporários."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(config, "DEDUPLICATION_INPUT_PATH", tmp_path / "validation.jsonl")
    monkeypatch.setattr(config, "DEDUPLICATION_EXTRACTION_PATH", tmp_path / "extraction.jsonl")
    monkeypatch.setattr(config, "DEDUPLICATION_OUTPUT_PATH", tmp_path / "output.jsonl")
    return config.DEDUPLICATION_OUTPUT_PATH


def forbidden(*args, **kwargs):
    """Detecta chamada indevida de dependência ou leitor."""
    raise AssertionError("Chamada inesperada.")


def empty_inputs(tmp_path, rows):
    """Produz extração e validação v3 coerentes sem execução de candidatos."""
    extraction, _ = write_rows(tmp_path, empty_repository_rows(rows))
    groups, source = load_extraction(extraction)
    output = tmp_path / "validation.jsonl"
    write_jsonl(output, validation.validate_repositories(groups, source, {}, policy_version=2),
                ensure_ascii=True, compact=True)
    return extraction, output


def positive_inputs(monkeypatch, tmp_path):
    """Produz 03/04 vinculados à mesma fonte e a eventos simulados de quatro casos."""
    _, catalog, _, execution = controlled_fixture((4,))
    contents = {item["path"]: item["content"] for item in execution["verified"]["context"]["files"]}
    contents["target.ts"] = ("/** Incrementa a entrada. */\n" + contents["target.ts"]
                             + "export function undocumented(value: number) { return value + 2; }\n")
    state = make_local_snapshot(tmp_path, contents)
    analyzer = Analyzer(preflight())
    try:
        extracted = _extract_candidate(state.client, state.candidate, analyzer, store=state.store)
    finally:
        analyzer.cancel()
    repository = extracted[0]
    rows = list(compact_records([
        diagnostic("run_started", message="Iniciada.", input_count=1, eligible_count=1), *extracted,
        diagnostic("run_finished", repository_count=1, **repository["summary"], message="Encerrada.",
                   error_count=len(repository["issues"]), status=repository["extraction_status"]),
    ], schema_version=3))
    extraction, _ = write_rows(tmp_path, rows)
    assert any(row.get("record_type") == "instance" and row["decision"] == "rejected" for row in rows)
    groups, source = load_extraction(extraction)
    assert len(groups) == 1 and len(groups[0]["instances"]) == 1
    group = groups[0]
    verified = load_repository_context(group["instances"], group["repository"], group["reasons"], state.store)
    identity = {key: state.candidate[key] for key in ("repository_id", "commit_sha")}
    payload = {"files": verified["context"]["files"], "instances": list(verified["instances"].values()), "identity": identity}
    process = subprocess.run([preflight()["node"], str(HELPER / "validation-plan.mjs")],
                             input=json.dumps(payload).encode(), capture_output=True, check=True, timeout=180)
    plan = json.loads(process.stdout)
    assert not plan["limitations"] and len(plan["targets"]) == 1 and len(plan["points"]) == 1
    point = next(iter(plan["points"].values()))
    declaration_id = point["declaration_id"]
    instance_id = point["instance_id"]
    protocol = {"protocol_version": 1, "session_id": "fixture", **identity, "instrument_id": "b" * 64}
    events = [{**protocol, "emitter_id": "worker", "sequence": 1, "event": "run_started",
               "role": "worker", "tools": {"vitest": "4.1.10"}}]
    tasks = []
    for occurrence in range(4):
        case = {"project_id": "unit", "file": point["path"], "declaration_id": declaration_id,
                "occurrence": occurrence, "worker_id": "worker", "runtime_id": str(occurrence),
                "title": "igual ç", "modifiers": []}
        case_id = case_identity(case)
        call_id = str(occurrence)
        sequence = [
            {"event": "case_started", "case_id": case_id, "case": case},
            {"event": "direct_call", "case_id": case_id, "attempt": 0, "call_id": call_id,
             "point_id": point["id"], "instance_id": instance_id},
            {"event": "target_entered", "case_id": case_id, "attempt": 0, "call_id": call_id, "instance_id": instance_id},
            {"event": "case_finished", "case_id": case_id, "status": "passed", "attempts": [
                {"index": 0, "retry": 0, "repeat": 0, "status": "passed", "expected_failure": False}]},
        ]
        for event in sequence:
            events.append({**protocol, "emitter_id": "worker", "sequence": len(events) + 1, **event})
        tasks.append({"id": str(occurrence), "file": point["path"], "project_id": "unit", "result": {"state": "passed"}})
    events.append({**protocol, "emitter_id": "worker", "sequence": len(events) + 1, "event": "run_finished",
                   "complete": True, "emitter_ids": [], "limitations": []})
    events.extend([{**protocol, "emitter_id": "main", "sequence": 1, "event": "run_started", "role": "coordinator", "tools": {}},
                   {**protocol, "emitter_id": "main", "sequence": 2, "event": "run_finished", "complete": True,
                    "emitter_ids": ["worker", "main"], "limitations": []}])
    evidence = validate_events(events, {**protocol, "emitters": {"worker": "worker", "main": "coordinator"},
                               "census_complete": True, "declarations": plan["declarations"], "points": plan["points"]})
    observation = execution["observation"]
    observation["plan"] = plan
    command = observation["commands"][0]
    command["evidence"] = evidence
    command["report"]["suites"][0]["tasks"] = tasks
    hashes = [{"path": item["path"], "sha256": item["sha256"]} for item in plan["plans"]]
    command["report"]["transforms"] = [{**item, "suite_id": "suite"} for item in hashes]
    command["report"]["resource_modules"] = [{**item, "origin": "vite_transform", "suite_id": "suite"} for item in hashes]
    recipe = next(iter(catalog["recipes"].values()))
    recipe.update(state.candidate)
    catalog["recipes"] = {(identity["repository_id"], identity["commit_sha"]): recipe}
    execution["verified"] = verified
    executor = FixtureExecutor(execution)
    monkeypatch.setattr(validation, "load_repository_context", lambda *args: verified)
    monkeypatch.setattr(validation, "prepare_workspace", lambda *args: SimpleNamespace(environment=execution["environment"]))
    monkeypatch.setattr(validation, "configure_instrument", lambda *args: SimpleNamespace(collect=lambda *args: observation))
    output = tmp_path / "validation.jsonl"
    write_jsonl(output, validation.validate_repositories(groups, source, catalog, executor, state.store, policy_version=2),
                ensure_ascii=True, compact=True)
    published = [json.loads(line) for line in output.read_bytes().splitlines()]
    validate_output_records(published)
    return extraction, output, published, instance_id


def test_positive_real_pipeline(monkeypatch, paths, tmp_path):
    """Seleciona candidata aprovada em 03/04 reais e relê seu representante no 05."""
    extraction, validated, rows, instance_id = positive_inputs(monkeypatch, tmp_path)
    assert [row["decision"] for row in rows if row["record_type"] == "instance"] == ["approved"]
    monkeypatch.setattr(config, "DEDUPLICATION_EXTRACTION_PATH", extraction)
    monkeypatch.setattr(config, "DEDUPLICATION_INPUT_PATH", validated)
    assert cli.main(["deduplicate"]) == 0
    instances, opening = load_deduplication(paths)
    assert opening["sources"]["extraction"]["sha256"] == hashlib.sha256(extraction.read_bytes()).hexdigest()
    assert opening["sources"]["validation"]["sha256"] == hashlib.sha256(validated.read_bytes()).hexdigest()
    assert len(instances) == 1 and instances[0]["instance_id"] == instance_id
    assert instances[0]["decision"] == "approved" and instances[0]["representative_instance_id"] == instance_id
    assert len(instances[0]["representation_sha256"]) == 64
    closing = json.loads(paths.read_bytes().splitlines()[-1])
    assert (closing["eligible_count"], closing["representative_count"], closing["duplicate_count"],
            closing["duplicate_group_count"], closing["status"]) == (1, 1, 0, 0, "completed")


def test_empty_real_inputs_without_external_tools(monkeypatch, paths, tmp_path, extraction_rows):
    """Relê as três publicações reais e dispensa ferramentas com zero aprovadas."""
    extraction, validated = empty_inputs(tmp_path, extraction_rows)
    monkeypatch.setattr(config, "DEDUPLICATION_EXTRACTION_PATH", extraction)
    monkeypatch.setattr(config, "DEDUPLICATION_INPUT_PATH", validated)
    monkeypatch.setattr(deduplication_input, "preflight", forbidden)
    monkeypatch.setattr(cli, "GitTransport", forbidden)
    monkeypatch.setattr(cli, "DockerExecutor", forbidden)
    assert cli.main(["deduplicate"]) == 0
    instances, opening = load_deduplication(paths)
    assert instances == [] and opening["eligible_count"] == 0
    assert json.loads(paths.read_text().splitlines()[-1])["status"] == "completed"


def test_existing_output_refuses_before_reader(monkeypatch, paths):
    """Recusa substituir o arquivo antes de carregar qualquer entrada."""
    paths.write_bytes(b"previous")
    monkeypatch.setattr(cli, "load_deduplication_inputs", forbidden)
    assert cli.main(["deduplicate"]) == 2
    assert paths.read_bytes() == b"previous"


def test_invalid_inputs_preserve_output(monkeypatch, paths, capsys):
    """Não apaga bytes anteriores nem divulga conteúdo de entrada inválida."""
    paths.write_bytes(b"previous")
    config.DEDUPLICATION_INPUT_PATH.write_text("segredo inválido", encoding="utf-8")
    assert cli.main(["deduplicate", "--overwrite"]) == 2
    assert paths.read_bytes() == b"previous"
    assert "segredo" not in capsys.readouterr().out


@pytest.mark.parametrize("failure", [ValueError, RuntimeError, OSError, KeyboardInterrupt])
def test_preparation_failure_preserves_output(monkeypatch, paths, failure, capsys):
    """Preserva a saída e oculta detalhes em falhas ou cancelamento da preparação."""
    paths.write_bytes(b"previous")

    def interrupted(*args):
        """Interrompe a preparação antes de qualquer abertura para escrita."""
        raise failure("segredo")

    monkeypatch.setattr(cli, "load_deduplication_inputs", interrupted)
    assert cli.main(["deduplicate", "--overwrite"]) == (1 if failure is KeyboardInterrupt else 2)
    assert paths.read_bytes() == b"previous"
    assert "segredo" not in capsys.readouterr().out


@pytest.mark.parametrize("kind", ["directory", "parent"])
def test_invalid_output_location(monkeypatch, paths, kind):
    """Recusa diretório de saída ou ancestral que seja arquivo antes da leitura."""
    if kind == "directory":
        paths.mkdir()
    else:
        paths.write_bytes(b"parent")
        monkeypatch.setattr(config, "DEDUPLICATION_OUTPUT_PATH", paths / "output.jsonl")
    monkeypatch.setattr(cli, "load_deduplication_inputs", forbidden)
    assert cli.main(["deduplicate", "--overwrite"]) == 2


@pytest.mark.parametrize("input_name", ["DEDUPLICATION_INPUT_PATH", "DEDUPLICATION_EXTRACTION_PATH"])
@pytest.mark.parametrize("alias", ["same", "hardlink"])
def test_input_alias_is_protected(monkeypatch, paths, input_name, alias):
    """Recusa identidade de caminho ou hardlink com qualquer uma das entradas."""
    original = getattr(config, input_name)
    original.write_bytes(b"input")
    if alias == "same":
        monkeypatch.setattr(config, "DEDUPLICATION_OUTPUT_PATH", original)
    else:
        paths.hardlink_to(original)
    monkeypatch.setattr(cli, "load_deduplication_inputs", forbidden)
    assert cli.main(["deduplicate", "--overwrite"]) == 2
    assert original.read_bytes() == b"input"


@pytest.mark.parametrize("failure", [OSError, KeyboardInterrupt])
def test_write_failure_or_cancel(monkeypatch, paths, tmp_path, extraction_rows, failure, capsys):
    """Informa falha ou interrupção durante a escrita e retorna código operacional."""
    empty_inputs(tmp_path, extraction_rows)

    def interrupted(path, records, **kwargs):
        """Simula interrupção após o início da publicação."""
        path.write_bytes(b"partial")
        raise failure()

    monkeypatch.setattr(cli, "write_jsonl", interrupted)
    assert cli.main(["deduplicate"]) == 1
    assert "incompleto" in capsys.readouterr().out


@pytest.mark.parametrize("option", [["--workers", "1"], ["--limit", "1"], ["--input", "a"], ["--output", "b"]])
def test_unsupported_options(paths, option):
    """Recusa caminhos, filtros e paralelismo fora do contrato."""
    with pytest.raises(SystemExit) as error:
        cli.main(["deduplicate", *option])
    assert error.value.code == 2 and not paths.exists()
