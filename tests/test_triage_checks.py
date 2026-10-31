"""Verifica riscos de falsa seleção e mistura de snapshots sem acessar rede."""

import base64
import hashlib
import json
from types import SimpleNamespace

from github import GithubException
import pytest
from requests.exceptions import RequestException

from robo_minerador import triage_snapshot
from robo_minerador.triage_checks import CRITERION_NAMES, evaluate_snapshot
from robo_minerador.triage_snapshot import Snapshot, SnapshotUnavailable


COMMIT = "a" * 40


def blob(text):
    """Constrói um objeto Git verificável, evitando mocks com SHA incoerente."""
    content = text.encode("utf-8")
    sha = hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()
    return {"sha": sha, "encoding": "base64", "content": base64.b64encode(content).decode()}


class MemoryClient:
    """Simula a API por ID e nome canônico com rastreamento de todos os pedidos."""

    def __init__(self, files=None, spdx="MIT"):
        """Cria árvore e licença de um único commit sem branch resolvível."""
        files = files if files is not None else {"LICENSE": "license", "src/main.ts": "export const x = 1"}
        self.objects = {path: blob(text) for path, text in files.items()}
        self.tree = {"truncated": False, "tree": [
            {"path": path, "type": "blob", "mode": "100644", "sha": data["sha"],
             "size": len(files[path].encode())} for path, data in self.objects.items()
        ]}
        self.license_data = ({**self.objects["LICENSE"], "path": "LICENSE", "license": {"spdx_id": spdx}}
                             if "LICENSE" in files else GithubException(404, {"message": "not found"}))
        self.repository = SimpleNamespace(id=7, private=False, full_name="canonical/renamed")
        self.requester = self
        self.requests = []

    def get_repo(self, repository_id):
        """Exige recuperação por ID em vez de nome antigo ou branch."""
        assert repository_id == 7
        return self.repository

    def requestJsonAndCheck(self, method, url, parameters=None):
        """Entrega apenas os endpoints permitidos, registrando SHA e parâmetros."""
        self.requests.append((method, url, parameters))
        assert method == "GET"
        assert url.startswith("/repos/canonical/renamed/")
        if "/git/trees/" in url:
            assert url.endswith(COMMIT)
            assert parameters == {"recursive": "1"}
            return {}, self.tree
        if url.endswith("/license"):
            assert parameters == {"ref": COMMIT}
            if isinstance(self.license_data, Exception):
                raise self.license_data
            return {}, self.license_data
        assert "/git/blobs/" in url
        sha = url.rsplit("/", 1)[1]
        return {}, next(data for data in self.objects.values() if data["sha"] == sha)


def snapshot(client):
    """Fixa o candidato no SHA observado, com metadado de licença não confiável."""
    return Snapshot(client, {"repository_id": 7, "commit_sha": COMMIT,
                             "repository_url": "https://github.com/old/name", "license": {"spdx_id": "MIT"}})


def evaluate(files, spdx="MIT"):
    """Avalia arquivos locais como snapshot público e completo no commit fixado."""
    return evaluate_snapshot(snapshot(MemoryClient({"LICENSE": "license", **files}, spdx)))


@pytest.mark.parametrize("boundary", ["get_repo", "tree", "blob", "license"])
@pytest.mark.parametrize("malformed_json", [True, False], ids=["truncated-json", "other-value-error"])
def test_api_json_decode_errors_are_sanitized_without_hiding_other_errors(monkeypatch, boundary, malformed_json):
    """Impede abortar o lote por JSON HTTP truncado sem mascarar outros ValueErrors."""
    client = MemoryClient()
    failure = (json.JSONDecodeError("sensitive message", "sensitive response", 0)
               if malformed_json else ValueError("programming error"))

    def fail_request(*args, **kwargs):
        """Simula falha exclusivamente na chamada de rede selecionada."""
        raise failure

    view = snapshot(client) if boundary in {"blob", "license"} else None
    if boundary == "get_repo":
        monkeypatch.setattr(client, "get_repo", fail_request)
    else:
        monkeypatch.setattr(client.requester, "requestJsonAndCheck", fail_request)
    with pytest.raises(RequestException if malformed_json else ValueError) as caught:
        if boundary in {"get_repo", "tree"}:
            snapshot(client)
        elif boundary == "blob":
            view.read_text("src/main.ts")
        else:
            view.license()
    if malformed_json:
        assert str(caught.value) == "Resposta JSON inválida recebida da API do GitHub."
        assert caught.value.__cause__ is failure
        assert "sensitive" not in str(caught.value)
    else:
        assert caught.value is failure


