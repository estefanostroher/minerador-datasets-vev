"""Protege ordem, continuidade, fechamento e preservação da saída anterior."""

import json
import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock, get_ident
from pathlib import Path
from types import SimpleNamespace

from github import GithubException
import pytest
from requests.exceptions import RequestException

from robo_minerador import cli, concurrency, config, extraction
from robo_minerador.extraction_analyzer import preflight
from robo_minerador.extraction_format import compact_records, expand_instance
from robo_minerador.snapshot_store import SnapshotStore
from robo_minerador.git_transport import GitTransport
from robo_minerador.triage_snapshot import SnapshotUnavailable
from test_extraction_context import make_local_snapshot, snapshot_case  # noqa: F401
from robo_minerador.extraction_context import load_instance_context
from robo_minerador.extraction_input import load_triage
from test_extraction_input import candidate, save_rows, triage_rows

SOURCE = {"path": "triage.jsonl", "sha256": "a" * 64, "status": "completed", "input_count": 3}
SETTINGS = {"analyzer": {"name": "typescript_compiler_api", "version": "6.0.3"}}


def legacy_extract(token, candidates, source, settings, *, workers=4):
    """Isola fixtures antigas de coordenação no adaptador interno legado explícito."""
    yield from compact_records(extraction._raw_extract_repositories(
        token, candidates, source, settings, workers=workers))


class Client:
    """Registra propriedade por thread e fechamento pelo coordenador."""

    def __init__(self, fail_close=False):
        """Inicializa o cliente simulado sem conexão ou contexto de candidato."""
        self.owner = get_ident()
        self.closed_by = []
        self.fail_close = fail_close

    def close(self):
        """Registra fechamento mesmo quando há falha operacional simulada."""
        self.closed_by.append(get_ident())
        if self.fail_close:
            raise GithubException(503, {"message": "secret"})


class EmptyAnalyzer:
    """Simula análise útil sem declarações para testar coordenação isoladamente."""

    def __init__(self, settings):
        """Preserva os metadados previamente validados."""
        self.settings = settings
        self.cancelled = False

    def analyze(self, files, issues):
        """Retorna ausência comprovada de unidades no conjunto fornecido."""
        return {"instances": [], "issues": issues, "local_dependencies": [], "unresolved_references": []}

    def cancel(self):
        """Registra cancelamento para o protocolo de encerramento do coordenador."""
        self.cancelled = True


def test_bounded_order_and_thread_clients(monkeypatch):
    """Força conclusão inversa por eventos e impede iniciar candidato fora da janela."""
    clients, started, completion = [], [], []
    second_done = Event()
    lock = Lock()
    coordinator = get_ident()

    def factory(token, gate):
        """Registra clientes independentes com o mesmo gate fornecido pelo coordenador."""
        client = Client()
        clients.append(client)
        return client

    def collect(client, row):
        """Completa o segundo antes do primeiro sem usar esperas temporais arbitrárias."""
        assert client.owner == get_ident() and not client.closed_by
        identity = row["repository_id"]
        with lock:
            started.append(identity)
        if identity == 1:
            assert second_done.wait(5)
        completion.append(identity)
        if identity == 2:
            second_done.set()
        return [], []

    monkeypatch.setattr(extraction, "Analyzer", EmptyAnalyzer)
    monkeypatch.setattr(extraction, "create_client", factory)
    monkeypatch.setattr(extraction, "collect_context", collect)
    records = legacy_extract("token", [candidate(i) for i in range(1, 5)],
                                               {**SOURCE, "input_count": 4}, SETTINGS, workers=2)
    assert next(records)["event"] == "run_started"
    first = next(records)
    assert first["repository_id"] == 1
    assert sorted(started) == [1, 2]
    assert completion[:2] == [2, 1]
    remaining = list(records)
    repositories = [first, *(row for row in remaining if row["record_type"] == "repository")]
    assert [row["repository_id"] for row in repositories] == [1, 2, 3, 4]
    assert remaining[-1]["repository_count"] == 4
    assert remaining[-1]["status"] == "completed"
    assert len(clients) == 2
    assert all(client.closed_by == [coordinator] for client in clients)


