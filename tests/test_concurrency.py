"""Verifica ordem, integridade no desligamento e pausas compartilhadas sem rede."""

from concurrent.futures import CancelledError, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from io import BytesIO
import json
from threading import Barrier, Event, get_ident

from github import Github, GithubException
from github.Requester import Requester
import pytest
from urllib3.exceptions import MaxRetryError
from urllib3.response import HTTPResponse

from robo_minerador import concurrency
from robo_minerador.concurrency import RateLimitGate, WorkerPool
from robo_minerador.github_api import _CoordinatedRetry, create_client


class Client:
    """Cliente rastreável para detectar compartilhamento e fechamento prematuro."""

    def __init__(self, close_error=None):
        """Registra a thread criadora e uma possível falha ao fechar."""
        self.owner = get_ident()
        self.closed_by = []
        self.close_error = close_error

    def close(self):
        """Registra o fechamento antes de propagar a falha configurada."""
        self.closed_by.append(get_ident())
        if self.close_error is not None:
            raise self.close_error


class Clock:
    """Relógios controlados para liberar esperas sem dormir de verdade."""

    def __init__(self):
        """Inicia relógios civil e monotônico em escalas distintas."""
        self.now = 100.0

    def monotonic(self):
        """Retorna o instante monotônico simulado."""
        return self.now

    def wall(self):
        """Retorna o instante civil simulado."""
        return 1000.0 + self.now


def response(status, message="", headers=None):
    """Monta uma resposta real do urllib3 sem abrir conexão de rede."""
    return HTTPResponse(status=status, body=BytesIO(json.dumps({"message": message}).encode()),
                        headers=headers or {}, preload_content=False)


def watch_waits(gate, monkeypatch, count=1):
    """Sinaliza quando todas as threads realmente entraram na espera da condição."""
    waiting = Event()
    original = gate._condition.wait
    calls = []

    def wait(timeout=None):
        """Observa a espera sob o lock original, preservando sua sincronização."""
        calls.append(get_ident())
        if len(set(calls)) >= count:
            waiting.set()
        return original(timeout)

    monkeypatch.setattr(gate._condition, "wait", wait)
    return waiting


def test_overlap_order_and_clients_reused_across_pages():
    """Impede serialização oculta, troca de ordem e compartilhamento de sessões."""
    coordinator = get_ident()
    clients = []
    barrier = Barrier(2)
    second_done = Event()
    completion = []

    def factory():
        """Cria um cliente por thread e registra sua identidade."""
        client = Client()
        clients.append(client)
        return client

    with WorkerPool(2, factory) as pool:
        def operation(item):
            """Força sobreposição e conclusão inversa dentro de cada página."""
            client = pool.get_client()
            assert client.owner == get_ident()
            assert pool.get_client() is client
            assert not client.closed_by
            barrier.wait(timeout=5)
            if item % 2 == 0:
                assert second_done.wait(5)
            completion.append(item)
            if item % 2:
                second_done.set()
            return item

        assert list(pool.map_ordered(operation, [0, 1])) == [0, 1]
        second_done.clear()
        assert list(pool.map_ordered(operation, [2, 3])) == [2, 3]
        assert len(clients) == 2
        assert len({client.owner for client in clients}) == 2
    assert completion == [1, 0, 3, 2]
    assert all(client.closed_by == [coordinator] for client in clients)


def test_bounded_input_and_generator_close():
    """Evita submissão integral e garante limpeza quando o consumidor para cedo."""
    consumed = []
    clients = []

    def items():
        """Registra consumo antecipado de uma entrada muito maior que a janela."""
        for item in range(1000):
            consumed.append(item)
            yield item

    def factory():
        """Rastreia todos os clientes que precisam ser fechados."""
        client = Client()
        clients.append(client)
        return client

    with WorkerPool(2, factory) as pool:
        def operation(item):
            """Usa um cliente antes de produzir o resultado."""
            pool.get_client()
            return item

        results = pool.map_ordered(operation, items())
        assert next(results) == 0
        assert consumed == [0, 1]
        assert pool.window_size == 2
        results.close()
        assert not pool._pending
        with pytest.raises(RuntimeError, match="fechado"):
            list(pool.map_ordered(operation, [1]))
    assert all(client.closed_by == [get_ident()] for client in clients)