def test_sha_tree_license_and_blob_integrity():
    """Impede uso da branch, metadado atual ou conteúdo diferente do blob fixado."""
    client = MemoryClient()
    view = snapshot(client)
    assert view.paths == ["LICENSE", "src/main.ts"]
    assert view.read_text("src/main.ts") == "export const x = 1"
    assert view.license() == {"path": "LICENSE", "blob_sha": client.objects["LICENSE"]["sha"], "spdx_id": "MIT"}
    assert client.requests[0][1].endswith(COMMIT)
    assert client.requests[-1][2] == {"ref": COMMIT}
    client.license_data["sha"] = "b" * 40
    with pytest.raises(SnapshotUnavailable, match="Licença retornada") as error:
        view.license()
    assert error.value.code == "license_mismatch"
    client.license_data = {**client.objects["LICENSE"], "path": "elsewhere/LICENSE"}
    with pytest.raises(SnapshotUnavailable):
        view.license()
    client.objects["src/main.ts"]["content"] = base64.b64encode(b"tampered").decode()
    with pytest.raises(SnapshotUnavailable) as error:
        snapshot(client).read_text("src/main.ts")
    assert error.value.code == "blob_mismatch"


@pytest.mark.parametrize("limitation", ["truncated", "files", "submodule", "symlink", "private", "content", "budget"])
def test_incomplete_inspection_never_becomes_absence(monkeypatch, limitation):
    """Impede reprovação científica quando limites ou links ocultam evidências."""
    client = MemoryClient()
    if limitation == "truncated":
        client.tree["truncated"] = True
    elif limitation == "files":
        monkeypatch.setattr(triage_snapshot, "MAX_FILES", 1)
    elif limitation in {"submodule", "symlink"}:
        client.tree["tree"].append({"path": "packages/core", "type": "commit" if limitation == "submodule" else "blob",
                                   "mode": "160000" if limitation == "submodule" else "120000", "sha": "c" * 40})
    elif limitation == "private":
        client.repository.private = True
    elif limitation == "content":
        monkeypatch.setattr(triage_snapshot, "MAX_FILE_BYTES", 1)
    else:
        monkeypatch.setattr(triage_snapshot, "MAX_CONTENT_READS", 0)
    with pytest.raises(SnapshotUnavailable) as error:
        view = snapshot(client)
        view.read_text("src/main.ts")
    assert error.value.code in {"truncated_tree", "file_limit", "uninspected_link", "repository_not_public", "content_limit", "content_budget"}