@pytest.mark.parametrize("workers", [1, 3])
@pytest.mark.parametrize("close_error", [False, True])
def test_failed_middle_repository_preserves_following_success(monkeypatch, workers, close_error):
    """Mantém grupos seguintes e inclui falhas de fechamento antes do resumo final."""
    clients = []

    def factory(token, gate):
        """Cria clientes rastreáveis para fechamento centralizado."""
        client = Client(close_error)
        clients.append(client)
        return client

    def collect(client, row):
        """Falha somente no candidato intermediário com texto de credencial proibido."""
        if row["repository_id"] == 2:
            raise GithubException(503, {"message": "secret"}, {"Authorization": "secret"})
        return [], []

    monkeypatch.setattr(extraction, "Analyzer", EmptyAnalyzer)
    monkeypatch.setattr(extraction, "create_client", factory)
    monkeypatch.setattr(extraction, "collect_context", collect)
    rows = list(legacy_extract("token", [candidate(i) for i in range(1, 4)],
                                                SOURCE, SETTINGS, workers=workers))
    repositories = [row for row in rows if row["record_type"] == "repository"]
    assert [row["repository_id"] for row in repositories] == [1, 2, 3]
    assert [row["extraction_status"] for row in repositories] == ["completed", "failed", "completed"]
    assert rows[-1]["error_count"] == 1 + (len(clients) if close_error else 0)
    assert rows[-1]["status"] == "partial"
    assert all(client.closed_by == [get_ident()] for client in clients)
    assert "secret" not in json.dumps(rows)


def test_zero_eligible_has_no_network(monkeypatch):
    """Garante execução vazia completed, mesmo com origem parcial, sem criar cliente."""
    def forbidden(*args, **kwargs):
        """Falha se a execução vazia tentar criar recursos de rede."""
        pytest.fail("Execução vazia não deve criar clientes.")

    monkeypatch.setattr(extraction, "create_client", forbidden)
    rows = list(extraction.extract_repositories("token", [], {**SOURCE, "status": "partial"}, SETTINGS))
    assert len(rows) == 2
    assert rows[0]["input_count"] == 3 and rows[0]["eligible_count"] == 0
    assert rows[-1]["status"] == "completed" and rows[-1]["repository_count"] == 0


def test_catalog_order_ignores_worker_completion(monkeypatch):
    """Atribui motivos pela publicação ordenada mesmo com conclusão inversa dos workers."""
    from test_extraction_format import instance, repository

    second_done = Event()
    completed = []

    def factory(token, gate):
        """Cria somente cliente local rastreável sem conexão externa."""
        return Client()

    def extract(client, row, analyzer, setup_error=None):
        """Conclui o segundo primeiro e fornece motivos distintos em cada grupo."""
        identity = row["repository_id"]
        if identity == 1:
            assert second_done.wait(5)
        completed.append(identity)
        if identity == 2:
            second_done.set()
        repo = repository(identity)
        unit = instance(repo)
        unit["criteria"]["first"]["reason"] = f"motivo-{identity}"
        return [repo, unit]

    monkeypatch.setattr(extraction, "Analyzer", EmptyAnalyzer)
    monkeypatch.setattr(extraction, "create_client", factory)
    monkeypatch.setattr(extraction, "_extract_candidate", extract)
    rows = list(legacy_extract("token", [candidate(1), candidate(2)], SOURCE, SETTINGS, workers=2))
    assert completed == [2, 1]
    definitions = [row for row in rows if row["record_type"] == "criterion_reason"]
    assert [(row["reason_id"], row["text"]) for row in definitions] == [
        ("R001", "motivo-1"), ("R002", "pendente\ud800"), ("R003", "motivo-2")]


@pytest.mark.parametrize("failure", ["input", "preflight", "git", "token", "exists"])
def test_output_sentinel_survives_before_open(tmp_path, monkeypatch, failure):
    """Preserva saída existente diante de entrada/configuração inválida ou recusa explícita."""
    input_path, output_path = tmp_path / "input.jsonl", tmp_path / "output.jsonl"
    save_rows(input_path, triage_rows([]))
    output_path.write_bytes(b"researcher sentinel\r\n")
    monkeypatch.setattr(config, "EXTRACTION_INPUT_PATH", input_path)
    monkeypatch.setattr(config, "EXTRACTION_OUTPUT_PATH", output_path)
    monkeypatch.setenv("GITHUB_TOKEN", "token")

    def settings():
        """Simula preflight inválido sem iniciar subprocessos ou consultas de candidatos."""
        if failure == "preflight":
            raise ValueError("Dependência confiável ausente.")
        return SETTINGS

    def forbidden(*args, **kwargs):
        """Impede início de rede quando a configuração não foi aceita."""
        pytest.fail("Não deveria iniciar extração.")

    monkeypatch.setattr(cli, "preflight", settings)
    if failure == "git":
        def bad_git(self):
            """Falha localmente antes da abertura mesmo com overwrite autorizado."""
            raise ValueError("Git indisponível.")
        monkeypatch.setattr(cli.GitTransport, "preflight", bad_git)
    monkeypatch.setattr(cli, "extract_repositories", forbidden)
    if failure == "input":
        input_path.write_bytes(b"invalid")
    elif failure == "token":
        monkeypatch.delenv("GITHUB_TOKEN")
    argv = ["extract"] + ([] if failure == "exists" else ["--overwrite"])
    assert cli.main(argv) == 2
    assert output_path.read_bytes() == b"researcher sentinel\r\n"


