"""Interface de linha de comando para descoberta e triagem de repositórios."""

import argparse
import os
from datetime import datetime, timedelta, timezone

from . import config
from .concurrency import RateLimitGate, WorkerPool
from .discovery import (
    EXPECTED_API_ERRORS, _error_context, collect_repositories, failed_run_records,
    resolve_repository_sha,
)
from .github_api import create_client
from .jsonl import write_jsonl
from .triage import (
    _screen_candidate, diagnostic, failed_screen_records, load_discovery, screen_repositories,
)


def _workers_argument(value: str) -> int:
    """Converte a opção de workers e apresenta limites inválidos em português."""
    try:
        return config.validate_workers(int(value))
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            f"O número de workers deve ser um inteiro entre 1 e {config.MAX_WORKERS}."
        ) from error


def _parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    """Interpreta o comando da etapa e suas opções de execução."""
    parser = argparse.ArgumentParser(description="Descoberta e triagem de repositórios no GitHub.")
    commands = parser.add_subparsers(dest="command", required=True)
    discover = commands.add_parser("discover", help="Descobrir repositórios candidatos.")
    discover.add_argument(
        "--limit", type=int, default=config.MAX_REPOSITORIES,
        help="Quantidade máxima de repositórios únicos.",
    )
    discover.add_argument(
        "--overwrite", action="store_true",
        help="Substituir intencionalmente a saída existente.",
    )
    triage = commands.add_parser("triage", help="Avaliar snapshots dos candidatos descobertos.")
    triage.add_argument(
        "--overwrite", action="store_true",
        help="Substituir intencionalmente a saída existente.",
    )
    for command in (discover, triage):
        command.add_argument(
            "--workers", type=_workers_argument, default=config.DEFAULT_WORKERS,
            help=f"Threads de consulta (1 a {config.MAX_WORKERS}; padrão {config.DEFAULT_WORKERS}). "
                 "Use 1 para execução sequencial.",
        )
    return parser.parse_args(argv)


def _triage_records(token: str, candidates: list[dict], source: dict, workers: int = 1):
    """Mantém o cliente aberto durante a escrita e registra falhas de abertura/fechamento."""
    finished = None
    if workers > 1:
        gate = RateLimitGate()

        def client_factory():
            """Cria um cliente exclusivo da thread com pausa comum à execução."""
            return create_client(token, gate=gate)

        with WorkerPool(workers, client_factory) as pool:
            def screen_candidate(candidate):
                """Isola falha de abertura no candidato e dispensa cliente sem SHA."""
                if candidate["commit_sha"] is None:
                    return _screen_candidate(None, candidate)
                try:
                    client = pool.get_client()
                except EXPECTED_API_ERRORS as error:
                    return _screen_candidate(None, candidate, setup_error=error)
                return _screen_candidate(client, candidate)

            def screen_many(rows):
                """Mantém a janela limitada e devolve decisões na ordem de entrada."""
                return pool.map_ordered(screen_candidate, rows)

            for record in screen_repositories(None, candidates, source,
                                              screen_many=screen_many, workers=workers):
                if record.get("event") == "run_finished":
                    finished = record
                else:
                    yield record
        for error in pool.close_errors:
            yield diagnostic("repository_error", "Falha operacional ao fechar o cliente GitHub.",
                             repository_id=None, **_error_context(error))
            finished["error_count"] += 1
            if finished["status"] != "failed":
                finished["status"] = "partial"
        yield finished
        return
    try:
        with create_client(token) as client:
            for record in screen_repositories(client, candidates, source):
                if record.get("event") == "run_finished":
                    finished = record
                else:
                    yield record
    except EXPECTED_API_ERRORS as error:
        if finished is None:
            yield from failed_screen_records(error, candidates, source)
            return
        yield diagnostic("repository_error", "Falha operacional ao fechar o cliente GitHub.",
                         repository_id=None, **_error_context(error))
        finished["error_count"] += 1
        if finished["status"] != "failed":
            finished["status"] = "partial"
    yield finished


