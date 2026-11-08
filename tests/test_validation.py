"""Protege decisões, contagem e persistência contra perda de prova na publicação."""

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from robo_minerador import validation
from robo_minerador.jsonl import write_jsonl
from robo_minerador.validation_evidence import evaluate_repository, validate_events, case_identity, decode_events
from robo_minerador.validation_evaluation import _hash, resource_audit_reference
from robo_minerador.validation_format import validate_output_records, encode_bytes
from robo_minerador.validation_recipe import load_recipes, resolve_recipe


def controlled_fixture(counts=(4, 3)):
    """Produz contexto controlado e traces T3 validados de duas funções distintas.

    Títulos iguais, parametrização e duas chamadas por caso tornam inflação e fusão
    observáveis; nenhuma fonte ou configuração de candidato real é executada.
    """
    repository = {"repository_id": 1, "repository_url": "https://github.com/fixture/unit", "commit_sha": "a" * 40}
    recipe = deepcopy(next(iter(load_recipes()["recipes"].values())))
    recipe.update(repository_id=1, repository_url=repository["repository_url"], commit_sha=repository["commit_sha"])
    recipe["phases"]["preparation"] = []
    recipe["unit_scope"] = {"status": "approved", "code": None,
        "packages": [{"path": ".", "name": "controlled-unit-fixture", "scope": "Único arquivo a.test.ts da fixture."}],
        "evidence": ["package.json", "vitest.config.ts"], "external_services": {"status": "not_evaluated",
            "message": "Recursos exigem a auditoria explícita da fixture.", "evidence": ["a.test.ts"]}}
    recipe["resolution"] = {"code": None, "message": "Receita somente da fixture; executor simulado, sem comandos candidatos.", "evidence": ["package.json"]}
    recipe["evidence"] = [{"path": path, "detail": "Arquivo controlado da fixture unitária."}
                          for path in ("package.json", "package-lock.json", "vitest.config.ts", "a.test.ts")]
    units, declarations, points, targets = [], {}, {}, {}
    identity = {"protocol_version": 1, "session_id": "fixture", "repository_id": 1, "commit_sha": "a" * 40, "instrument_id": "b" * 64}
    events = [{**identity, "emitter_id": "worker", "sequence": 1, "event": "run_started", "role": "worker", "tools": {"vitest": "4.1.10"}}]
    for ordinal, count in enumerate(counts):
        instance_id = hashlib.sha256(str(ordinal).encode()).hexdigest()
        declaration_id, point_id = "declaration-" + str(ordinal), "point-" + str(ordinal)
        association = {"kind": "direct_call", "call_line": ordinal + 2, "expression": "compute" + str(ordinal),
                       "resolved_target": {"path": "target.ts", "start_line": ordinal + 1, "name": "compute" + str(ordinal)}}
        test = {"path": "a.test.ts", "start_line": ordinal + 2, "end_line": ordinal + 2, "framework": "vitest", "modifiers": [], "association": association}
        units.append({"instance_id": instance_id, "function": {"path": "target.ts", "start_line": ordinal + 1, "end_line": ordinal + 1}, "tests": [test]})
        declarations[declaration_id] = {"id": declaration_id, "file": "a.test.ts", "start": ordinal * 100, "end": ordinal * 100 + 99, "modifiers": [], "associated": True}
        points[point_id] = {"id": point_id, "instance_id": instance_id, "declaration_id": declaration_id,
                            "path": "a.test.ts", "start": ordinal * 100 + 10, "end": ordinal * 100 + 20, "association": association}
        targets[instance_id] = {"id": instance_id, "path": "target.ts", "start": ordinal * 50, "end": ordinal * 50 + 49}
        for occurrence in range(count):
            value = {"project_id": "unit", "file": "a.test.ts", "declaration_id": declaration_id, "occurrence": occurrence,
                     "worker_id": "worker", "runtime_id": f"{ordinal}-{occurrence}", "title": "igual ç", "modifiers": []}
            case_id = case_identity(value)
            rows = [{"event": "case_started", "case_id": case_id, "case": value}]
            for call in range(2):
                token = f"{ordinal}-{occurrence}-{call}"
                rows.extend([{"event": "direct_call", "case_id": case_id, "attempt": 0, "call_id": token, "point_id": point_id, "instance_id": instance_id},
                             {"event": "target_entered", "case_id": case_id, "attempt": 0, "call_id": token, "instance_id": instance_id}])
            rows.append({"event": "case_finished", "case_id": case_id, "status": "passed", "attempts": [
                {"index": 0, "retry": 0, "repeat": 0, "status": "passed", "expected_failure": False}]})
            for row in rows:
                events.append({**identity, "emitter_id": "worker", "sequence": len(events) + 1, **row})
    events.append({**identity, "emitter_id": "worker", "sequence": len(events) + 1, "event": "run_finished", "complete": True, "emitter_ids": [], "limitations": []})
    events.extend([{**identity, "emitter_id": "main", "sequence": 1, "event": "run_started", "role": "coordinator", "tools": {}},
                   {**identity, "emitter_id": "main", "sequence": 2, "event": "run_finished", "complete": True, "emitter_ids": ["worker", "main"], "limitations": []}])
    expected = {**identity, "emitters": {"worker": "worker", "main": "coordinator"}, "census_complete": True, "declarations": declarations, "points": points}
    evidence = validate_events(events, expected)
    target_source = "".join(f"export function compute{ordinal}(x){{return x+1;}}\n" for ordinal in range(len(counts)))
    test_source = "import {test,expect} from 'vitest'; import {" + ",".join(f"compute{i}" for i in range(len(counts))) + "} from './target';\n"
    for ordinal, count in enumerate(counts):
        test_source += f"test.each({json.dumps(list(range(count)))})('igual ç',/** Exercita duas chamadas diretas. */(value)=>{{for(const repeat of [0,1]){{expect(compute{ordinal}(value)).toBe(value+1);}}}});\n"
    package_source = json.dumps({"name": "controlled-unit-fixture", "version": "1.0.0", "scripts": {"test": "vitest run"},
                                "devDependencies": {"vitest": recipe["tools"]["vitest"]}})
    lock_source = json.dumps({"name": "controlled-unit-fixture", "version": "1.0.0", "lockfileVersion": 3, "packages": {}})
    recipe["lockfiles"][0]["sha256"] = hashlib.sha256(lock_source.encode()).hexdigest()
    files = [{"path": "target.ts", "content": target_source}, {"path": "a.test.ts", "content": test_source},
             {"path": "package.json", "content": package_source}, {"path": "package-lock.json", "content": lock_source},
             {"path": "vitest.config.ts", "content": "export default {test:{include:['a.test.ts']}};\n"}]
    for ordinal, unit in enumerate(units):
        target_line = target_source.splitlines()[ordinal]
        target_start = sum(len(line) + 1 for line in target_source.splitlines()[:ordinal])
        targets[unit["instance_id"]].update(start=target_start, end=target_start + len(target_line))
    environment = {"tools": recipe["tools"], "image": recipe["image"], "platform": recipe["platform"],
        "image_id": "sha256:" + "1" * 64, "os_release": "ID=debian\n", "daemon": {"OSType": "linux", "ServerVersion": "fixture"},
        "dependencies": {"packages": [{"path": "node_modules/vitest/package.json", "name": "vitest", "version": recipe["tools"]["vitest"]}]},
        "lockfiles": [{**lock, "effective_sha256": lock["sha256"], "matches_original": True} for lock in recipe["lockfiles"]]}
    environment["resource_bindings"] = {"vitest": [{"root": "/workspace/node_modules/vitest", "version": "4.1.10",
        "tree_sha256": "d3fd24af92fabda62731814693ab1fa47a77c91b1b48a7be343c45bf5213780e"}]}
    environment["resource_bindings"]["vite"] = [{"root": "/workspace/node_modules/vite", "version": "7.3.6",
        "tree_sha256": "d8fb0c20419149231ae0828a36d08c44bc38863ce5d6971468c7617e221fa527"}]
    phases = {phase: {"phase": phase, "status": "completed", "required": phase != "preparation", "commands": [
        {"argv": recipe["phases"][phase][0]["argv"], "cwd": ".", "exitcode": 0, "stdout": encode_bytes(b"ok\n"), "stderr": encode_bytes(b"")}]
        if recipe["phases"][phase] else []} for phase in ("installation", "preparation", "unit_suite")}
    tasks = [{"id": event["case"]["runtime_id"], "file": "a.test.ts", "project_id": "unit", "result": {"state": "passed"}}
             for event in events if event["event"] == "case_started"]
    observation = {"complete": True, "instrument_sha256": "b" * 64, "settings_sha256": "c" * 64,
                   "plan": {"points": points, "targets": targets, "declarations": declarations, "limitations": []},
                   "installations": [{"root": "/workspace/node_modules/vitest", "vite_root": "/workspace/node_modules/vite",
                                      "tools": {"vitest": "4.1.10", "vite": "7.3.6"}}],
                   "commands": [{"complete": True, "evidence": evidence, "census": {"complete": True},
                       "report": {"complete": True, "original_exit": {"code": 0, "signal": None}, "transforms": [{"path": "target.ts"}],
                                  "suites": [{"suite_id": "suite", "root": "/workspace", "cwd": "/workspace", "terminal": True,
                                      "planned_modules": [{"file": "a.test.ts", "project_id": "unit"}],
                                       "modules": [{"id": "module", "file": "a.test.ts", "project_id": "unit", "worker_id": "worker"}],
                                       "errors": [], "tasks": tasks, "module_results": [{"id": "module", "state": "passed", "errors": []}], "coverage": None}]}}]}
    group = {"repository": repository, "instances": [{"instance_id": u["instance_id"]} for u in units], "reasons": {}}
    verified = {"snapshot": {"repository_id": 1, "commit_sha": "a" * 40, "format": "git-bare-v1", "cache_key": "1/" + "a" * 40,
                             "tree_oid": "2" * 40, "manifest_sha256": "3" * 64, "status": "complete", "file_count": len(files),
                             "total_blob_bytes": sum(len(f["content"].encode()) for f in files)}, "context": {"files": files, "analysis_complete": True},
                "instances": {u["instance_id"]: u for u in units}, "execution_requirements": []}
    observation["plan"]["plans"] = [{"path": file["path"], "sha256": hashlib.sha256(file["content"].encode()).hexdigest()}
                                    for file in files if file["path"].endswith(".ts")]
    observation["commands"][0]["report"]["transforms"] = [{**item, "suite_id": "suite"} for item in observation["plan"]["plans"]]
    observation["commands"][0]["report"]["resource_modules"] = [
        {"path": file["path"], "sha256": hashlib.sha256(file["content"].encode()).hexdigest(), "origin": "vite_transform", "suite_id": "suite"}
        for file in files if file["path"].endswith(".ts")]
    bind_resource_snapshot(verified)
    suite = observation["commands"][0]["report"]["suites"][0]
    suite["resource_installation"] = {"vitest_root": "/workspace/node_modules/vitest", "vite_root": "/workspace/node_modules/vite"}
    suite["resource_contexts"] = [{"file": "a.test.ts", "project_id": "unit", "root": ".", "pool": "forks", "isolate": True,
        "environment": "node", "config_file": "vitest.config.ts", "config_dependencies": ["vitest.config.ts"],
        "config_sources": [{"path": file["path"], "sha256": hashlib.sha256(file["content"].encode()).hexdigest()}
                           for file in files if file["path"] == "vitest.config.ts"], "setup_files": [], "global_setup": []}]
    suite["resource_root_configuration"] = suite["resource_contexts"][0]
    from robo_minerador.validation_resources import produce_resource_audit
    audit = produce_resource_audit(verified, recipe, observation, environment)
    execution = {"verified": verified, "environment": environment, "phases": phases, "observation": observation, "resource_evidence": audit}
    from robo_minerador.validation_resources_local import produce_instance_resource_audits
    execution.update(produce_instance_resource_audits(verified, recipe, observation, environment, audit))
    catalog = {"recipes": {(1, "a" * 40): recipe}, "sha256": "d" * 64}
    source = {"path": "fixture.jsonl", "sha256": "e" * 64, "schema_version": 3, "status": "partial", "input_count": len(units),
              "repository_count": 1, "eligible_count": len(units), "eligible_repository_count": 1,
              "decisions": {"approved": len(units), "rejected": 0, "not_evaluated": 0}}
    return group, catalog, source, execution