@pytest.mark.parametrize("test_name", ["double", r"\ud800"])
def test_cli_real_jsonl_and_origin(tmp_path, monkeypatch, test_name):
    """Exerce análise e escrita reais, preservando nomes inclusive com surrogate isolado."""
    try:
        settings = preflight()
    except ValueError as error:
        pytest.skip(str(error))
    texts = [{"path": "a.ts", "content": "/** Dobra a entrada. */\nexport function double(x:number) { return x * 2; }\n",
              "sha256": "unused"},
             {"path": "a.test.ts", "content": "import { test } from 'node:test';\nimport { double } from './a';\ntest('"
              + test_name + "', () => { double(2); });\n",
              "sha256": "unused"}]
    for item in texts:
        item["sha256"] = hashlib.sha256(item["content"].encode()).hexdigest()
    input_path, output_path = tmp_path / "input.jsonl", tmp_path / "output.jsonl"
    state = make_local_snapshot(tmp_path, {item["path"]: item["content"] for item in texts})
    save_rows(input_path, triage_rows([{**candidate(), **state.candidate}]))
    monkeypatch.setattr(config, "EXTRACTION_INPUT_PATH", input_path)
    monkeypatch.setattr(config, "EXTRACTION_OUTPUT_PATH", output_path)
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    def configured():
        """Retorna o preflight real já concluído para a invocação CLI."""
        return settings

    def factory(token, gate):
        """Cria cliente local sem abrir rede."""
        client = Client()
        client.get_repo = state.client.get_repo
        return client

    def collect(client, row):
        """Entrega textos fixos preservados sem executar código candidato."""
        return texts, []

    monkeypatch.setattr(cli, "preflight", configured)
    monkeypatch.setattr(extraction, "create_client", factory)
    monkeypatch.setattr(extraction, "collect_context", collect)
    def store_factory(**kwargs):
        """Injeta somente a fixture Git local na execução pública V3."""
        return state.store

    def text_path(item):
        """Compara textos na ordem canônica produzida pela projeção."""
        return item["path"]

    monkeypatch.setattr(extraction, "SnapshotStore", store_factory)
    original_extract = extraction._extract_candidate
    raw_instances = []

    def capture_candidate(*args, **kwargs):
        """Captura evidências legadas da análise real para comparação integral."""
        group = original_extract(*args, **kwargs)
        raw_instances.extend(row for row in group if row["record_type"] == "instance")
        return group

    monkeypatch.setattr(extraction, "_extract_candidate", capture_candidate)
    assert cli.main(["extract", "--workers", "1"]) == 0
    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["schema_version"] == rows[-1]["schema_version"] == 3
    reasons = {row["reason_id"]: row["text"] for row in rows if row["record_type"] == "criterion_reason"}
    published = next(row for row in rows if row["record_type"] == "instance")
    instance = expand_instance(published, rows[1], reasons)
    assert [instance] == raw_instances
    assert rows[1]["context"]["files"] == sorted(texts, key=text_path)
    assert instance["repository_id"] == rows[1]["repository_id"] == 1
    assert instance["commit_sha"] == state.candidate["commit_sha"]
    assert instance["validation"]["executed_test_cases"] is None
    assert instance["tests"][0]["name"] == json.loads('"' + test_name + '"')
    assert instance["context_paths"] == sorted(item["path"] for item in texts)
    assert instance["function"]["reference_code"] in texts[0]["content"]
    assert "context_paths" not in published and "commit_sha" not in published
    assert output_path.read_text(encoding="utf-8").splitlines() == [
        json.dumps(row, ensure_ascii=True, separators=(",", ":")) for row in rows]
    assert rows[-1]["event"] == "run_finished" and rows[-1]["status"] == "completed"
    assert rows[-1]["instance_count"] == rows[-1]["approved"] == 1