@pytest.mark.parametrize("original", [False, True])
def test_close_errors_do_not_hide_programming_failures(original):
    """Separa erros operacionais e preserva o defeito original após fechar tudo."""
    expected = GithubException(503, {"message": "indisponível"})
    defect = ValueError("defeito no fechamento")
    clients = []
    barrier = Barrier(2)

    def factory():
        """Distribui falhas distintas de fechamento entre clientes isolados."""
        client = Client(expected if not clients else defect)
        clients.append(client)
        return client

    pool = WorkerPool(2, factory)

    def operation(item):
        """Garante dois clientes vivos antes de eventualmente falhar."""
        pool.get_client()
        barrier.wait(timeout=5)
        if original and item == 0:
            raise RuntimeError("defeito original")
        return item

    with pytest.raises(RuntimeError if original else ValueError) as caught:
        with pool:
            list(pool.map_ordered(operation, [0, 1]))
    assert pool.close_errors == (expected,)
    assert len(clients) == 2
    assert all(client.closed_by == [get_ident()] for client in clients)
    if original:
        assert str(caught.value) == "defeito original"
        assert "ValueError" in caught.value.__notes__[0]


def test_factory_error_stays_inside_operation_handler():
    """Permite que a etapa transforme falha esperada da fábrica em diagnóstico."""
    def factory():
        """Simula falha esperada antes da primeira sessão existir."""
        raise GithubException(503, {"message": "indisponível"})

    with WorkerPool(1, factory) as pool:
        def operation(item):
            """Captura a falha do cliente junto com as demais chamadas do candidato."""
            try:
                pool.get_client()
            except GithubException:
                return "diagnostic"
            pytest.fail("A fábrica deveria falhar.")

        assert list(pool.map_ordered(operation, [1])) == ["diagnostic"]


def test_generator_close_propagates_programming_error():
    """Impede que GeneratorExit oculte defeitos internos durante abandono do mapa."""
    def factory():
        """Cria um cliente com defeito de fechamento observável pelo coordenador."""
        return Client(ValueError("defeito no fechamento"))

    with WorkerPool(1, factory) as pool:
        def operation(item):
            """Abre o cliente antes de produzir o primeiro item do mapa."""
            pool.get_client()
            return item

        results = pool.map_ordered(operation, range(10))
        assert next(results) == 0
        with pytest.raises(ValueError, match="defeito no fechamento"):
            results.close()
        assert pool.close_errors == ()


@pytest.mark.parametrize("original", [False, True])
def test_unconsumed_worker_failure_survives_shutdown(original):
    """Evita perder defeito de tarefa concluída quando o consumidor abandona o mapa."""
    failed = Event()
    clients = []
    defect = ValueError("defeito na segunda tarefa")
    failure = RuntimeError("defeito no consumidor")

    def factory():
        """Registra clientes que devem fechar mesmo com defeito não consumido."""
        client = Client()
        clients.append(client)
        return client

    with pytest.raises(RuntimeError if original else ValueError) as caught:
        with WorkerPool(2, factory) as pool:
            def operation(item):
                """Falha na segunda tarefa antes de liberar o primeiro resultado."""
                pool.get_client()
                if item == 0:
                    assert failed.wait(5)
                    return item
                failed.set()
                raise defect

            results = pool.map_ordered(operation, [0, 1])
            assert next(results) == 0
            if original:
                raise failure
            results.close()
    assert caught.value is (failure if original else defect)
    if original:
        assert any("ValueError: defeito na segunda tarefa" in note
                   for note in caught.value.__notes__)
    assert len(clients) == 2
    assert all(client.closed_by == [get_ident()] for client in clients)
    assert pool.close_errors == ()
    results.close()


def test_consumed_worker_failure_is_not_reported_twice():
    """Impede duplicar no shutdown a exceção que result já entregou ao coordenador."""
    defect = ValueError("defeito já consumido")

    def operation(item):
        """Produz um único defeito que será consumido diretamente pelo mapa."""
        raise defect

    with pytest.raises(ValueError) as caught:
        with WorkerPool(1, Client) as pool:
            list(pool.map_ordered(operation, [0]))
    assert caught.value is defect
    assert not getattr(defect, "__notes__", [])


@pytest.mark.parametrize("original", [False, True])
def test_cancel_callback_failure_preserves_cleanup_and_original(original):
    """Fecha clientes após falha do callback único e preserva a exceção original ativa."""
    calls, clients = [], []
    defect = ValueError("defeito no cancelamento")
    failure = RuntimeError("defeito original")

    def factory():
        """Registra clientes que devem fechar mesmo quando o callback falha."""
        client = Client()
        clients.append(client)
        return client

    def cancel():
        """Registra chamada única antes de simular defeito na limpeza externa."""
        calls.append(get_ident())
        raise defect

    pool = WorkerPool(1, factory, on_cancel=cancel)

    def operation(item):
        """Cria uma sessão real do pool antes do fechamento do coordenador."""
        pool.get_client()
        return item

    with pytest.raises(RuntimeError if original else ValueError) as caught:
        with pool:
            assert list(pool.map_ordered(operation, [1])) == [1]
            if original:
                raise failure
    pool.close()
    assert caught.value is (failure if original else defect)
    assert calls == [get_ident()]
    assert clients and all(client.closed_by == [get_ident()] for client in clients)
    assert not pool._pending
    if original:
        assert any("ValueError: defeito no cancelamento" in note for note in failure.__notes__)