def bind_resource_snapshot(verified):
    """Vincula os bytes controlados a um manifesto canônico completo da fixture."""
    files = verified["context"]["files"]
    entries = [{"path": file["path"], "type": "blob", "mode": "100644", "size": len(file["content"].encode()),
                "oid": hashlib.sha1(b"blob " + str(len(file["content"].encode())).encode() + b"\0" + file["content"].encode()).hexdigest()}
               for file in files]
    snapshot = verified["snapshot"]
    manifest = {"format": "git-bare-v1", **{key: snapshot[key] for key in ("repository_id", "commit_sha", "tree_oid")}, "entries": entries}
    verified["resource_manifest"] = manifest
    snapshot.update(manifest_sha256=_hash(manifest), file_count=len(files), total_blob_bytes=sum(e["size"] for e in entries))


def attach_function_coverage(execution):
    """Acrescenta provider Istanbul controlado com source map e hash original verificáveis."""
    instance_id = next(iter(execution["verified"]["instances"]))
    source = execution["verified"]["context"]["files"][0]["content"]
    target = execution["observation"]["plan"]["targets"][instance_id]
    end = {"line": 1, "column": target["end"]}
    provider = {"path": "/workspace/target.ts", "inputSourceMap": {"sources": ["target.ts"], "sourcesContent": [source]},
        "fnMap": {"0": {"decl": {"start": {"line": 1, "column": 7}}, "loc": {"start": {"line": 1, "column": source.index("{")}, "end": end}}},
        "statementMap": {"0": {"start": {"line": 1, "column": source.index("return")}, "end": {"line": 1, "column": source.index("}")}}},
        "s": {"0": 1}, "branchMap": {}, "b": {}}
    execution["observation"]["commands"][0]["report"]["suites"][0]["coverage"] = {"/workspace/target.ts": provider}
    hashes = [{"path": file["path"], "sha256": hashlib.sha256(file["content"].encode()).hexdigest()}
              for file in execution["verified"]["context"]["files"] if file["path"].endswith(".ts")]
    execution["observation"]["plan"]["plans"] = hashes
    execution["observation"]["commands"][0]["report"]["transforms"] = [{**item, "suite_id": "suite"} for item in hashes]
    return provider