def test_write_failure_closes_generator_pool_and_clients(tmp_path, monkeypatch):
    """Garante limpeza integral quando o consumidor falha durante a publicação."""
    input_path = tmp_path / "input.jsonl"
    save_rows(input_path, triage_rows([candidate(), candidate(2)]))
    clients = []
    analyzers = []

    def factory(token, gate):
        """Registra clientes abertos durante a tentativa de escrita."""
        client = Client()
        clients.append(client)
        return client

    def configured():
        """Fornece configuração confiável simulada antes de abrir a saída."""
        return SETTINGS

    def analyzer_factory(settings):
        """Registra o cancelamento obrigatório do auxiliar compartilhado."""
        analyzer = EmptyAnalyzer(settings)
        analyzers.append(analyzer)
        return analyzer

    def collect(client, row):
        """Fornece snapshot vazio sem conexão com o GitHub."""
        return [], []

    def fail_write(path, records, *, ensure_ascii=False, compact=False):
        """Interrompe após abrir o pool e publicar o primeiro contexto."""
        for record in records:
            if record["record_type"] == "repository":
                raise OSError("Disco indisponível.")

    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setattr(config, "EXTRACTION_INPUT_PATH", input_path)
    monkeypatch.setattr(config, "EXTRACTION_OUTPUT_PATH", tmp_path / "output.jsonl")
    monkeypatch.setattr(cli, "preflight", configured)
    monkeypatch.setattr(cli, "write_jsonl", fail_write)
    monkeypatch.setattr(cli, "extract_repositories", legacy_extract)
    monkeypatch.setattr(extraction, "Analyzer", analyzer_factory)
    monkeypatch.setattr(extraction, "create_client", factory)
    monkeypatch.setattr(extraction, "collect_context", collect)
    assert cli.main(["extract", "--workers", "2"]) == 1
    assert clients and all(client.closed_by == [get_ident()] for client in clients)
    assert analyzers[0].cancelled


@pytest.mark.parametrize("snapshot_mode", [False, True])
def test_interrupt_cancels_analyzer_before_pool_wait(monkeypatch, tmp_path, snapshot_mode):
    """Interrompe result em voo e exige cancelamento do auxiliar antes de shutdown esperar."""
    entered, cancelled = Event(), Event()
    order, clients = [], []
    interruption = KeyboardInterrupt()
    executor = ThreadPoolExecutor(max_workers=1)
    original_submit, original_shutdown = executor.submit, executor.shutdown

    class BlockingAnalyzer(EmptyAnalyzer):
        """Simula auxiliar em voo cujo encerramento depende do callback do pool."""

        def analyze(self, files, issues):
            """Sinaliza início e permanece em voo até o cancelamento explícito."""
            entered.set()
            assert cancelled.wait(5)
            return super().analyze(files, issues)

        def cancel(self):
            """Libera o auxiliar e registra cada chamada para detectar duplicações."""
            order.append("cancel")
            cancelled.set()

    class BlockingStore:
        """Simula aquisição em voo que só termina após cancelamento do coordenador."""

        def __init__(self, **kwargs):
            """Mantém Git local confiável e raiz temporária sem qualquer aquisição real."""
            self.root = tmp_path
            self.transport = GitTransport()

        def acquire(self, candidate, client, *, cancel=None):
            """Espera o evento comum e preserva cancelamento como falha operacional."""
            entered.set()
            assert cancel.wait(5)
            raise SnapshotUnavailable("SNAPSHOT_CANCELLED", "Aquisição cancelada.")

        def cancel(self):
            """Confirma que o transporte foi cancelado antes de shutdown esperar."""
            order.append("store_cancel")
            self.transport.cancel()

    def submit(*args, **kwargs):
        """Injeta interrupção no coordenador enquanto a tarefa continua ativa."""
        future = original_submit(*args, **kwargs)

        def interrupt_result(timeout=None):
            """Espera o auxiliar iniciar e interrompe a obtenção do primeiro resultado."""
            assert entered.wait(5)
            raise interruption

        future.result = interrupt_result
        return future

    def shutdown(*args, **kwargs):
        """Verifica a ordem sem medir duração e recolhe o executor mesmo em falha."""
        try:
            assert cancelled.is_set()
            order.append("shutdown")
        finally:
            cancelled.set()
            original_shutdown(*args, **kwargs)

    def executor_factory(**kwargs):
        """Entrega executor real instrumentado para verificar a ordem de fechamento."""
        return executor

    def factory(token, gate):
        """Registra o cliente aberto pelo worker que será interrompido."""
        client = Client()
        clients.append(client)
        return client

    def collect(client, row):
        """Entrega contexto vazio sem bloquear em rede."""
        return [], []

    monkeypatch.setattr(executor, "submit", submit)
    monkeypatch.setattr(executor, "shutdown", shutdown)
    monkeypatch.setattr(concurrency, "ThreadPoolExecutor", executor_factory)
    monkeypatch.setattr(extraction, "Analyzer", BlockingAnalyzer)
    monkeypatch.setattr(extraction, "create_client", factory)
    monkeypatch.setattr(extraction, "collect_context", collect)
    monkeypatch.setattr(extraction, "SnapshotStore", BlockingStore)
    extract = extraction.extract_repositories if snapshot_mode else legacy_extract
    records = extract("token", [candidate()], SOURCE, SETTINGS, workers=1)
    try:
        assert next(records)["event"] == "run_started"
        with pytest.raises(KeyboardInterrupt) as caught:
            next(records)
        assert caught.value is interruption
        assert order == (["cancel", "store_cancel", "shutdown"] if snapshot_mode else ["cancel", "shutdown"])
        assert clients and all(client.closed_by == [get_ident()] for client in clients)
    finally:
        cancelled.set()
        records.close()
        original_shutdown(wait=True)


