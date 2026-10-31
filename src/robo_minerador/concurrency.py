"""Workers limitados, clientes isolados e pausa de API compartilhada."""

from collections import deque
from concurrent.futures import CancelledError, ThreadPoolExecutor
from math import ceil
from threading import Condition, Lock, local
import sys
import time

from github import GithubException
from requests.exceptions import RequestException
from urllib3.exceptions import HTTPError


class RateLimitGate:
    """Compartilha um prazo monotônico; relógios injetáveis permitem simular a espera."""

    def __init__(self, *, clock=time.monotonic, wall_clock=time.time):
        """Inicializa a pausa com relógios monotônico e civil independentes."""
        self._clock = clock
        self._wall_clock = wall_clock
        self._condition = Condition()
        self._deadline = 0.0
        self._announced_deadline = None

    def defer(self, seconds):
        """Estende a pausa global sem reduzir um prazo já observado."""
        with self._condition:
            self._deadline = max(self._deadline, self._clock() + max(0, seconds))
            self._condition.notify_all()

    def observe(self, headers):
        """Pausa após esgotamento normal da cota, convertendo reset civil em duração."""
        headers = {key.lower(): value for key, value in headers.items()}
        if str(headers.get("x-ratelimit-remaining")) == "0":
            try:
                seconds = float(headers["x-ratelimit-reset"]) - self._wall_clock() + 1
            except (KeyError, ValueError, TypeError):
                return
            self.defer(seconds)

    def wake(self):
        """Acorda esperas para reavaliar relógio ou cancelamento, sem remover a pausa."""
        with self._condition:
            self._condition.notify_all()

    def wait(self, *, cancel=None, deadline=0.0):
        """Aguarda prazos e anuncia pausas globais, deduplicadas até extensão de um segundo."""
        with self._condition:
            while True:
                if cancel is not None and cancel.is_set():
                    raise CancelledError()
                now = self._clock()
                remaining = max(self._deadline, deadline) - now
                if remaining <= 0:
                    return
                if self._deadline > now and (
                    self._announced_deadline is None
                    or self._deadline - self._announced_deadline >= 1
                ):
                    self._announced_deadline = self._deadline
                    print(
                        f"Limite temporário da API: aguardando {ceil(self._deadline - now)} "
                        "segundos (pausa compartilhada).",
                        file=sys.stderr,
                    )
                self._condition.wait(remaining)

    def wait_for(self, seconds, *, cancel=None):
        """Aplica backoff local cancelável sem impor pausa aos demais clientes."""
        self.wait(cancel=cancel, deadline=self._clock() + max(0, seconds))


class WorkerPool:
    """Mapa ordenado com até workers futures e um cliente preguiçoso por thread.

    O coordenador deve consumir mapas sequencialmente e fechar o contexto. Cada
    operação recebe apenas o item e chama get_client dentro do seu próprio try.
    Um mapa abandonado deve ser fechado explicitamente ou pelo contexto do pool.
    """

    def __init__(self, workers, client_factory):
        """Cria o executor; clientes só são construídos quando uma operação os pede."""
        if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
            raise ValueError("workers deve ser um inteiro positivo.")
        self.window_size = workers
        self._factory = client_factory
        self._executor = ThreadPoolExecutor(max_workers=workers)
        self._local = local()
        self._lock = Lock()
        self._clients = []
        self._pending = set()
        self._closed = False
        self._close_errors = []

    @property
    def close_errors(self):
        """Retorna somente falhas esperadas de API no fechamento, após encerrar o pool."""
        return tuple(self._close_errors)

    def __enter__(self):
        """Disponibiliza o pool como contexto reutilizável entre páginas."""
        return self

    def __exit__(self, exc_type, exc, traceback):
        """Fecha recursos preservando a exceção original e anotando defeitos de limpeza."""
        try:
            self.close()
        except BaseException as error:
            if exc is None or isinstance(exc, GeneratorExit):
                raise
            exc.add_note(f"Falha interna ao fechar WorkerPool: {type(error).__name__}: {error}")
        return False

    def get_client(self):
        """Obtém o cliente exclusivo da thread, reutilizando-o até o encerramento."""
        if not hasattr(self._local, "client"):
            client = self._factory()
            with self._lock:
                self._clients.append(client)
                if self._closed:
                    self._cancel_client(client)
            self._local.client = client
        return self._local.client

    @staticmethod
    def _cancel_client(client):
        """Interrompe esperas coordenadas quando o cliente oferece esse protocolo."""
        cancel = getattr(client, "cancel_pending_requests", None)
        if cancel is not None:
            cancel()

    def map_ordered(self, operation, iterable):
        """Produz resultados na ordem de entrada, sem consumir antecipadamente tudo.

        Mantém no máximo window_size futures, inclusive resultados prontos ainda
        não publicados. Exceção ou close do gerador encerra o pool inteiro.
        """
        if self._closed:
            raise RuntimeError("WorkerPool já foi fechado.")
        pending = deque()
        completed = False
        try:
            items = iter(iterable)
            exhausted = False
            while True:
                while not exhausted and len(pending) < self.window_size:
                    try:
                        item = next(items)
                    except StopIteration:
                        exhausted = True
                        break
                    future = self._executor.submit(operation, item)
                    pending.append(future)
                    self._pending.add(future)
                if not pending:
                    completed = True
                    return
                future = pending.popleft()
                try:
                    result = future.result()
                except BaseException as error:
                    if future.done() and (future.cancelled() or future.exception() is error):
                        self._pending.remove(future)
                    raise
                self._pending.remove(future)
                yield result
        finally:
            if not completed:
                self.__exit__(*sys.exc_info())

    def close(self):
        """Cancela pendências, aguarda workers e fecha todos os clientes no coordenador."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            clients = list(self._clients)
        errors = []
        for future in self._pending:
            future.cancel()
        for client in clients:
            try:
                self._cancel_client(client)
            except Exception as error:
                errors.append(error)
        self._executor.shutdown(wait=True, cancel_futures=True)
        for future in self._pending:
            if future.cancelled():
                continue
            error = future.exception()
            if error is not None and not isinstance(
                error, (CancelledError, GithubException, RequestException, HTTPError),
            ):
                errors.append(error)
        self._pending.clear()
        for client in self._clients:
            try:
                client.close()
            except (GithubException, RequestException, HTTPError) as error:
                self._close_errors.append(error)
            except Exception as error:
                errors.append(error)
        if errors:
            raise errors[0]