def reseal_fixture(execution):
    """Reconstrói protocolo controlado após remoção legítima de eventos da fixture."""
    observation = execution["observation"]
    command = observation["commands"][0]
    evidence = command["evidence"]
    events = []
    for state in evidence["emitters"].values():
        events.extend(event for event in (state["started"], state["finished"]) if event)
    for case in evidence["cases"].values():
        events.extend(event for event in (case["started"], case["finished"]) if event)
    for call in evidence["calls"]:
        events.extend(event for event in (call["direct"], call["entered"]) if event)
    events.sort(key=lambda event: (event["emitter_id"], event["sequence"]))
    sequences = {}
    for event in events:
        key = event["emitter_id"]
        sequences[key] = sequences.get(key, 0) + 1
        event["sequence"] = sequences[key]
    first = events[0]
    expected = {key: first[key] for key in ("session_id", "repository_id", "commit_sha", "instrument_id")}
    expected.update(emitters={key: state["started"]["role"] for key, state in evidence["emitters"].items()},
                    census_complete=True, declarations=observation["plan"]["declarations"], points=observation["plan"]["points"])
    command["evidence"] = validate_events(events, expected)


class FixtureExecutor:
    """Simula somente infraestrutura controlada com logs destruídos no fechamento."""

    def __init__(self, execution):
        """Retém evidências para exercitar as chamadas reais do coordenador."""
        self.execution = execution
        self.daemon = {"OSType": "linux"}
        self.events, self.calls, self.cleanup_errors = [], [], []
        self.closed = False

    def run_phase(self, workspace, phase, recipe):
        """Registra uma passagem por fase e expõe spool binário antes de close."""
        self.calls.append(phase)
        result = deepcopy(self.execution["phases"][phase])
        for command in result["commands"]:
            command.pop("stdout", None)
            command.pop("stderr", None)
            command.update(stdout_path="temporary.stdout", stderr_path="temporary.stderr")
        return result

    def read_log(self, event, stream):
        """Recusa leitura tardia para detectar dependência de arquivos temporários."""
        assert not self.closed
        return b"ok\xff" if stream == "stdout" else b""

    def observe_resources(self, workspace, verified, recipe, observation):
        """Produz fechamento local real, mantendo a auditoria global original da fixture."""
        from robo_minerador.validation_resources_local import produce_instance_resource_audits
        shared = self.execution["resource_evidence"]
        return {"resource_evidence": shared, **produce_instance_resource_audits(verified, recipe, observation, self.execution["environment"], shared)}

    def cancel(self):
        """Registra cancelamento do gerador antes de seu fechamento."""
        self.calls.append("cancel")

    def close(self):
        """Invalida spools para obrigar incorporação anterior dos bytes."""
        self.closed = True