def test_cli_v3_cache_reuse_offline_consumer_and_resource_cleanup(snapshot_case, tmp_path, monkeypatch):
    """Integra CLI, estados científicos, overwrite JSONL e reutilização sem API/fetch."""
    state = snapshot_case
    input_path, output_path = tmp_path / "triage.jsonl", tmp_path / "extract.jsonl"
    save_rows(input_path, triage_rows([{**candidate(), **state.candidate}]))
    monkeypatch.setattr(config, "EXTRACTION_INPUT_PATH", input_path)
    monkeypatch.setattr(config, "EXTRACTION_OUTPUT_PATH", output_path)
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-token")
    stores, analyzers = [], []
    original_analyzer = extraction.Analyzer

    def store_factory(**kwargs):
        """Usa transporte local na primeira passagem e cache offline nas seguintes."""
        store = state.store if not stores else SnapshotStore(state.store.root)
        stores.append(store)
        return store

    def analyzer_factory(settings):
        """Registra recursos reais para comprovar fechamento após a publicação."""
        analyzer = original_analyzer(settings)
        analyzers.append(analyzer)
        return analyzer

    def get_repo(identity):
        """Recusa metadados na segunda passagem, inclusive no caminho CLI público."""
        assert len(stores) == 1
        return state.client.get_repo(identity)

    def client_factory(token, gate):
        """Cria cliente sem rede com metadados controlados pela fixture."""
        client = Client()
        client.get_repo = get_repo
        return client

    monkeypatch.setattr(extraction, "SnapshotStore", store_factory)
    monkeypatch.setattr(extraction, "Analyzer", analyzer_factory)
    monkeypatch.setattr(extraction, "create_client", client_factory)
    assert cli.main(["extract", "--workers", "1"]) == 1
    rows = [json.loads(line) for line in output_path.read_text().splitlines()]
    repository = next(row for row in rows if row["record_type"] == "repository")
    units = [row for row in rows if row["record_type"] == "instance"]
    assert {unit["decision"] for unit in units} == {"approved", "rejected", "not_evaluated"}
    assert rows[-1]["error_count"] == rows[-1]["issue_count"] == 1
    assert [issue["code"] for issue in repository["issues"]] == ["UNPROVEN_IMPLEMENTATION"]
    assert rows[-1]["cache_misses"] == 1 and rows[-1]["cache_hits"] == 0
    assert repository["execution_requirements"] and "inventory" not in repository["context"]
    reference = repository["context"]["snapshot"]
    offline = SnapshotStore(state.store.root)
    artifact = offline.open_verified(reference)
    manifest_before = (artifact.storage_path / "manifest.json").read_bytes()
    reasons = {row["reason_id"]: row["text"] for row in rows if row["record_type"] == "criterion_reason"}
    approved = [unit for unit in units if unit["decision"] == "approved"]
    assert len(approved) == 1
    loaded = load_instance_context(approved[0], repository, reasons, offline)
    assert loaded["instance"]["function"]["name"] == "increment"
    assert cli.main(["extract", "--overwrite", "--workers", "1"]) == 1
    repeated = [json.loads(line) for line in output_path.read_text().splitlines()]
    assert repeated[-1]["cache_hits"] == 1 and repeated[-1]["cache_misses"] == 0
    assert state.calls == [1] and state.transport.fetches == 1
    assert (artifact.storage_path / "manifest.json").read_bytes() == manifest_before
    assert all(analyzer._cancelled and not analyzer._processes for analyzer in analyzers)
    assert all(store.transport._cancelled.is_set() for store in stores)


def test_snapshot_operational_limit_is_failed_without_scientific_rejection(snapshot_case):
    """Limite de aquisição não cria ausência científica nem snapshot parcial publicado."""
    state = snapshot_case
    state.transport.max_entries = 1
    group = extraction._extract_candidate(state.client, state.candidate,
                                          extraction.Analyzer(preflight()), store=state.store)
    rows = list(compact_records([extraction.diagnostic("run_started", "início"), *group,
                               extraction.diagnostic("run_finished", "fim")], schema_version=3))
    repository = rows[1]
    assert repository["extraction_status"] == "failed"
    assert repository["summary"]["rejected"] == repository["summary"]["instance_count"] == 0
    assert repository["context"]["snapshot"] is None
    assert repository["issues"][0]["code"] == "SNAPSHOT_ENTRY_LIMIT"
    assert not list(state.store.root.rglob("complete.json"))


