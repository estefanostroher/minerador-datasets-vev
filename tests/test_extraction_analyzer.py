"""Verifica vínculos reais do auxiliar e rejeição de protocolos adulterados."""

from copy import deepcopy
import hashlib
import io
import subprocess

import pytest

from robo_minerador.extraction_analyzer import Analyzer, AnalyzerUnavailable, preflight, validate_output
from robo_minerador import extraction, extraction_analyzer
from test_extraction_input import candidate


@pytest.fixture(scope="module")
def settings():
    """Usa Node e TypeScript instalados ou informa precisamente o requisito ausente."""
    try:
        return preflight()
    except ValueError as error:
        pytest.skip(str(error))


@pytest.fixture(scope="module")
def analysis(settings):
    """Analisa alias e documentação com teste parametrizado sem executar candidatos."""
    texts = {
        "src/math.ts": "/** Dobra a entrada e acrescenta uma unidade. */\nexport function double(x: number) { return x * 2 + 1; }\n",
        "src/index.ts": "export { double as calculate } from './math';\n",
        "tests/math.test.ts": "import { test } from 'vitest';\nimport { calculate } from '@app/index';\ntest.each([1,2])('calculate', (x) => { calculate(x); });\n",
        "tsconfig.json": '{"compilerOptions":{"baseUrl":".","paths":{"@app/*":["src/*"]}}}',
        "package.json": '{"devDependencies":{"vitest":"1"}}',
    }
    files = [{"path": path, "content": content, "sha256": hashlib.sha256(content.encode()).hexdigest()}
             for path, content in texts.items()]
    analyzer = Analyzer(settings)
    try:
        return files, analyzer.analyze(files, [])
    finally:
        analyzer.cancel()


def test_real_alias_documentation_and_unexecuted_counts(analysis):
    """Confirma resolução ao símbolo original e preserva contagens executadas nulas."""
    files, result = analysis
    assert len(result["instances"]) == 1
    unit = result["instances"][0]
    assert unit["decision"] == "approved"
    assert unit["function"]["path"] == "src/math.ts"
    assert unit["documentation"][0]["original_text"] == files[0]["content"].splitlines()[0]
    assert len(unit["tests"]) == 1
    assert unit["tests"][0]["parameterized"] is True
    assert unit["tests"][0]["association"]["resolved_target"]["name"] == "double"
    assert unit["validation"]["executed_test_cases"] is None
    assert unit["validation"]["suite_passed"] is None
    assert any(item["specifier"] == "vitest" and item["resolved_path"] is None
               for item in result["local_dependencies"])


@pytest.mark.parametrize("damage", ["duplicate", "decision", "snippet", "line", "target", "validation",
                                     "missing_context", "documentation", "criterion", "unknown_field"])
def test_malformed_protocol_never_approves(analysis, settings, damage):
    """Bloqueia associações forjadas, posições erradas e promoção por protocolo inválido."""
    files, original = analysis
    result = deepcopy(original)
    unit = result["instances"][0]
    if damage == "duplicate":
        result["instances"].append(deepcopy(unit))
    elif damage == "decision":
        unit["decision"] = "rejected"
    elif damage == "snippet":
        unit["function"]["reference_code"] = "function forged() {}"
    elif damage == "line":
        unit["function"]["start_line"] = 999
    elif damage == "target":
        unit["tests"][0]["association"]["resolved_target"]["path"] = "src/index.ts"
    elif damage == "validation":
        unit["validation"]["executed_test_cases"] = 2
    elif damage == "missing_context":
        unit["context_paths"].append("missing.json")
    elif damage == "documentation":
        unit["documentation"][0]["original_text"] = "Descrição forjada."
    elif damage == "criterion":
        unit["criteria"]["preserved_local_context"]["status"] = []
    elif damage == "unknown_field":
        unit["tests"][0]["executed_count"] = 2
    with pytest.raises(AnalyzerUnavailable):
        validate_output(result, files, settings["analyzer"])


def test_incomplete_snapshot_never_promoted(analysis, settings):
    """Mantém pendência de contexto quando arquivos foram omitidos antes da análise."""
    files, _ = analysis
    result = Analyzer(settings).analyze(files, [{"code": "BINARY_CONTEXT", "message": "Fixture não inspecionada."}])
    assert result["instances"][0]["decision"] == "not_evaluated"
    assert result["instances"][0]["criteria"]["preserved_local_context"]["status"] == "not_evaluated"