@pytest.mark.parametrize("error", [CancelledError(), GithubException(503, {})])
def test_unconsumed_expected_failure_is_ignored_on_close(error):
    """Mantém cancelamentos e falhas esperadas de tarefa fora dos defeitos de limpeza."""
    failed = Event()

    with WorkerPool(2, Client) as pool:
        def operation(item):
            """Retém a primeira tarefa até a segunda produzir a falha esperada."""
            if item == 0:
                assert failed.wait(5)
                return item
            failed.set()
            raise error

        results = pool.map_ordered(operation, [0, 1])
        assert next(results) == 0
        results.close()
    assert pool.close_errors == ()


def test_input_failure_cancels_queued_futures_before_shutdown(monkeypatch):
    """Impede execução de itens pendentes após falha ao ler a entrada da etapa."""
    release = Event()
    submitted = []

    def initialize():
        """Mantém workers parados antes de retirar qualquer item da fila."""
        assert release.wait(5)

    executor = ThreadPoolExecutor(max_workers=3, initializer=initialize)
    original_submit = executor.submit
    original_shutdown = executor.shutdown

    def submit(*args, **kwargs):
        """Registra futures reais que devem continuar na fila até o cancelamento."""
        future = original_submit(*args, **kwargs)
        submitted.append(future)
        return future

    def shutdown(*args, **kwargs):
        """Exige cancelamento antes de liberar workers e aguarda seu encerramento."""
        try:
            assert len(submitted) == 2
            assert all(future.cancelled() for future in submitted)
        finally:
            release.set()
            original_shutdown(*args, **kwargs)

    def executor_factory(**kwargs):
        """Entrega um executor real com inicialização bloqueada por evento."""
        return executor

    def items():
        """Falha depois de dois itens, antes de completar a janela de submissão."""
        yield 0
        yield 1
        raise ValueError("entrada interrompida")

    def operation(item):
        """Rejeita execução de qualquer tarefa que deveria ter sido cancelada."""
        pytest.fail("A tarefa pendente não deveria executar.")

    monkeypatch.setattr(executor, "submit", submit)
    monkeypatch.setattr(executor, "shutdown", shutdown)
    monkeypatch.setattr(concurrency, "ThreadPoolExecutor", executor_factory)
    with pytest.raises(ValueError, match="entrada interrompida"):
        with WorkerPool(3, Client) as pool:
            list(pool.map_ordered(operation, items()))
    assert len(submitted) == 2
    assert all(future.cancelled() for future in submitted)


