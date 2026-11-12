"""Protege seleção e ligação entre as publicações de entrada."""

from copy import deepcopy
import hashlib
import json
import subprocess

import pytest

from robo_minerador import deduplication_input as reader
from robo_minerador.validation_evaluation import PAIR_POLICY


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    """Isola o vínculo entre origens com unidades e planos controlados."""
    code = "😀\nfunction f() {}"
    test_code = "test('x', () => f());"
    test = {"path": "t.ts", "start_line": 1, "end_line": 1,
            "association": {"kind": "direct_call", "call_line": 1, "expression": "f",
                            "resolved_target": {"path": "a.ts", "start_line": 2, "name": "f"}}}
    units = [{"instance_id": str(i) * 64, "repository_id": 1, "tests": [deepcopy(test)],
              "function": {"path": "a.ts", "start_line": 2, "end_line": 2, "reference_code": "function f() {}"}}
             for i in (1, 2, 3)]
    files = {"a.ts": code, "t.ts": test_code}
    repo = {"repository_id": 1, "repository_url": "https://github.com/a/b", "commit_sha": "a" * 40,
            "context": {"files": [{"path": p, "content": c} for p, c in files.items()]}}
    source = {"path": "old", "sha256": "b" * 64, "schema_version": 3, "status": "partial", "input_count": 3,
              "repository_count": 1, "eligible_count": 3, "eligible_repository_count": 1,
              "decisions": {"approved": 3, "rejected": 0, "not_evaluated": 0}}
    target = {"id": units[0]["instance_id"], "path": "a.ts", "start": 3, "end": 18}
    point = {"path": "t.ts", "start": 16, "end": 19, "association": test["association"]}
    declaration = {"file": "t.ts", "start": 0, "end": len(test_code) - 1}
    case = {"declaration": declaration, "points": [point], "original_tests": [test]}
    plan = {"targets": {units[0]["instance_id"]: target}, "plans": [
        {"path": p, "sha256": hashlib.sha256(c.encode()).hexdigest()} for p, c in files.items()]}
    records = [{"record_type": "run_started", "schema_version": 3, "policy": deepcopy(PAIR_POLICY), "source": deepcopy(source)},
               {"record_type": "repository", "schema_version": 3, **{k: repo[k] for k in ("repository_id", "repository_url", "commit_sha")},
                "decision": "rejected", "unit_suite": {"plan": plan}}]
    for unit, decision in zip(units, ("approved", "rejected", "not_evaluated")):
        records.append({"record_type": "instance", "schema_version": 3, "repository_id": 1,
                        "instance_id": unit["instance_id"], "decision": decision,
                        "test_cases": {"associations": [{"test": test}] if decision != "not_evaluated" else []},
                        "case_evidence": [case] if decision == "approved" else []})
    records.append({"record_type": "run_finished", "schema_version": 3, "status": "partial"})
    monkeypatch.setattr(reader, "load_extraction", lambda path: ([{"repository": repo, "instances": units, "reasons": {}}], source))
    monkeypatch.setattr(reader, "expand_instance", lambda instance, repository, reasons: instance)
    monkeypatch.setattr(reader, "validate_output_records", lambda rows: rows[-1])

    def run(rows=records):
        """Publica bytes temporários e executa a fronteira de seleção."""
        path = tmp_path / "validation.jsonl"
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        return reader.load_deduplication_inputs(path, tmp_path / "extraction.jsonl")
    return records, run


def test_selection_and_utf16(boundary):
    """Seleciona apenas aprovada mesmo com origem parcial e repositório rejeitado."""
    rows, run = boundary
    candidates, source = run()
    assert [item["unit"]["instance_id"] for item in candidates] == [rows[2]["instance_id"]]
    assert source["input_decisions"] == {"approved": 1, "rejected": 1, "not_evaluated": 1}
    assert source["sources"]["validation"]["status"] == source["sources"]["extraction"]["status"] == "partial"


@pytest.mark.parametrize("call,accepted", [("f()", True), ("f /* nota */ <T> ()", True),
                                            ("f ?. /* nota */ (1)", True), ("foo()", False),
                                            ("f.bar()", False), ("f?.bar()", False),
                                            ("f<T>.foo()", False)])