@pytest.mark.parametrize("sha_case", ["upper", "mixed"])
def test_public_extraction_canonicalizes_sha_without_changing_identity(tmp_path, monkeypatch, sha_case):
    """SHA válido com caixa variável reutiliza cache e publica V3 completo com os mesmos IDs."""
    state = make_local_snapshot(tmp_path, {
        "math.ts": "/** Incrementa a entrada. */\nexport function increment(value: number) { return value + 1; }\n",
        "math.test.ts": "import { test } from 'vitest';\nimport { increment } from './math';\n"
                        "test('incrementa', () => { increment(1); });\n",
    })
    artifact = state.store.acquire(state.candidate, state.client)
    settings = preflight()

    def forbidden_metadata(identity):
        """Cache preenchido não permite consulta de metadados nem aquisição adicional."""
        pytest.fail("Cache hit não deve consultar metadados.")

    def client_factory(token, gate):
        """Fornece cliente sem rede e falha explicitamente caso o cache seja ignorado."""
        client = Client()
        client.get_repo = forbidden_metadata
        return client

    monkeypatch.setattr(extraction, "create_client", client_factory)
    baseline = list(extraction.extract_repositories(
        "token", [state.candidate], SOURCE, settings, workers=1,
        store=SnapshotStore(state.store.root)))
    sha = state.candidate["commit_sha"]
    varied_sha = sha.upper() if sha_case == "upper" else sha[:20].upper() + sha[20:]
    input_path = tmp_path / "triage.jsonl"
    save_rows(input_path, triage_rows([{**candidate(), **state.candidate, "commit_sha": varied_sha}]))
    candidates, source = load_triage(input_path)
    assert candidates[0]["commit_sha"] == varied_sha
    rows = list(extraction.extract_repositories(
        "token", candidates, source, settings, workers=1, store=SnapshotStore(state.store.root)))
    assert rows[-1]["event"] == "run_finished" and rows[-1]["status"] == "completed"
    assert rows[-1]["cache_hits"] == 1 and rows[-1]["cache_misses"] == 0
    assert rows[-1]["error_count"] == 0
    repository = next(row for row in rows if row["record_type"] == "repository")
    assert repository["commit_sha"] == sha
    assert repository["context"]["snapshot"] == artifact.reference()
    assert [row["instance_id"] for row in rows if row["record_type"] == "instance"] == [
        row["instance_id"] for row in baseline if row["record_type"] == "instance"]
    assert candidates[0]["commit_sha"] == varied_sha
    assert state.calls == [1] and state.transport.fetches == 1


@pytest.mark.parametrize("boundary", ["acquisition", "projection"])
def test_public_cache_io_failure_preserves_next_candidate_and_cleanup(tmp_path, monkeypatch, boundary):
    """Falha local do primeiro candidato não interrompe o segundo nem expõe caminhos privados."""
    state = make_local_snapshot(tmp_path, {
        "math.ts": "/** Incrementa. */\nexport function increment(value: number) { return value + 1; }\n",
        "math.test.ts": "import { test } from 'vitest';\nimport { increment } from './math';\n"
                        "test('incrementa', () => { increment(1); });\n",
    })
    first = {**state.candidate, "repository_id": 2}
    state.store.acquire(state.candidate, state.client)
    if boundary == "projection":
        def local_metadata(identity):
            """Permite preencher somente o cache local do candidato cuja projeção falhará."""
            return SimpleNamespace(id=identity, private=False, full_name="fixture/public")

        state.store.acquire(first, SimpleNamespace(get_repo=local_metadata))
    fetches = state.transport.fetches
    store = SnapshotStore(state.store.root)
    clients, analyzers = [], []
    original_mkdir = Path.mkdir
    original_projection = extraction.project_snapshot
    original_analyzer = extraction.Analyzer

    def fail_cache_mkdir(path, *args, **kwargs):
        """Reproduz PermissionError real no mkdir do store, antes de qualquer metadado."""
        if path == store.root / "2":
            raise PermissionError("private-cache-secret")
        return original_mkdir(path, *args, **kwargs)

    def fail_projection(store, artifact, *, cancel=None):
        """Simula erro de leitura local somente na projeção do primeiro candidato."""
        if artifact.repository_id == 2:
            raise PermissionError("private-cache-secret")
        return original_projection(store, artifact, cancel=cancel)

    def forbidden_metadata(identity):
        """Impede rede tanto na falha local quanto no cache hit do candidato seguinte."""
        pytest.fail("A execução deve usar somente o cache local.")

    def client_factory(token, gate):
        """Registra cliente exclusivo do worker para conferir fechamento centralizado."""
        client = Client()
        client.get_repo = forbidden_metadata
        clients.append(client)
        return client

    def analyzer_factory(settings):
        """Registra o analisador real para conferir cancelamento e coleta dos processos."""
        analyzer = original_analyzer(settings)
        analyzers.append(analyzer)
        return analyzer

    if boundary == "acquisition":
        monkeypatch.setattr(Path, "mkdir", fail_cache_mkdir)
    else:
        monkeypatch.setattr(extraction, "project_snapshot", fail_projection)
    monkeypatch.setattr(extraction, "create_client", client_factory)
    monkeypatch.setattr(extraction, "Analyzer", analyzer_factory)
    rows = list(extraction.extract_repositories(
        "token", [first, state.candidate], {**SOURCE, "input_count": 2}, preflight(),
        workers=1, store=store))
    repositories = [row for row in rows if row["record_type"] == "repository"]
    assert [row["repository_id"] for row in repositories] == [2, 1]
    assert [row["extraction_status"] for row in repositories] == ["failed", "completed"]
    assert repositories[0]["summary"]["instance_count"] == 0
    assert repositories[0]["issues"][0]["code"] == "SNAPSHOT_CACHE_IO_ERROR"
    assert rows[-1]["event"] == "run_finished" and rows[-1]["status"] == "partial"
    assert rows[-1]["repository_count"] == 2
    assert rows[-1]["instance_count"] == rows[-1]["approved"] == 1
    assert rows[-1]["rejected"] == rows[-1]["not_evaluated"] == 0
    assert rows[-1]["error_count"] == rows[-1]["issue_count"] == 1
    assert "private-cache-secret" not in json.dumps(rows)
    assert state.transport.fetches == fetches
    assert clients and all(client.closed_by == [get_ident()] for client in clients)
    assert not store.transport._cancelled.is_set()
    assert analyzers[0]._cancelled and not analyzers[0]._processes


