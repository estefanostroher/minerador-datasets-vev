"""Interface de linha de comando para a descoberta de repositórios."""

import argparse
import os
from datetime import datetime, timedelta, timezone

from . import config
from .discovery import EXPECTED_API_ERRORS, collect_repositories, failed_run_records
from .github_api import create_client
from .jsonl import write_jsonl


def _parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    """Interpreta o comando de descoberta e suas opções de execução."""
    parser = argparse.ArgumentParser(description="Descoberta de repositórios no GitHub.")
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Valida a execução, coordena a coleta e persiste o único JSONL da etapa."""
    args = _parse_arguments(argv)
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
    try:
        with create_client(token) as client:
            records = collect_repositories(client, start, end, args.limit)
    except EXPECTED_API_ERRORS as error:
        if records is None:
            records = failed_run_records(start, end, args.limit, error)
        else:
            failure = failed_run_records(start, end, args.limit, error)
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