def published_fixture(monkeypatch, tmp_path, *, fail_first=False, counts=(4, 3), fail_all=False, mutate=None, policy_version=1):
    """Usa coordenador e escritor reais; substitui somente fronteiras T1/T2/T3."""
    group, catalog, source, execution = controlled_fixture(counts)
    if mutate is not None:
        mutate(execution)
    executor = FixtureExecutor(execution)
    monkeypatch.setattr(validation, "load_repository_context", lambda *args: execution["verified"])
    monkeypatch.setattr(validation, "prepare_workspace", lambda *args: SimpleNamespace(environment=execution["environment"]))
    monkeypatch.setattr(validation, "configure_instrument", lambda *args: SimpleNamespace(collect=lambda *args: execution["observation"]))
    groups = [group]
    if fail_first:
        broken = deepcopy(group)
        broken["repository"]["repository_id"] = 2
        broken["instances"] = [{"instance_id": "f" * 64}]
        groups.insert(0, broken)
        source.update(eligible_count=3, eligible_repository_count=2, input_count=3, repository_count=2)
        source["decisions"]["approved"] = 3

        def verify(instances, repository, reasons, store):
            """Falha uma origem sem impedir a seguinte."""
            if repository["repository_id"] == 2:
                raise ValueError("Snapshot controlado ausente.")
            return execution["verified"]

        monkeypatch.setattr(validation, "load_repository_context", verify)
    if fail_all:
        def unavailable(*args):
            """Recusa contexto para verificar failed sem critérios úteis fabricados."""
            raise ValueError("Snapshot controlado indisponível.")

        monkeypatch.setattr(validation, "load_repository_context", unavailable)
    output = tmp_path / "validation.jsonl"
    write_jsonl(output, validation.validate_repositories(groups, source, catalog, executor, object(), policy_version=policy_version), ensure_ascii=True, compact=True)
    rows = decode_events(output.read_bytes())
    return rows, executor, output