@pytest.mark.parametrize("kind", ["primary", "secondary", "retry-after", "429", "429-reset"])
def test_recognized_limits_pause_all_workers_and_preserve_clones(kind, monkeypatch):
    """Impede que workers ultrapassem uma pausa reconhecida em outra sessão."""
    clock = Clock()
    gate = RateLimitGate(clock=clock.monotonic)
    retry = _CoordinatedRetry(gate=gate, cancel=Event(), total=10, backoff_factor=0.1)
    headers = {}
    message = "You have exceeded a secondary rate limit."
    status = 403
    if kind == "primary":
        message = "API rate limit exceeded for fixture."
        headers = {"X-RateLimit-Reset": str(int(datetime.now(timezone.utc).timestamp()) + 60)}
    elif kind == "429-reset":
        status = 429
        headers = {"X-RateLimit-Remaining": "0",
                   "X-RateLimit-Reset": str(int(datetime.now(timezone.utc).timestamp()) + 60)}
    elif kind in ("retry-after", "429"):
        headers = {"Retry-After": "60"}
        status = 429 if kind == "429" else 403
    clone = retry.increment("GET", "/fixture", response(status, message, headers))
    assert clone.gate is gate
    assert clone.cancel is retry.cancel
    assert clone.total == 9
    assert clone.backoff_factor == 0.1
    assert clone.new().gate is gate
    waiting = watch_waits(gate, monkeypatch, count=2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(gate.wait)
        second = executor.submit(clone.sleep, response(status, headers=headers))
        try:
            assert waiting.wait(5)
            assert not first.done() and not second.done()
        finally:
            clock.now += 120
            gate.wake()
        first.result(timeout=5)
        second.result(timeout=5)


def test_denied_403_does_not_pause_and_5xx_backoff_is_local(monkeypatch):
    """Evita pausa global indevida por permissão negada ou erro transitório comum."""
    gate = RateLimitGate()
    pauses = []
    waits = []
    monkeypatch.setattr(gate, "defer", pauses.append)

    def wait_for(seconds, *, cancel=None):
        """Registra backoff local sem esperar nem afetar os demais workers."""
        waits.append(seconds)

    monkeypatch.setattr(gate, "wait_for", wait_for)
    retry = _CoordinatedRetry(gate=gate, cancel=Event(), total=10, backoff_factor=0.1)
    with pytest.raises(GithubException):
        retry.increment("GET", "/fixture", response(403, "Resource not accessible by integration"))
    clone = retry.increment("GET", "/fixture", response(503))
    clone = clone.increment("GET", "/fixture", response(503))
    clone.sleep(response(503))
    assert pauses == []
    assert waits == [pytest.approx(0.2)]
    assert clone.total == 8


@pytest.mark.parametrize("status", [403, 429])
def test_exhausted_retry_still_extends_shared_pause(status):
    """Preserva nova pausa explícita mesmo quando o último retry termina em erro."""
    clock = Clock()
    gate = RateLimitGate(clock=clock.monotonic, wall_clock=clock.wall)
    retry = _CoordinatedRetry(gate=gate, cancel=Event(), total=1)
    retry = retry.increment("GET", "/fixture", response(status, headers={"Retry-After": "60"}))
    assert gate._deadline == 160
    clock.now = 161
    with pytest.raises(MaxRetryError) as caught:
        retry.increment("GET", "/fixture", response(status, headers={"Retry-After": "60"}))
    assert caught.value.reason.args == (f"too many {status} error responses",)
    assert gate._deadline == 221


@pytest.mark.parametrize("kind", ["primary", "secondary", "permission"])
def test_exhausted_403_only_publishes_recognized_limits(kind):
    """Respeita reset e limite secundário no esgotamento sem pausar por permissão."""
    clock = Clock()
    gate = RateLimitGate(clock=clock.monotonic, wall_clock=clock.wall)
    retry = _CoordinatedRetry(gate=gate, cancel=Event(), total=0)
    headers = {}
    if kind == "primary":
        message = "API rate limit exceeded for fixture."
        headers = {"X-RateLimit-Reset": "1160"}
    elif kind == "secondary":
        message = "You have exceeded a secondary rate limit."
    else:
        message = "Resource not accessible by integration"
        headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1160"}
    with pytest.raises(GithubException if kind == "permission" else MaxRetryError):
        retry.increment("GET", "/fixture", response(403, message, headers))
    assert gate._deadline == {"primary": 161, "secondary": 160, "permission": 0}[kind]


def test_http_date_retry_after_uses_library_parser():
    """Mantém suporte a Retry-After civil sem interpretar datas manualmente."""
    gate = RateLimitGate()
    retry = _CoordinatedRetry(gate=gate, cancel=Event(), total=10)
    date = format_datetime(datetime.now(timezone.utc) + timedelta(minutes=2), usegmt=True)
    clone = retry.increment("GET", "/fixture", response(429, headers={"Retry-After": date}))
    assert 100 < clone.get_retry_after(response(429, headers={"Retry-After": date})) <= 120


def test_request_boundary_observes_successful_exhaustion(monkeypatch, capsys):
    """Anuncia a espera após cota consumida com 200, sem esperar tempo real."""
    clock = Clock()
    gate = RateLimitGate(clock=clock.monotonic, wall_clock=clock.wall)
    calls = []

    def request_json(self, *args, **kwargs):
        """Substitui transporte e preserva argumentos posicionais e nomeados."""
        calls.append((args, kwargs))
        return 200, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1160"}, "{}"

    def wait(timeout):
        """Avança o relógio em vez de bloquear ao atingir a fronteira da API."""
        assert timeout == 61
        assert len(calls) == 1
        clock.now += timeout

    monkeypatch.setattr(Requester, "requestJson", request_json)
    monkeypatch.setattr(gate._condition, "wait", wait)
    with create_client("fixture", gate=gate) as client:
        retry = client.requester._Requester__retry
        assert retry.total == Github.default_retry.total == 10
        assert retry.backoff_factor == Github.default_retry.backoff_factor
        assert retry.is_retry("GET", 429)
        assert client.requester.requestJson("GET", "/fixture", parameters={"page": 1})[0] == 200
        assert capsys.readouterr().err == ""
        assert client.requester.requestJson("GET", "/next")[0] == 200
    assert calls[0] == (("GET", "/fixture"), {"parameters": {"page": 1}})
    assert capsys.readouterr().err == (
        "Limite temporário da API: aguardando 61 segundos (pausa compartilhada).\n"
    )


def test_shared_pause_announcement_is_deduplicated_between_workers(monkeypatch, capsys):
    """Evita avisos duplicados de duas threads esperando o mesmo prazo global."""
    clock = Clock()
    gate = RateLimitGate(clock=clock.monotonic, wall_clock=clock.wall)
    waits = []

    def advance():
        """Libera ambas as threads pelo relógio após confirmarem a espera."""
        clock.now += 61.25

    barrier = Barrier(2, action=advance)

    def wait(timeout):
        """Sincroniza duas esperas sem aguardar a duração simulada da pausa."""
        waits.append(timeout)
        gate._condition.release()
        try:
            barrier.wait(timeout=5)
        finally:
            gate._condition.acquire()

    monkeypatch.setattr(gate._condition, "wait", wait)
    gate.defer(61.25)
    assert capsys.readouterr().err == ""
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(gate.wait)
        second = executor.submit(gate.wait)
        first.result(timeout=5)
        second.result(timeout=5)
    assert waits == [61.25, 61.25]
    assert capsys.readouterr().err == (
        "Limite temporário da API: aguardando 62 segundos (pausa compartilhada).\n"
    )


def test_local_backoff_does_not_announce_expired_global_pause(monkeypatch, capsys):
    """Impede que backoff local de 5xx seja anunciado como limite compartilhado."""
    clock = Clock()
    gate = RateLimitGate(clock=clock.monotonic, wall_clock=clock.wall)
    gate.defer(1)
    clock.now += 2

    def wait(timeout):
        """Conclui o backoff local avançando somente o relógio simulado."""
        assert timeout == 5
        clock.now += timeout

    monkeypatch.setattr(gate._condition, "wait", wait)
    gate.wait_for(5)
    assert capsys.readouterr().err == ""


def test_global_extension_is_announced_only_when_waiting(monkeypatch, capsys):
    """Anuncia extensão relevante durante a espera, sem avisar em defer ou wake."""
    clock = Clock()
    gate = RateLimitGate(clock=clock.monotonic, wall_clock=clock.wall)
    waits = []

    def wait(timeout):
        """Simula despertar sem extensão e depois ampliação relevante do prazo."""
        waits.append(timeout)
        if len(waits) == 1:
            gate.wake()
        elif len(waits) == 2:
            gate.defer(70)
        else:
            clock.now += timeout

    monkeypatch.setattr(gate._condition, "wait", wait)
    gate.defer(60)
    assert capsys.readouterr().err == ""
    gate.wait()
    assert waits == [60, 60, 70]
    assert capsys.readouterr().err.splitlines() == [
        "Limite temporário da API: aguardando 60 segundos (pausa compartilhada).",
        "Limite temporário da API: aguardando 70 segundos (pausa compartilhada).",
    ]


def test_pool_shutdown_interrupts_long_rate_wait_after_workers(monkeypatch):
    """Evita travar o encerramento em rate sleeps e fechar sessão ainda em uso."""
    gate = RateLimitGate()
    gate.defer(86400)
    waiting = watch_waits(gate, monkeypatch)
    clients = []
    stopped = Event()
    closed = []

    def request_json(self, *args, **kwargs):
        """Rejeita qualquer transporte que ultrapasse a pausa cancelada."""
        pytest.fail("Nenhuma chamada de rede deveria iniciar.")

    monkeypatch.setattr(Requester, "requestJson", request_json)

    def factory():
        """Cria sessões reais isoladas com fechamento rastreado no coordenador."""
        client = create_client("fixture", gate=gate)
        original_close = client.close

        def close():
            """Exige fim da operação bloqueada antes de fechar a sessão."""
            assert stopped.is_set()
            closed.append(get_ident())
            original_close()

        client.close = close
        clients.append(client)
        return client

    with WorkerPool(2, factory) as pool:
        def operation(item):
            """Deixa uma operação pronta e outra bloqueada até o cancelamento."""
            client = pool.get_client()
            if item == 0:
                assert waiting.wait(5)
                return item
            try:
                client.requester.requestJson("GET", "/fixture")
            except CancelledError:
                stopped.set()
                raise

        results = pool.map_ordered(operation, range(1000))
        assert next(results) == 0
        results.close()
    assert len(clients) == 2
    assert closed == [get_ident(), get_ident()]