@pytest.mark.parametrize("spdx", ["MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "GPL-3.0", "NOASSERTION", None])
def test_license_recognition_controls_selection_and_preserves_readme(spdx):
    """Impede aprovação por licença preliminar e perda do README na saída antecipada."""
    result = evaluate({"README.md": "docs", "src/x.ts": "export const x = 1"}, spdx)
    allowed = spdx in {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC"}
    assert result["criteria"]["license"]["status"] == ("approved" if allowed else "rejected")
    assert result["readme"] == {"present": True, "paths": ["README.md"], "count": 1}
    assert set(result["criteria"]) == set(CRITERION_NAMES)
    if not allowed:
        assert all(result["criteria"][name]["status"] == "not_evaluated" for name in CRITERION_NAMES[2:])


def test_license_404_is_absence_only_without_candidate_and_api_errors_propagate():
    """Distingue licença ausente, falha de reconhecimento e falha operacional."""
    client = MemoryClient({"src/x.ts": "export {}"})
    assert evaluate_snapshot(snapshot(client))["criteria"]["license"]["status"] == "rejected"
    assert ("GET", "/repos/canonical/renamed/license", {"ref": COMMIT}) in client.requests
    client = MemoryClient()
    client.license_data = GithubException(404, {})
    result = evaluate_snapshot(snapshot(client))
    assert result["criteria"]["license"]["status"] == "not_evaluated"
    assert result["criteria"]["license"]["evidence"]["limitation_code"] == "license_unresolved"
    client.license_data = GithubException(403, {})
    with pytest.raises(GithubException):
        evaluate_snapshot(snapshot(client))


@pytest.mark.parametrize("recognized", [True, False], ids=["recognized-at-sha", "candidate-with-404"])
def test_prefixed_license_uses_endpoint_and_404_is_not_absence(recognized):
    """Evita falsa ausência de MIT-LICENSE e exige identificação no blob do SHA fixado."""
    client = MemoryClient({"MIT-LICENSE": "MIT license text", "src/index.ts": "export const x = 1"})
    license_blob = client.objects["MIT-LICENSE"]
    client.license_data = ({**license_blob, "path": "MIT-LICENSE", "license": {"spdx_id": "MIT"}}
                           if recognized else GithubException(404, {}))
    criterion = evaluate_snapshot(snapshot(client))["criteria"]["license"]
    assert ("GET", "/repos/canonical/renamed/license", {"ref": COMMIT}) in client.requests
    assert criterion["status"] == ("approved" if recognized else "not_evaluated")
    if recognized:
        assert criterion["evidence"]["path"] == "MIT-LICENSE"
        assert criterion["evidence"]["blob_sha"] == license_blob["sha"]
        assert criterion["evidence"]["spdx_id"] == "MIT"
    else:
        assert criterion["evidence"]["limitation_code"] == "license_unresolved"


def test_declarations_dependencies_generated_and_tests_are_not_ts_implementation():
    """Evita selecionar repositório TS composto só de declarações ou artefatos/testes."""
    files = {path: "export {}" for path in ["index.d.ts", "index.d.mts", "index.d.cts", "node_modules/x.ts",
             "generated/x.ts", "src/x.generated.ts", "tests/x.test.ts", "dist/x.ts", "vitest.config.ts"]}
    assert evaluate(files)["criteria"]["typescript"]["status"] == "rejected"
    files["packages/core/src/main.mts"] = "export function real() { return 1 }"
    criterion = evaluate(files)["criteria"]["typescript"]
    assert criterion["status"] == "approved"
    assert criterion["evidence"]["paths"] == ["packages/core/src/main.mts"]


@pytest.mark.parametrize("framework,command", [("jest", "jest"), ("vitest", "vitest run"), ("mocha", "mocha"),
    ("ava", "ava"), ("tape", "tape"), ("node:test", "node --test"), ("bun:test", "bun test")])
def test_monorepo_frameworks_without_root_manifest_lock_or_readme(framework, command):
    """Evita falso negativo em subpacotes e frameworks unitários além de Jest/Vitest."""
    result = evaluate({"packages/core/src/main.ts": "export const x = 1",
        "packages/core/test/main.spec.ts": f'import {{ test }} from "{framework}";\ntest("works", () => {{}});',
        "packages/core/package.json": json.dumps({"scripts": {"test": command}})})
    assert all(item["status"] == "approved" for item in result["criteria"].values())
    assert result["readme"]["present"] is False
    assert result["criteria"]["execution_configuration"]["evidence"]["locks"]["count"] == 0


@pytest.mark.parametrize("mode,test_status,execution_status", [
    ("browser", "rejected", "rejected"), ("placeholder", "not_evaluated", "rejected"),
    ("unknown", "not_evaluated", "not_evaluated"), ("custom-files", "not_evaluated", "not_evaluated"),
    ("absent", "rejected", "rejected"),
])
def test_browser_placeholder_unknown_and_absence_remain_distinct(mode, test_status, execution_status):
    """Impede que navegador, nomes isolados ou placeholders sejam aprovação unitária."""
    files = {"src/main.ts": "export {}"}
    if mode == "browser":
        files["test/main.spec.ts"] = 'import { test } from "@playwright/test";'
        command = "playwright test"
    elif mode == "placeholder":
        files["test/main.spec.ts"] = '// import { test } from "vitest";\nexport {}'
        command = 'echo "Error: no test specified" && exit 1'
    elif mode == "unknown":
        files["test/main.spec.ts"] = "customTest('case')"
        command = "node scripts/custom-runner.js"
    elif mode == "custom-files":
        files["checks/custom-cases.ts"] = "customTest('case')"
        command = "node scripts/custom-runner.js"
    else:
        command = None
    files["package.json"] = json.dumps({"scripts": {"test": command}} if command else {})
    criteria = evaluate(files)["criteria"]
    assert criteria["test_indicators"]["status"] == test_status
    assert criteria["execution_configuration"]["status"] == execution_status


def test_manifest_scope_parse_limits_and_evidence_counts():
    """Evita herdar framework de pacote irmão, rejeitar parser limitado ou truncar contagens."""
    files = {"src/main.ts": "export {}", "packages/a/package.json": '{"devDependencies":{"vitest":"1"}}',
             "packages/b/package.json": '{}', "packages/b/test/x.spec.ts": "customTest()"}
    result = evaluate(files)
    assert result["criteria"]["test_indicators"]["status"] == "not_evaluated"
    files["packages/b/package.json"] = "{ /* JSONC não suportado */ }"
    result = evaluate(files)
    assert result["criteria"]["execution_configuration"]["status"] == "not_evaluated"
    assert result["criteria"]["execution_configuration"]["evidence"]["limitation_code"] == "manifest_parse"
    files = {f"src/{number}.ts": "export const x = 1" for number in range(25)}
    evidence = evaluate(files)["criteria"]["typescript"]["evidence"]
    assert evidence["candidate_files"]["count"] == 25
    assert len(evidence["candidate_files"]["paths"]) == 20
    assert evidence["count"] == 1


@pytest.mark.parametrize("source,command,expected", [
    ("README.md", "npx vitest run", "approved"),
    ("DEVELOPMENT.md", "npx vitest run", "approved"),
    ("docs/testing.md", "npx vitest run", "approved"),
    ("README.md", "npm test", "not_evaluated"),
    ("README.md", "Consulte o guia externo para executar a suíte.", "not_evaluated"),
    ("vitest.config.ts", "export default customConfig()", "not_evaluated"),
    ("package.json", '{"devDependencies":{"vitest":"1"}}', "not_evaluated"),
    (None, None, "rejected"),
])
def test_documented_or_configured_execution_without_test_script(source, command, expected):
    """Impede rejeitar H-R03 apenas porque não existe script test no manifesto."""
    files = {"src/index.ts": "export const x = 1", "package.json": "{}",
             "test/x.spec.ts": 'import { test } from "vitest";'}
    if source:
        files[source] = f"```sh\n{command}\n```" if source.endswith(".md") else command
    criterion = evaluate(files)["criteria"]["execution_configuration"]
    assert criterion["status"] == expected
    if expected == "approved":
        assert criterion["evidence"]["commands"][0]["source"] == "documentation"


@pytest.mark.parametrize("signal", ["script", "dependency", "configuration", "documentation"])
def test_runner_with_unconventional_selection_is_pending(signal):
    """Evita falsa ausência quando um runner pode selecionar arquivos não convencionais."""
    files = {"src/index.ts": "export const x = 1", "checks/cases.ts": "customTest()"}
    if signal == "script":
        files["package.json"] = '{"scripts":{"test":"vitest run checks"}}'
    elif signal == "dependency":
        files["package.json"] = '{"devDependencies":{"vitest":"1"}}'
    elif signal == "configuration":
        files["vitest.config.ts"] = "export default { test: { include: ['checks/**'] } }"
    else:
        files["README.md"] = "```sh\nnpx vitest run checks\n```"
    assert evaluate(files)["criteria"]["test_indicators"]["status"] == "not_evaluated"


@pytest.mark.parametrize("command_owner,command,expected", [
    ("packages/b/package.json", "vitest run", "not_evaluated"),
    ("packages/a/package.json", "jest", "not_evaluated"),
    ("package.json", "pnpm --filter a test", "not_evaluated"),
    ("packages/a/package.json", "vitest run", "approved"),
])
def test_execution_must_match_unit_package_and_framework(command_owner, command, expected):
    """Impede combinar teste unitário de um pacote com runner de outro ou incompatível."""
    files = {"packages/a/src/index.ts": "export const x = 1",
             "packages/a/test/x.spec.ts": 'import { test } from "vitest";',
             "packages/a/package.json": "{}", "packages/b/package.json": "{}"}
    files[command_owner] = json.dumps({"scripts": {"test": command}})
    criteria = evaluate(files)["criteria"]
    assert criteria["test_indicators"]["status"] == "approved"
    assert criteria["execution_configuration"]["status"] == expected
    assert criteria["test_indicators"]["evidence"]["unit_files"]["paths"] == ["packages/a/test/x.spec.ts"]


@pytest.mark.parametrize("path", ["src/browser/parser.ts", "packages/build/src/index.ts", "packages/vendor/src/index.ts"])
def test_production_directories_are_not_silently_discarded(path):
    """Preserva implementação em diretórios de domínio que parecem artefatos ou navegador."""
    criterion = evaluate({path: "export const parse = (text: string) => text.trim()"})["criteria"]["typescript"]
    assert criterion["status"] == "approved"
    assert criterion["evidence"]["paths"] == [path]


@pytest.mark.parametrize("content,expected", [
    ("", "rejected"), ("// const x = 1\n/* class Runtime {} */", "rejected"),
    ("export interface Options { value: string }\nexport type Result = number;", "rejected"),
    ("export declare class Parser { parse(): string }", "rejected"),
    ("export const x = 1", "approved"),
    ("export class Parser { parse() { return 1 } }", "approved"),
    ("runCustomRuntime()", "not_evaluated"),
])
def test_typescript_needs_runtime_content_not_just_extension(content, expected):
    """Impede que arquivo vazio, comentários e declarações sejam implementação TS."""
    assert evaluate({"src/index.ts": content})["criteria"]["typescript"]["status"] == expected


def test_typescript_read_limit_remains_pending(monkeypatch):
    """Impede confundir falha de leitura de implementação com ausência científica."""
    monkeypatch.setattr(triage_snapshot, "MAX_CONTENT_READS", 1)
    criterion = evaluate({"src/index.ts": "export const x = 1"})["criteria"]["typescript"]
    assert criterion["status"] == "not_evaluated"
    assert criterion["evidence"]["limitation_code"] == "content_budget"


@pytest.mark.parametrize("command", ["jest --version", "vitest --help", "npx jest --listTests",
    "jest --showConfig", "vitest list", "bun test --list-tests", "mocha --list-reporters"])
def test_informational_runner_commands_do_not_approve_execution(command):
    """Impede aprovação por comandos que só mostram ajuda, versão ou inventários."""
    criteria = evaluate({"src/index.ts": "export const x = 1",
        "test/x.spec.ts": 'import { test } from "vitest";',
        "package.json": json.dumps({"scripts": {"test": command}})})["criteria"]
    assert criteria["execution_configuration"]["status"] != "approved"


@pytest.mark.parametrize("command,framework,expected", [
    ("vitest run --root ../b", "vitest", "not_evaluated"),
    ("vitest run --root=../b", "vitest", "not_evaluated"),
    ("vitest run --dir ../b/test", "vitest", "not_evaluated"),
    ("vitest run --dir=../b/test", "vitest", "not_evaluated"),
    ("vitest run --project b", "vitest", "not_evaluated"),
    ("vitest run --project=b", "vitest", "not_evaluated"),
    ("jest --projects ../b", "jest", "not_evaluated"),
    ("jest --projects=../b", "jest", "not_evaluated"),
    ("vitest run --workspace ../b/workspace.ts", "vitest", "not_evaluated"),
    ("vitest run --workspace=../b/workspace.ts", "vitest", "not_evaluated"),
    ("jest --config ../b/jest.config.js", "jest", "not_evaluated"),
    ("jest --config=../b/jest.config.js", "jest", "not_evaluated"),
    ("jest -c ../b/jest.config.js", "jest", "not_evaluated"),
    ("jest --testPathPatterns=b", "jest", "not_evaluated"),
    ("vitest run ../b/test/b.spec.ts", "vitest", "not_evaluated"),
    ("jest -- ../b/test/b.spec.ts", "jest", "not_evaluated"),
    ("node --test ../b/test/b.spec.js", "node:test", "not_evaluated"),
    ("bun test ../b/test/b.spec.ts", "bun:test", "not_evaluated"),
    ("vitest run --coverage", "vitest", "approved"),
    ("jest --coverage=true --runInBand", "jest", "approved"),
    ("vitest run --coverage.reporter=lcov", "vitest", "approved"),
    ("jest --coverageDirectory ../reports", "jest", "approved"),
    ("jest --coverage --coverageReporters lcov", "jest", "approved"),
    ("vitest run --coverage --dir=../b", "vitest", "not_evaluated"),
    ("jest --coverageDirectory ../reports ../b/test/b.spec.ts", "jest", "not_evaluated"),
])
def test_unresolved_selectors_cannot_bind_other_package_tests(command, framework, expected):
    """Impede ligar testes A a comando que pode selecionar B, preservando cobertura inócua."""
    criteria = evaluate({"packages/a/src/index.ts": "export const x = 1",
        "packages/a/test/a.spec.ts": f'import {{ test }} from "{framework}";',
        "packages/a/package.json": json.dumps({"scripts": {"test": command}})})["criteria"]
    assert criteria["test_indicators"]["status"] == "approved"
    execution = criteria["execution_configuration"]
    assert execution["status"] == expected
    if expected == "not_evaluated":
        assert execution["evidence"]["commands"][0]["kind"] == "unknown"
        assert execution["evidence"]["matched_command_count"] == 0


def test_matching_script_stops_test_io_and_never_reads_documentation(monkeypatch):
    """Evita cem leituras redundantes após comprovar existência e comando compatível."""
    files = {"LICENSE": "license", "README.md": "Não deve ser lido",
             "src/index.ts": "export const x = 1",
             "package.json": '{"scripts":{"test":"vitest run"}}',
             **{f"test/{number:03}.spec.ts": 'import { test } from "vitest";' for number in range(100)}}
    view = snapshot(MemoryClient(files))
    original_read = view.read_text
    reads = []

    def tracked_read(path):
        """Falha se houver leitura dispensável de documentação ou de outro teste."""
        reads.append(path)
        assert path != "README.md"
        assert not path.startswith("test/") or path == "test/000.spec.ts"
        return original_read(path)

    monkeypatch.setattr(view, "read_text", tracked_read)
    criteria = evaluate_snapshot(view)["criteria"]
    assert all(item["status"] == "approved" for item in criteria.values())
    assert reads == ["src/index.ts", "package.json", "test/000.spec.ts"]
    evidence = criteria["test_indicators"]["evidence"]
    assert evidence["candidate_files"]["count"] == 100
    assert evidence["inspected_files"]["count"] == 1
    assert evidence["unit_files"]["count"] == evidence["unit_association_count"] == 1
    assert evidence["inspection_complete"] is False
    execution = criteria["execution_configuration"]["evidence"]
    assert execution["documentation"]["count"] == 1
    assert execution["inspected_documentation"]["count"] == 0
    assert execution["documentation_inspection_complete"] is False


@pytest.mark.parametrize("documentation_only", [False, True], ids=["later-package-script", "later-package-docs"])
def test_later_package_match_and_deferred_documentation_preserve_selection(monkeypatch, documentation_only):
    """Preserva busca em múltiplos pacotes e fallback documental sem reler testes."""
    files = {"LICENSE": "license", "packages/a/src/index.ts": "export const x = 1",
             "packages/a/package.json": "{}", "packages/a/test/a.spec.ts": 'import { test } from "jest";',
             "packages/b/package.json": "{}" if documentation_only else '{"scripts":{"test":"vitest run"}}',
             "packages/b/test/b.spec.ts": 'test("case", () => {})',
             "packages/a/README.md": "```sh\nnpx jest --version\n```",
             "packages/b/README.md": "```sh\nnpx vitest run\n```",
             "zz_unused.md": "Não deve ser lido"}
    view = snapshot(MemoryClient(files))
    original_read = view.read_text
    reads = []

    def tracked_read(path):
        """Registra leituras únicas e bloqueia documentação posterior ao vínculo suficiente."""
        assert path not in reads
        assert path != "zz_unused.md"
        if not documentation_only:
            assert not path.endswith("README.md")
        reads.append(path)
        return original_read(path)

    monkeypatch.setattr(view, "read_text", tracked_read)
    criteria = evaluate_snapshot(view)["criteria"]
    assert all(item["status"] == "approved" for item in criteria.values())
    assert "packages/b/test/b.spec.ts" in reads
    matches = criteria["execution_configuration"]["evidence"]["matched_commands"]
    assert {match["owner"] for match in matches} == {"packages/b/package.json"}
    assert {match["source"] for match in matches} == ({"documentation"} if documentation_only else {"script"})
    inventory = criteria["test_indicators"]["evidence"]
    assert inventory["candidate_files"]["count"] == inventory["inspected_files"]["count"] == 2
    assert inventory["unit_files"]["count"] == inventory["unit_association_count"] == 2
    assert inventory["inspection_complete"] is True
    if documentation_only:
        assert reads.index("packages/b/test/b.spec.ts") < reads.index("packages/a/README.md")
        assert "packages/b/README.md" in reads
        evidence = criteria["execution_configuration"]["evidence"]
        assert evidence["inspected_documentation"]["count"] == 2
        assert evidence["documentation_inspection_complete"] is False