def test_writer_retains_two_decisions_and_every_witness(monkeypatch, tmp_path):
    """Reabrir JSONL comprova limiar 3/4, parametrização, bytes e vínculos originais."""
    rows, executor, output = published_fixture(monkeypatch, tmp_path)
    final = validate_output_records(rows)
    assert final["status"] == "completed"
    assert final["instance_decisions"] == {"approved": 1, "rejected": 1, "not_evaluated": 0}
    assert executor.closed and executor.calls == ["installation", "preparation", "unit_suite"]
    assert output.read_bytes().isascii() and b"\\u00e7" in output.read_bytes()
    repository = rows[1]
    assert repository["execution"]["installation"]["commands"][0]["stdout"] == encode_bytes(b"ok\xff")
    for instance, count in zip(rows[2:4], (4, 3)):
        assert instance["test_cases"]["valid_count"] == count
        assert len(instance["case_evidence"]) == count
        assert all(case["points"][0]["association"]["resolved_target"]["path"] == "target.ts" for case in instance["case_evidence"])
        assert instance["coverage"]["associated_tests"]["lines"] is None


def test_writer_coverage_requires_provider_measurement_and_original_source(monkeypatch, tmp_path):
    """Aceita medição global rastreável e recusa percentual alterado após o roundtrip."""
    rows, _, _ = published_fixture(monkeypatch, tmp_path, counts=(4,), mutate=attach_function_coverage)
    assert validate_output_records(rows)["status"] == "completed"
    lines = rows[2]["coverage"]["full_unit_suite"]["lines"]
    assert lines["percentage"] == 100
    lines.update(covered=0, percentage=0)
    with pytest.raises(ValueError):
        validate_output_records(rows)