@pytest.mark.parametrize(("stage", "error_type"), [
    ("acquisition", TypeError), ("projection", KeyError), ("analyzer", OSError),
    ("acquisition", RequestException),
])
def test_cache_io_boundary_preserves_programming_analyzer_and_api_errors(monkeypatch, stage, error_type):
    """A fronteira local não absorve bugs, falhas externas ao cache ou erros próprios da API."""
    failure = error_type("private-error-secret")

    def acquire(row, client, *, cancel=None):
        """Injeta falha na aquisição sem iniciar Git nem consultar rede."""
        if stage == "acquisition":
            raise failure
        return object()

    def project(store, artifact, *, cancel=None):
        """Entrega projeção mínima ou falha de programação controlada."""
        if stage == "projection":
            raise failure
        return SimpleNamespace(files=[], issues=[], snapshot={}, inventory=[], analysis_complete=True)

    def analyze(files, issues, **kwargs):
        """Representa falha do analisador fora da fronteira de I/O do cache."""
        raise failure

    analyzer = EmptyAnalyzer(SETTINGS)
    analyzer.analyze = analyze
    monkeypatch.setattr(extraction, "project_snapshot", project)
    store = SimpleNamespace(acquire=acquire)
    if error_type is RequestException:
        group = extraction._extract_candidate(None, candidate(), analyzer, store=store)
        assert group[0]["extraction_status"] == "failed"
        assert group[0]["issues"][0]["code"] == "SNAPSHOT_API_ERROR"
        assert "private-error-secret" not in json.dumps(group)
    else:
        with pytest.raises(error_type) as caught:
            extraction._extract_candidate(None, candidate(), analyzer, store=store)
        assert caught.value is failure