def test_credited_call_expression_boundary(boundary, call, accepted):
    """Distingue a expressão original de chamadas com prefixo textual idêntico."""
    rows, run = boundary
    groups, _ = reader.load_extraction(None)
    test_file = groups[0]["repository"]["context"]["files"][1]
    content = "test('x', () => { f(); " + call + "; });"
    test_file["content"] = content
    plan = rows[1]["unit_suite"]["plan"]
    plan["plans"][1]["sha256"] = hashlib.sha256(content.encode()).hexdigest()
    case = rows[2]["case_evidence"][0]
    case["declaration"]["end"] = len(content) - 1
    point = case["points"][0]
    point["start"] = content.index(call, content.index("f();") + 4)
    point["end"] = point["start"] + len(call)
    if accepted:
        assert len(run()[0]) == 1
    else:
        with pytest.raises(ValueError):
            run()


@pytest.mark.parametrize("content,accepted", [
    ("test(f(), () => {});", False),
    ("test('x', () => { const later = () => f(); });", False),
    ("test('x', () => { f(); });", True),
])
def test_credited_point_in_direct_callback(boundary, content, accepted):
    """Exige chamada no callback imediato, não em título ou função diferida."""
    rows, run = boundary
    groups, _ = reader.load_extraction(None)
    groups[0]["repository"]["context"]["files"][1]["content"] = content
    rows[1]["unit_suite"]["plan"]["plans"][1]["sha256"] = hashlib.sha256(content.encode()).hexdigest()
    case = rows[2]["case_evidence"][0]
    case["declaration"]["end"] = len(content) - 1
    case["points"][0]["start"] = content.index("f()")
    case["points"][0]["end"] = content.index("f()") + 3
    if accepted:
        assert len(run()[0]) == 1
    else:
        with pytest.raises(ValueError, match="vínculos sintáticos"):
            run()


@pytest.mark.parametrize("index", [2, 3, 4])
@pytest.mark.parametrize("damage", ["order", "multiplicity"])
def test_association_order_and_multiplicity(boundary, index, damage):
    """Exige sequência e multiplicidade também em decisões sem aprovação."""
    rows, run = boundary
    groups, _ = reader.load_extraction(None)
    unit = groups[0]["instances"][index - 2]
    first = deepcopy(unit["tests"][0])
    second = deepcopy(first)
    second["association"]["expression"] = "other"
    unit["tests"] = [first, second, first]
    rows[index]["test_cases"]["associations"] = [{"test": deepcopy(test)} for test in unit["tests"]]
    assert len(run()[0]) == 1
    associations = rows[index]["test_cases"]["associations"]
    if damage == "order":
        associations[0], associations[1] = associations[1], associations[0]
    else:
        associations.pop()
    with pytest.raises(ValueError):
        run()


def test_pending_without_plan_or_associations(boundary):
    """Aceita contexto não carregado para pendentes sem exigir plano ou alvo."""
    rows, run = boundary
    rows[2]["decision"] = "not_evaluated"
    rows[2]["test_cases"]["associations"] = []
    rows[2]["case_evidence"] = []
    rows[1]["unit_suite"].pop("plan")
    assert run()[0] == []


def test_real_helper_rejects_non_registration_and_invalid_source(boundary):
    """Recusa intervalo que não identifica registro e fonte com erro sintático."""
    rows, run = boundary
    case = rows[2]["case_evidence"][0]
    case["declaration"]["end"] -= 1
    with pytest.raises(ValueError):
        run()
    groups, _ = reader.load_extraction(None)
    content = "test('x', () => f()); @@@"
    groups[0]["repository"]["context"]["files"][1]["content"] = content
    rows[1]["unit_suite"]["plan"]["plans"][1]["sha256"] = hashlib.sha256(content.encode()).hexdigest()
    case["declaration"]["end"] += 1
    with pytest.raises(ValueError, match="vínculos sintáticos"):
        run()


def test_no_approved_does_not_require_helper(boundary, monkeypatch):
    """A ausência de aprovadas não exige Node ou compilador instalado."""
    rows, run = boundary
    rows[2]["decision"] = "not_evaluated"
    rows[2]["test_cases"]["associations"] = []
    rows[2]["case_evidence"] = []

    def unavailable():
        """Falha caso o preflight seja indevidamente solicitado."""
        raise AssertionError("Preflight inesperado.")

    monkeypatch.setattr(reader, "preflight", unavailable)
    assert run()[0] == []