@pytest.mark.parametrize("corruption", ["audit_subset", "executed_paths", "source_hash", "universe_removed"])
def test_resource_approval_requires_entire_portable_universe(monkeypatch, tmp_path, corruption):
    """Reduzir auditoria ou romper hashes/caminhos não preserva aprovação após publicação."""
    rows, _, _ = published_fixture(monkeypatch, tmp_path)
    assert validate_output_records(rows)["status"] == "completed"
    repository = rows[1]
    audit = repository["resource_evidence"]
    if corruption == "audit_subset":
        audit["files"] = audit["files"][:1]
        audit["executed_paths"] = []
    elif corruption == "executed_paths":
        audit["executed_paths"] = []
    elif corruption == "source_hash":
        audit["files"][0]["sha256"] = "0" * 64
    else:
        repository.pop("resource_universe")
    # Mantém todas as cópias da alegação de aprovação coerentes: o erro é a
    # prova insuficiente, não uma simples diferença entre referências copiadas.
    repository["criteria"]["no_external_services"]["evidence"] = resource_audit_reference(audit)
    for record in rows:
        if record["record_type"] == "instance":
            record["criteria"]["no_external_services"]["evidence"] = resource_audit_reference(audit)
    with pytest.raises(ValueError):
        validate_output_records(rows)


@pytest.mark.parametrize("gap", ["command", "evidence", "census", "report", "instance_limitation"])
def test_incomplete_observation_cannot_publish_exact_rejection(monkeypatch, tmp_path, gap):
    """Uma contagem de três testemunhas não vira exata por editar decisão e totais."""
    def incomplete(execution):
        """Introduz lacuna real em um dos fechamentos ou na associação localizada."""
        observation = execution["observation"]
        command = observation["commands"][0]
        if gap == "instance_limitation":
            observation["plan"]["limitations"] = [{"instance_id": next(iter(execution["verified"]["instances"])), "reason": "Forma não comprovada."}]
        elif gap == "command":
            command["complete"] = False
        else:
            command[gap]["complete"] = False

    rows, _, _ = published_fixture(monkeypatch, tmp_path, counts=(3,), mutate=incomplete)
    assert validate_output_records(rows)["status"] == "partial"
    instance = rows[2]
    assert instance["criteria"]["minimum_valid_cases"]["status"] == "not_evaluated"
    assert instance["criteria"]["original_target_execution"]["status"] == ("not_evaluated" if gap == "instance_limitation" else "approved")
    instance["test_cases"].update(counting_complete=True, count_kind="exact")
    instance["criteria"]["minimum_valid_cases"]["status"] = "rejected"
    instance["criteria"]["minimum_valid_cases"]["evidence"]["counting_complete"] = True
    instance["criteria"]["original_target_execution"]["status"] = "approved"
    instance["decision"] = "rejected"
    decisions = {"approved": 0, "rejected": 1, "not_evaluated": 0}
    rows[1]["instance_decisions"] = deepcopy(decisions)
    rows[-1]["instance_decisions"] = deepcopy(decisions)
    if gap == "instance_limitation":
        rows[-1]["status"] = "completed"
    with pytest.raises(ValueError):
        validate_output_records(rows)


