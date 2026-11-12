"""Interface de linha de comando para descoberta e triagem de repositórios."""

import argparse
import os
from datetime import datetime, timedelta, timezone

from . import config
from .concurrency import RateLimitGate, WorkerPool
from .deduplication import deduplicate_instances
from .deduplication_format import validate_deduplication_records
from .deduplication_input import load_deduplication_inputs
from .discovery import (
    EXPECTED_API_ERRORS, _error_context, collect_repositories, failed_run_records,
    resolve_repository_sha,
)
from .github_api import create_client
from .extraction import extract_repositories
from .extraction_analyzer import preflight
from .extraction_input import load_triage
from .git_transport import GitTransport
from .jsonl import write_jsonl
from .validation import validate_repositories
from .validation_docker import DockerExecutor
from .validation_input import load_extraction
from .validation_recipe import load_recipes
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
    parser = argparse.ArgumentParser(description="Descoberta, triagem, extração, validação e deduplicação de repositórios.")
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
    extract = commands.add_parser("extract", help="Extrair funções, documentação e testes no SHA aprovado.")
    extract.add_argument("--overwrite", action="store_true",
                         help="Substituir intencionalmente a saída existente.")
    validate = commands.add_parser("validate", help="Validar candidatas em Docker com uma passagem unitária.")
    validate.add_argument("--overwrite", action="store_true",
                          help="Substituir intencionalmente a saída existente.")
    deduplicate = commands.add_parser("deduplicate", help="Deduplicar instâncias aprovadas.")
    deduplicate.add_argument("--overwrite", action="store_true",
                             help="Substituir intencionalmente a saída existente.")
    for command in (discover, triage, extract):
        command.add_argument(
            "--workers", type=_workers_argument, default=config.DEFAULT_WORKERS,
            help=f"Threads de consulta (1 a {config.MAX_WORKERS}; padrão {config.DEFAULT_WORKERS}). "
                 "Use 1 para execução sequencial.",
        )
    return parser.parse_args(argv)


def _run_deduplication(args: argparse.Namespace) -> int:
    """Prepara e confere a publicação inteira antes de abrir o arquivo de saída."""
    writing = False
    try:
        try:
            output = config.DEDUPLICATION_OUTPUT_PATH
            inputs = (config.DEDUPLICATION_INPUT_PATH, config.DEDUPLICATION_EXTRACTION_PATH)
            if output.is_dir() or any(parent.exists() and not parent.is_dir() for parent in output.parents):
                raise ValueError("Caminho de publicação inválido.")
            for path in inputs:
                if output.resolve() == path.resolve() or (output.exists() and path.exists() and output.samefile(path)):
                    raise ValueError("Entrada e saída coincidem.")
            if output.exists() and not args.overwrite:
                print("A saída já existe. Use --overwrite para autorizar a substituição.")
                return 2
            candidates, source = load_deduplication_inputs(*inputs)
            records = deduplicate_instances(candidates, source)
            result = validate_deduplication_records(records)
        except (ValueError, OSError, RuntimeError):
            print("Entrada, configuração ou preflight da deduplicação inválidos; a saída foi preservada.")
            return 2
        writing = True
        try:
            write_jsonl(output, records, ensure_ascii=True, compact=True)
        except (ValueError, OSError, RuntimeError):
            print("Não foi possível concluir a deduplicação; o arquivo pode estar incompleto.")
            return 1
        print(f"Elegíveis: {result['eligible_count']}; representantes: {result['representative_count']}; "
              f"duplicatas: {result['duplicate_count']}; grupos duplicados: {result['duplicate_group_count']}; "
              f"saída: {output}")
        return 0
    except KeyboardInterrupt:
        print("Deduplicação cancelada; " + ("o arquivo pode estar incompleto." if writing else "a saída foi preservada."))
        return 1