def test_public_external_store_survives_completion_consumer_and_second_pass(tmp_path, monkeypatch):
    """Mesma instância externa serve extração, consumidor offline e segunda passagem pelo cache."""
    source_text = "/** Incrementa. */\nexport function increment(value: number) { return value + 1; }\n"
    state = make_local_snapshot(tmp_path, {
        "math.ts": source_text,
        "math.test.ts": "import { test } from 'vitest';\nimport { increment } from './math';\n"
                        "test('incrementa', () => { increment(1); });\n",
    })
    clients, analyzers = [], []
    original_analyzer = extraction.Analyzer

    def metadata(identity):
        """Permite somente os metadados da primeira aquisição Git local."""
        assert state.calls == []
        return state.client.get_repo(identity)

    def client_factory(token, gate):
        """Registra clientes sem rede para conferir fechamento a cada passagem."""
        client = Client()
        client.get_repo = metadata
        clients.append(client)
        return client

    def analyzer_factory(settings):
        """Confere fechamento do analisador interno sem invalidar o store externo."""
        analyzer = original_analyzer(settings)
        analyzers.append(analyzer)
        return analyzer

    monkeypatch.setattr(extraction, "create_client", client_factory)
    monkeypatch.setattr(extraction, "Analyzer", analyzer_factory)
    settings = preflight()
    rows = list(extraction.extract_repositories(
        "token", [state.candidate], SOURCE, settings, workers=1, store=state.store))
    assert rows[-1]["event"] == "run_finished" and rows[-1]["status"] == "completed"
    repository = next(row for row in rows if row["record_type"] == "repository")
    unit = next(row for row in rows if row["record_type"] == "instance")
    reference = repository["context"]["snapshot"]
    artifact = state.store.open_verified(reference)
    assert state.store.read_blob(artifact, "math.ts") == source_text.encode()
    reasons = {row["reason_id"]: row["text"] for row in rows if row["record_type"] == "criterion_reason"}
    loaded = load_instance_context(unit, repository, reasons, state.store)
    assert loaded["snapshot"] == reference and loaded["instance"]["decision"] == "approved"
    repeated = list(extraction.extract_repositories(
        "token", [state.candidate], SOURCE, settings, workers=1, store=state.store))
    assert repeated[-1]["event"] == "run_finished" and repeated[-1]["status"] == "completed"
    assert repeated[-1]["cache_hits"] == 1 and repeated[-1]["cache_misses"] == 0
    assert next(row for row in repeated if row["record_type"] == "instance")["instance_id"] == unit["instance_id"]
    assert state.store.open_verified(reference) == artifact
    assert not state.transport._cancelled.is_set()
    assert state.transport.fetches == 1 and state.calls == [1]
    assert len(clients) == 2 and all(client.closed_by == [get_ident()] for client in clients)
    assert len(analyzers) == 2 and all(analyzer._cancelled and not analyzer._processes for analyzer in analyzers)


def test_closing_public_generator_cancels_external_store_and_analyzer(tmp_path, monkeypatch):
    """Abandono com aquisição em voo cancela ambos e não reativa o store abortado."""
    state = make_local_snapshot(tmp_path, {
        "math.ts": "/** Incrementa. */\nexport function increment(value: number) { return value + 1; }\n",
        "math.test.ts": "import { test } from 'vitest';\nimport { increment } from './math';\n"
                        "test('incrementa', () => { increment(1); });\n",
    })
    artifact = state.store.acquire(state.candidate, state.client)
    entered, exited = Event(), Event()
    order, analyzers, clients = [], [], []
    original_acquire, original_cancel = state.store.acquire, state.store.cancel
    original_analyzer = extraction.Analyzer

    def acquire(row, client, *, cancel=None):
        """Mantém a segunda aquisição em voo até o evento de aborto do coordenador."""
        if row["repository_id"] == 2:
            entered.set()
            try:
                assert cancel.wait(10)
                raise SnapshotUnavailable("SNAPSHOT_CANCELLED", "Aquisição cancelada.")
            finally:
                exited.set()
        return original_acquire(row, client, cancel=cancel)

    def cancel_store():
        """Cancela permanentemente o transporte externo somente por aborto."""
        order.append("git_cancel")
        original_cancel()

    def analyzer_factory(settings):
        """Instrumenta cancelamento do analisador real pertencente à execução."""
        analyzer = original_analyzer(settings)
        original_node_cancel = analyzer.cancel

        def cancel_node():
            """Registra cancelamento de Node antes da espera dos workers."""
            order.append("node_cancel")
            original_node_cancel()

        analyzer.cancel = cancel_node
        analyzers.append(analyzer)
        return analyzer

    def forbidden_metadata(identity):
        """Impede consulta externa ao usar o snapshot já adquirido localmente."""
        pytest.fail("O teste não permite rede.")

    def client_factory(token, gate):
        """Registra fechamento dos clientes após o aborto do gerador público."""
        client = Client()
        client.get_repo = forbidden_metadata
        clients.append(client)
        return client

    monkeypatch.setattr(state.store, "acquire", acquire)
    monkeypatch.setattr(state.store, "cancel", cancel_store)
    monkeypatch.setattr(extraction, "Analyzer", analyzer_factory)
    monkeypatch.setattr(extraction, "create_client", client_factory)
    records = extraction.extract_repositories(
        "token", [state.candidate, {**state.candidate, "repository_id": 2}], SOURCE,
        preflight(), workers=2, store=state.store)
    try:
        assert next(records)["event"] == "run_started"
        assert next(records)["record_type"] == "repository"
        assert entered.wait(10)
    finally:
        records.close()
    assert exited.is_set() and order == ["node_cancel", "git_cancel"]
    assert state.transport._cancelled.is_set()
    assert analyzers[0]._cancelled and not analyzers[0]._processes
    assert clients and all(client.closed_by == [get_ident()] for client in clients)
    with pytest.raises(SnapshotUnavailable) as caught:
        state.store.open_verified(artifact.reference())
    assert caught.value.code == "SNAPSHOT_CANCELLED"