@pytest.mark.parametrize("corruption", ["bare", "policy", "limits", "versions"])
def test_run_started_requires_reproduction_contract(monkeypatch, tmp_path, corruption):
    """Abertura sem contrato, política alterada ou limites alheios não fecha execução válida."""
    rows, _, _ = published_fixture(monkeypatch, tmp_path)
    started = rows[0]
    if corruption == "bare":
        for key in ("policy", "limits", "miner_version", "instrument_protocol_version"):
            started.pop(key)
    elif corruption == "policy":
        started["policy"]["suite_passes"] = True
    elif corruption == "limits":
        started["limits"][0]["limits"]["memory_bytes"] *= 2
    else:
        started["miner_version"] = ""
        started["instrument_protocol_version"] = True
    with pytest.raises(ValueError):
        validate_output_records(rows)


def test_zero_requires_no_execution_dependencies(monkeypatch):
    """Entrada vazia conclui sem construir Docker, Git ou analisador."""
    _, _, source, _ = controlled_fixture(())
    source.update(repository_count=0, eligible_repository_count=0)

    def forbidden(*args, **kwargs):
        """Detecta construção indevida de dependência externa."""
        raise AssertionError("Dependência desnecessária.")

    monkeypatch.setattr(validation, "DockerExecutor", forbidden)
    monkeypatch.setattr(validation, "SnapshotStore", forbidden)
    rows = list(validation.validate_repositories([], source, {}))
    assert validate_output_records(rows)["status"] == "completed"


def test_failed_group_preserves_next_valid_group(monkeypatch, tmp_path):
    """Falha operacional local mantém a população e os resultados independentes."""
    rows, executor, _ = published_fixture(monkeypatch, tmp_path, fail_first=True)
    assert validate_output_records(rows)["status"] == "partial"
    assert rows[-1]["instance_count"] == 3 and rows[-1]["instance_decisions"]["approved"] == 1


@pytest.mark.parametrize("fail_all,expected", [(False, "completed"), (True, "failed")])
def test_all_scientific_rejections_complete_but_no_useful_evaluation_fails(monkeypatch, tmp_path, fail_all, expected):
    """Estado da etapa não confunde rejeição integral com falha do minerador."""
    rows, _, _ = published_fixture(monkeypatch, tmp_path, counts=(3, 2), fail_all=fail_all)
    assert validate_output_records(rows)["status"] == expected
    assert rows[-1]["instance_decisions"]["rejected"] == (0 if fail_all else 2)


def test_closing_interrupted_generator_cancels_and_does_not_close_injected_store(monkeypatch):
    """Falha de escrita/cancelamento não publica fechamento nem abandona executor próprio."""
    group, catalog, source, execution = controlled_fixture()
    executor = FixtureExecutor(execution)
    store = SimpleNamespace()
    records = validation.validate_repositories([group], source, catalog, executor, store)
    assert next(records)["record_type"] == "run_started"
    records.close()
    assert executor.closed and executor.calls == ["cancel"]