def test_same_line_methods_and_anonymous_class_are_valid_protocol(settings):
    """Preserva métodos homônimos de classes distintas sem inventar nome de classe anônima."""
    content = ("class A { double(x:number) { return x*2; } } "
               "class B { double(x:number) { return x*2; } } "
               "export default class { triple(x:number) { return x*3; } }")
    result = Analyzer(settings).analyze([{"path": "a.ts", "content": content}], [])
    assert len(result["instances"]) == 3
    assert [unit["function"]["class_name"] for unit in result["instances"]] == ["A", "B", None]


def test_identical_same_line_declarations_preserve_unique_stable_instances(settings, monkeypatch):
    """Preserva duas ocorrências reais idênticas e rejeita unidades além do fonte original."""
    content = ("namespace A { export function f(x:number) {return x*2;} } "
               "namespace B { export function f(x:number) {return x*2;} }")
    files = [{"path": "a.ts", "content": content,
              "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()}]

    def collect(client, row):
        """Fornece o fonte preservado sem recuperar ou executar conteúdo candidato."""
        return files, []

    monkeypatch.setattr(extraction, "collect_context", collect)
    analyzer = Analyzer(settings)
    try:
        first = extraction._extract_candidate(None, candidate(), analyzer)
        second = extraction._extract_candidate(None, candidate(), analyzer)
        result = analyzer.analyze(files, [])
    finally:
        analyzer.cancel()
    assert first[0]["extraction_status"] != "failed"
    assert first[0]["summary"]["instance_count"] == 2
    assert not any(issue["code"] == "ANALYZER_FAILURE" for issue in first[0]["issues"])
    first_units = [row for row in first if row["record_type"] == "instance"]
    second_units = [row for row in second if row["record_type"] == "instance"]
    assert len(first_units) == len(second_units) == 2
    assert first_units[0]["function"] == first_units[1]["function"]
    identities = [row["instance_id"] for row in first_units]
    assert len(set(identities)) == 2
    assert identities == [row["instance_id"] for row in second_units]
    assert identities == [extraction.instance_id(candidate(), row["function"], ordinal=index)
                          for index, row in enumerate(first_units)]
    result["instances"].append(deepcopy(result["instances"][0]))
    with pytest.raises(AnalyzerUnavailable):
        validate_output(result, files, settings["analyzer"])


@pytest.mark.parametrize("interrupted", [False, True])
def test_timeout_or_interruption_kills_and_waits(monkeypatch, interrupted):
    """Impede processos órfãos ao expirar o prazo ou interromper a comunicação."""
    processes = []

    class Process:
        """Simula filho confiável travado, registrando comunicação e limpeza."""

        def __init__(self, command, **kwargs):
            """Registra argumentos de execução sem iniciar processo de candidato."""
            self.command = command
            self.returncode = None
            self.stdin, self.stdout, self.stderr = io.StringIO(), io.StringIO(), io.StringIO()
            self.killed = self.waited = False
            processes.append(self)

        def communicate(self, payload, timeout):
            """Exige envio somente em memória e dispara falha controlada."""
            assert timeout == 180
            assert '"files": []' in payload
            if interrupted:
                raise KeyboardInterrupt()
            raise subprocess.TimeoutExpired(self.command, timeout)

        def poll(self):
            """Mantém o processo em voo até seu recolhimento explícito."""
            return self.returncode

        def kill(self):
            """Registra interrupção forçada do filho."""
            self.killed = True

        def wait(self):
            """Registra recolhimento posterior ao encerramento forçado."""
            assert self.killed
            self.waited = True
            self.returncode = -1

    monkeypatch.setattr(extraction_analyzer.subprocess, "Popen", Process)
    analyzer = Analyzer({"node": "trusted-node", "helper": "trusted-helper", "analyzer": {}})
    with pytest.raises(KeyboardInterrupt if interrupted else AnalyzerUnavailable):
        analyzer.analyze([], [])
    assert processes[0].killed and processes[0].waited
    assert "--max-old-space-size=512" in processes[0].command
    assert processes[0].stdin.closed and processes[0].stdout.closed and processes[0].stderr.closed
    assert not analyzer._processes