@pytest.mark.parametrize("failure", ["preflight", "timeout"])
def test_helper_failure_is_sanitized(boundary, monkeypatch, failure):
    """Falha operacional interrompe seleção sem divulgar conteúdo do subprocesso."""
    _, run = boundary

    def preflight():
        """Fornece configuração controlada ou simula ferramenta ausente."""
        if failure == "preflight":
            raise ValueError("segredo")
        return {"node": "node", "analyzer": {"version": "6.0.3"}}

    def timeout(*args, **kwargs):
        """Simula prazo excedido com detalhes privados no subprocesso."""
        raise subprocess.TimeoutExpired("segredo", 180)

    monkeypatch.setattr(reader, "preflight", preflight)
    monkeypatch.setattr(reader.subprocess, "run", timeout)
    with pytest.raises(ValueError, match="Não foi possível conferir os vínculos sintáticos") as error:
        run()
    assert "segredo" not in str(error.value)


def test_repeated_cases_are_batched(boundary, monkeypatch):
    """Desduplicação conserva apenas um vínculo por ponto e repositório."""
    rows, run = boundary
    rows[2]["case_evidence"].append(deepcopy(rows[2]["case_evidence"][0]))
    calls = []

    def process(command, **kwargs):
        """Inspeciona o protocolo enviado sem iniciar processo externo."""
        calls.append(json.loads(kwargs["input"]))
        return type("Result", (), {"returncode": 0, "stdout": b'{"ok":true}'})()

    monkeypatch.setattr(reader, "preflight", lambda: {"node": "node", "analyzer": {"version": "6.0.3"}})
    monkeypatch.setattr(reader.subprocess, "run", process)
    assert len(run()[0]) == 1
    assert len(calls) == 1 and len(calls[0]["bindings"]) == 1 and len(calls[0]["files"]) == 1


@pytest.mark.parametrize("damage", ["sha", "id", "origin", "association", "version", "policy", "target", "point", "hash"])
def test_cross_input_corruption(boundary, damage):
    """Recusa adulteração da população, política ou evidência vinculada à origem."""
    rows, run = boundary
    if damage == "sha":
        rows[0]["source"]["sha256"] = "c" * 64
    elif damage == "id":
        rows[3]["instance_id"] = "f" * 64
    elif damage == "origin":
        rows[1]["commit_sha"] = "c" * 40
    elif damage == "association":
        rows[2]["test_cases"]["associations"][0]["test"]["path"] = "other.ts"
    elif damage == "version":
        rows[0]["schema_version"] = 2
    elif damage == "policy":
        rows[0]["policy"]["version"] = True
    elif damage == "target":
        rows[1]["unit_suite"]["plan"]["targets"][rows[2]["instance_id"]]["start"] = 2
    elif damage == "point":
        rows[2]["case_evidence"][0]["points"][0]["start"] = 0
    else:
        rows[1]["unit_suite"]["plan"]["plans"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError):
        run()


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}\n', b'{"x":NaN}\n', b'{"x":1e999}\n', b'{}\n\n', b'\xff'])
def test_strict_jsonl(tmp_path, raw):
    """Recusa JSON ambíguo, não finito ou com linhas internas vazias."""
    path = tmp_path / "input.jsonl"
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        reader.read_jsonl_bytes(path)


def test_missing_closing_and_zero_selection(boundary, monkeypatch):
    """A validação integral ocorre mesmo quando nenhuma instância é aprovada."""
    rows, run = boundary
    rows[2]["decision"] = "not_evaluated"
    rows[2]["test_cases"]["associations"] = []
    rows[2]["case_evidence"] = []

    def validate(records):
        """Simula o leitor integral ao exigir o fechamento publicado."""
        if records[-1]["record_type"] != "run_finished":
            raise ValueError("Fechamento ausente.")
        return records[-1]
    monkeypatch.setattr(reader, "validate_output_records", validate)
    assert run()[0] == []
    rows[3]["instance_id"] = "f" * 64
    with pytest.raises(ValueError):
        run()
    with pytest.raises(ValueError):
        run(rows[:-1])


def test_real_reader_rejects_missing_closing(tmp_path, monkeypatch):
    """O validador real do 04 recusa publicação truncada antes de carregar o 03."""
    from test_validation import published_fixture

    rows, _, _ = published_fixture(monkeypatch, tmp_path, counts=(4,), policy_version=2)
    assert rows[0]["schema_version"] == 3 and rows[0]["policy"] == PAIR_POLICY
    assert reader.validate_output_records(rows) == rows[-1]
    with pytest.raises(ValueError):
        reader.validate_output_records(rows[:-1])
    path = tmp_path / "truncated.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows[:-1]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        reader.load_deduplication_inputs(path, tmp_path / "missing-extraction.jsonl")