@pytest.mark.parametrize("impediment", ["oom", "capture_loss", "protocol"])
def test_scientific_rejection_preserves_simultaneous_operational_failure(monkeypatch, tmp_path, impediment):
    """Falha de teste antes da interrupção rejeita a origem, mas não apaga impedimentos."""
    def interrupted(execution):
        """Preserva falha original alinhada ao reporter antes da perda operacional."""
        observation = execution["observation"]
        command = observation["commands"][0]
        case = next(iter(command["evidence"]["cases"].values()))
        case["finished"]["status"] = "failed"
        case["finished"]["attempts"][0]["status"] = "failed"
        command["report"]["suites"][0]["tasks"][0]["result"]["state"] = "failed"
        observation["complete"] = command["complete"] = command["report"]["complete"] = False
        phase = execution["phases"]["unit_suite"]
        if impediment == "protocol":
            command["evidence"]["complete"] = False
            command["evidence"]["limitations"] = ["Emissor sem fechamento após a falha."]
        else:
            phase["status"] = "failed"
            phase["commands"][0].update(exitcode=137)
            phase["commands"][0][impediment] = True if impediment == "oom" else "Captura interrompida."
            command["report"]["original_exit"]["code"] = 137

    rows, _, _ = published_fixture(monkeypatch, tmp_path, mutate=interrupted)
    final = validate_output_records(rows)
    assert rows[1]["decision"] == "rejected" and final["instance_decisions"]["rejected"] == 1
    assert final["operational_errors"] > 0 and final["status"] == "partial"
    # Remover diagnóstico e ajustar totais não pode reclassificar como completed.
    stripped = [r for r in rows if r["record_type"] != "diagnostic"]
    stripped[-1].update(operational_errors=0, status="completed")
    with pytest.raises(ValueError):
        validate_output_records(stripped)


@pytest.mark.parametrize("corruption", ["schema", "reference", "duplicate", "count", "decision", "case", "target", "source", "end", "bytes",
                                        "environment", "snapshot", "environment_identity", "associated_coverage", "global_coverage"])
def test_output_validator_refuses_structural_or_scientific_corruption(monkeypatch, tmp_path, corruption):
    """Recusa alterações que poderiam fabricar aprovações ou apagar proveniência."""
    rows, _, _ = published_fixture(monkeypatch, tmp_path)
    if corruption == "schema":
        rows[2]["schema_version"] = True
    elif corruption == "reference":
        rows[2]["repository_id"] = 20
    elif corruption == "duplicate":
        rows[3]["instance_id"] = rows[2]["instance_id"]
    elif corruption == "count":
        rows[2]["test_cases"]["valid_count"] = 8
    elif corruption == "decision":
        rows[3]["decision"] = "approved"
    elif corruption == "case":
        rows[2]["case_evidence"].append(deepcopy(rows[2]["case_evidence"][0]))
    elif corruption == "target":
        rows[2]["case_evidence"][0]["target"]["path"] = "mock.ts"
    elif corruption == "source":
        rows[0]["source"]["sha256"] = "bad"
    elif corruption == "end":
        rows.pop()
    elif corruption == "bytes":
        rows[1]["execution"]["installation"]["commands"][0]["stdout"]["data"] = "AA=="
    elif corruption == "environment":
        rows[1]["environment"] = {}
    elif corruption == "snapshot":
        rows[1]["snapshot"] = None
    elif corruption == "environment_identity":
        rows[1]["environment"]["tools"]["node"] = "0.0.0"
    else:
        scope = "associated_tests" if corruption == "associated_coverage" else "full_unit_suite"
        rows[2]["coverage"][scope] = {"status": "available", "scope": scope, "reason": None,
            "lines": {"status": "available", "covered": 1, "total": 1, "percentage": 100},
            "branches": {"status": "available", "covered": 1, "total": 1, "percentage": 100}}
    with pytest.raises(ValueError):
        validate_output_records(rows)