def _run_validation(args: argparse.Namespace) -> int:
    """Confere pré-requisitos antes da escrita e fecha recursos próprios em todo encerramento."""
    executor = None
    records = None
    try:
        try:
            input_path = config.VALIDATION_INPUT_PATH
            output_path = config.VALIDATION_OUTPUT_PATH
            if input_path.resolve() == output_path.resolve() or output_path.is_dir():
                raise ValueError("Caminhos de entrada e saída incompatíveis.")
            if any(parent.exists() and not parent.is_dir() for parent in output_path.parents):
                raise ValueError("Diretório de publicação inválido.")
            config.validate_validation_limits({
                "cpus": config.VALIDATION_CPUS, "memory_bytes": config.VALIDATION_MEMORY_BYTES,
                "installation_seconds": config.VALIDATION_INSTALLATION_SECONDS,
                "preparation_seconds": config.VALIDATION_PREPARATION_SECONDS,
                "unit_suite_seconds": config.VALIDATION_UNIT_SUITE_SECONDS,
            })
            if output_path.exists() and not args.overwrite:
                print("A saída já existe. Use --overwrite para autorizar a substituição.")
                return 2
            groups, source = load_extraction(input_path)
            catalog = load_recipes()
            if groups:
                GitTransport().preflight()
                preflight()
                executor = DockerExecutor()
                executor.preflight()
        except (ValueError, OSError, RuntimeError):
            print("Entrada, configuração ou preflight inválidos: verifique o JSONL, as receitas, "
                  "os limites e Git/Node/Docker Linux. A saída foi preservada.")
            return 2

        print(f"Validação: {len(groups)} repositórios; {source['eligible_count']} candidatas.")
        records = validate_repositories(groups, source, catalog, executor=executor)
        result = None

        def tracked_records():
            """Acompanha progresso e fechamento sem acumular os registros da execução."""
            nonlocal result
            for record in records:
                if record.get("record_type") == "repository":
                    print(f"Repositório {record['repository_id']}: {record['decision']}.")
                if record.get("record_type") == "run_finished":
                    result = record
                yield record

        try:
            write_jsonl(output_path, tracked_records(), ensure_ascii=True, compact=True)
        except (OSError, ValueError, RuntimeError):
            if executor is not None:
                executor.cancel()
            print("Não foi possível concluir a saída da validação; o arquivo pode estar incompleto.")
            return 1
        if result is None:
            print("Validação sem fechamento; o arquivo não representa uma execução completa.")
            return 1
        counts = result["instance_decisions"]
        print(f"Repositórios: {result['repository_count']}; instâncias: {result['instance_count']}; "
              f"aprovadas: {counts['approved']}; rejeitadas: {counts['rejected']}; "
              f"pendentes: {counts['not_evaluated']}; erros operacionais: {result['operational_errors']}; "
              f"estado: {result['status']}; saída: {output_path}")
        return 0 if result["status"] == "completed" else 1
    except KeyboardInterrupt:
        if executor is not None:
            executor.cancel()
        print("Validação cancelada; confira o fechamento do arquivo antes de utilizá-lo.")
        return 1
    finally:
        try:
            if records is not None:
                records.close()
        finally:
            # Também recolhe o executor se preflight/escrita falhou antes de iniciar o gerador.
            if executor is not None:
                executor.close()


def _run_extraction(args: argparse.Namespace) -> int:
    """Valida entrada e auxiliar antes da escrita e mantém o gerador cancelável."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    try:
        if not token:
            raise ValueError("Defina GITHUB_TOKEN no ambiente antes da extração.")
        config.validate_workers(args.workers)
        candidates, source = load_triage(config.EXTRACTION_INPUT_PATH)
        settings = preflight()
        GitTransport().preflight()
        if config.EXTRACTION_OUTPUT_PATH.exists() and not args.overwrite:
            print("A saída já existe. Use --overwrite para autorizar a substituição.")
            return 2
    except (ValueError, OSError) as error:
        if isinstance(error, ValueError):
            print(f"Entrada ou configuração inválida: {error}")
        else:
            print("Não foi possível ler a entrada ou verificar a configuração da extração.")
        return 2
    records = extract_repositories(token, candidates, source, settings, workers=args.workers)
    result = None

    def tracked_records():
        """Registra o resumo sem reter todo o JSONL na memória do coordenador."""
        nonlocal result
        for record in records:
            if record.get("event") == "run_finished":
                result = record
            yield record

    try:
        write_jsonl(config.EXTRACTION_OUTPUT_PATH, tracked_records(), ensure_ascii=True, compact=True)
    except OSError:
        print("Não foi possível gravar o arquivo de saída da extração.")
        return 1
    finally:
        records.close()
    print(f"Repositórios: {result['repository_count']}; instâncias: {result['instance_count']}; "
          f"estado: {result['status']}; saída: {config.EXTRACTION_OUTPUT_PATH}")
    return 0 if result["status"] == "completed" else 1


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
    if args.command == "deduplicate":
        return _run_deduplication(args)
    if args.command == "validate":
        return _run_validation(args)
    try:
        config.validate_workers(args.workers)
    except ValueError as error:
        print(str(error))
        return 2
    if args.command == "triage":
        return _run_triage(args)
    if args.command == "extract":
        return _run_extraction(args)
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
