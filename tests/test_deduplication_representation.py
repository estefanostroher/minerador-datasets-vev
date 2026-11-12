"""Verifica riscos de falsa igualdade na representação de instâncias."""

from copy import deepcopy
import json

import pytest

from robo_minerador.deduplication_representation import build_representation, canonical_bytes


def sample():
    """Cria evidências repetidas que permitem alterar posições sem mudar textos."""
    files = {"src.ts": "doc\ncode\ndoc\ncode", "test.ts": "test\ntest",
             "config.json": "{}", "fixture.txt": "fixture"}
    unit = {
        "instance_id": "one", "criteria": {"reason": "first"},
        "function": {"kind": "function_declaration", "name": "f", "class_name": None,
                     "path": "src.ts", "start_line": 2, "end_line": 2,
                     "signature": "code", "reference_code": "code"},
        "documentation": [{"kind": "attached_comment", "path": "src.ts", "start_line": 1,
                           "end_line": 1, "original_text": "doc", "association": {"kind": "attached"}}],
        "tests": [{"path": "test.ts", "framework": "jest", "name": "t", "start_line": 1,
                   "end_line": 1, "parameterized": False, "modifiers": [], "skipped": False,
                   "association": {"kind": "direct_call", "call_line": 1, "expression": "f()",
                                   "resolved_target": {"path": "src.ts", "name": "f", "start_line": 2}}}],
        "context_paths": list(files),
    }
    return unit, files


def test_equal_despite_provenance_positions_and_order():
    """Ignora proveniência e posições, ordenando conjuntos publicados sem perdas."""
    unit, files = sample()
    unit["documentation"].append({**unit["documentation"][0], "kind": "external"})
    unit["tests"].append({**deepcopy(unit["tests"][0]), "name": "other"})
    other = deepcopy(unit)
    other.update(instance_id="other", criteria={"reason": "different"}, validation={"environment": "other"})
    other["function"].update(start_line=4, end_line=4)
    for doc in other["documentation"]:
        doc.update(start_line=3, end_line=3)
    for test in other["tests"]:
        test.update(start_line=2, end_line=2)
        test["association"].update(call_line=2)
        test["association"]["resolved_target"]["start_line"] = 4
    other["documentation"].reverse()
    other["tests"].reverse()
    other["context_paths"].reverse()
    assert build_representation(unit, files) == build_representation(other, files)


@pytest.mark.parametrize("change", ["code", "doc", "test", "config", "fixture", "path", "newline", "association"])
def test_evidence_changes_remain_distinct(change):
    """Distingue texto, caminhos, contexto e multiplicidade dos vínculos."""
    unit, files = sample()
    other, changed = deepcopy(unit), files.copy()
    if change == "code":
        other["function"]["reference_code"] = "different"
    elif change == "doc":
        other["documentation"][0]["original_text"] = "different"
    elif change == "test":
        changed["test.ts"] = "different\ntest"
    elif change == "config":
        changed["config.json"] = '{"x":1}'
    elif change == "fixture":
        changed["fixture.txt"] = "different"
    elif change == "path":
        changed["renamed.txt"] = changed.pop("fixture.txt")
        other["context_paths"][-1] = "renamed.txt"
    elif change == "newline":
        changed["src.ts"] = changed["src.ts"].replace("\n", "\r\n")
    else:
        other["tests"].append(deepcopy(other["tests"][0]))
    assert build_representation(unit, files) != build_representation(other, changed)


def test_unicode_serialization_preserves_astral_and_lone_surrogates():
    """Preserva Unicode astral e surrogates isolados por escapes JSON."""
    text = "😀\ud800x\udfff"
    raw = canonical_bytes({"text": text})
    assert raw == b'{"text":"\\ud83d\\ude00\\ud800x\\udfff"}'
    assert json.loads(raw)["text"] == text


def test_javascript_line_breaks_and_vertical_tab():
    """Distingue separadores JavaScript de tabulação vertical dentro da linha."""
    unit, files = sample()
    files["test.ts"] = "a\r\nb\rc\nd\u2028e\u2029f\vinside\n"
    unit["tests"][0].update(start_line=2, end_line=6)
    value = json.loads(build_representation(unit, files))
    assert value["tests"][0]["original_text"] == "b\rc\nd\u2028e\u2029f\vinside"
    unit["tests"][0].update(start_line=7, end_line=7)
    assert json.loads(build_representation(unit, files))["tests"][0]["original_text"] == ""


@pytest.mark.parametrize("start,end", [(True, 1), (0, 1), (2, 1), (1, 3)])
def test_invalid_intervals_cannot_produce_equality(start, end):
    """Recusa intervalos inválidos em vez de produzir trechos vazios enganosos."""
    unit, files = sample()
    unit["tests"][0].update(start_line=start, end_line=end)
    with pytest.raises(ValueError):
        build_representation(unit, files)


def test_missing_context_or_test_file_is_rejected():
    """Recusa referências ausentes em vez de compará-las como conteúdo vazio."""
    unit, files = sample()
    for path in ("fixture.txt", "test.ts"):
        incomplete = files.copy()
        del incomplete[path]
        with pytest.raises(KeyError):
            build_representation(unit, incomplete)