def _run_triage(args: argparse.Namespace) -> int:
    """Valida token e entrada antes de substituir a saída e persiste o gerador da triagem."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    try:
        if not token:
            raise ValueError("Token ausente.")
        candidates, source = load_discovery(config.TRIAGE_INPUT_PATH)
        if config.TRIAGE_OUTPUT_PATH.exists() and not args.overwrite:
            print("A saída já existe. Use --overwrite para autorizar a substituição.")
            return 2
    except (ValueError, OSError):
        print("Entrada ou configuração inválida: verifique GITHUB_TOKEN e o JSONL de descoberta.")
        return 2
    records = _triage_records(token, candidates, source, workers=args.workers)
    result = None

    def tracked_records():
        """Preserva o diagnóstico final sem acumular resultados em memória."""
        nonlocal result
        for record in records:
            if record.get("event") == "run_finished":
                result = record
            yield record

    try:
        write_jsonl(config.TRIAGE_OUTPUT_PATH, tracked_records())
    except OSError:
        print("Não foi possível gravar o arquivo de saída da triagem.")
        return 1
    finally:
        records.close()
    print(f"Repositórios: {result['repository_count']}; estado: {result['status']}; "
          f"saída: {config.TRIAGE_OUTPUT_PATH}")
    return 0 if result["status"] == "completed" else 1


def main(argv: list[str] | None = None) -> int:
    """Valida a execução, coordena a coleta e persiste o único JSONL da etapa."""
    args = _parse_arguments(argv)
    try:
        config.validate_workers(args.workers)
    except ValueError as error:
        print(str(error))
        return 2
    if args.command == "triage":
        return _run_triage(args)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    try:
        start = config.validate_config(config.MIN_REPOSITORY_DATE, args.limit, now)
        if not token:
            raise ValueError("Defina GITHUB_TOKEN no ambiente antes da descoberta.")
    except ValueError:
        print("Configuração inválida: verifique GITHUB_TOKEN, a data mínima e o limite positivo.")
        return 2

    try:
        if config.OUTPUT_PATH.exists() and not args.overwrite:
            print("A saída já existe. Use --overwrite para autorizar a substituição.")
            return 2
    except OSError:
        print("Não foi possível verificar o arquivo de saída.")
        return 1

    end = now + timedelta(seconds=1)
    records = None
    pool = None
    errors = []
    try:
        if args.workers == 1:
            with create_client(token) as client:
                records = collect_repositories(client, start, end, args.limit)
        else:
            gate = RateLimitGate()

            def client_factory():
                """Abre clientes de worker independentes da busca com a mesma pausa."""
                return create_client(token, gate=gate)

            with create_client(token, gate=gate) as client:
                with WorkerPool(args.workers, client_factory) as pool:
                    def resolve_candidate(record):
                        """Associa falha operacional de abertura ou resolução ao SHA deste ID."""
                        try:
                            return resolve_repository_sha(pool.get_client(), record)
                        except EXPECTED_API_ERRORS as error:
                            return None, error

                    def resolve_many(rows):
                        """Resolve apenas candidatos selecionados, preservando ordem da página."""
                        return pool.map_ordered(resolve_candidate, rows)

                    records = collect_repositories(client, start, end, args.limit,
                                                   resolve_many=resolve_many, workers=args.workers)
    except EXPECTED_API_ERRORS as error:
        errors.append(error)
    if pool is not None:
        errors.extend(pool.close_errors)
    for error in errors:
        if records is None:
            records = failed_run_records(start, end, args.limit, error, workers=args.workers)
        else:
            failure = failed_run_records(start, end, args.limit, error, workers=args.workers)
            records.insert(-1, failure[1])
            records[-1]["status"] = "partial" if records[-1]["repository_count"] else "failed"
            records[-1]["stop_reason"] = "api_error"

    try:
        write_jsonl(config.OUTPUT_PATH, records)
    except OSError:
        print("Não foi possível gravar o arquivo de saída da descoberta.")
        return 1
    result = records[-1]
    print(
        f"Repositórios: {result['repository_count']}; estado: {result['status']}; "
        f"saída: {config.OUTPUT_PATH}"
    )
    return 0 if result["status"] == "completed" else 1
