"""Configuração e validação dos limites da descoberta."""

from datetime import date, datetime, time, timezone
from pathlib import Path

MIN_REPOSITORY_DATE = "2025-01-01"
MAX_REPOSITORIES = 1000
DEFAULT_WORKERS = 4
MAX_WORKERS = 16
OUTPUT_PATH = Path("results/01_descoberta.jsonl")
TRIAGE_INPUT_PATH = OUTPUT_PATH
TRIAGE_OUTPUT_PATH = Path("results/02_triagem.jsonl")
EXTRACTION_INPUT_PATH = TRIAGE_OUTPUT_PATH
EXTRACTION_OUTPUT_PATH = Path("results/03_extracao.jsonl")
VALIDATION_INPUT_PATH = EXTRACTION_OUTPUT_PATH
VALIDATION_OUTPUT_PATH = Path("results/04_validacao.jsonl")
VALIDATION_CPUS = 4
VALIDATION_MEMORY_BYTES = 8 * 1024**3
VALIDATION_INSTALLATION_SECONDS = 1200
VALIDATION_PREPARATION_SECONDS = 600
VALIDATION_UNIT_SUITE_SECONDS = 1800
SNAPSHOT_CACHE_PATH = Path(".cache/robo-minerador/snapshots")
SNAPSHOT_MAX_ENTRIES = 100_000
SNAPSHOT_MAX_BLOB_BYTES = 512 * 1024 * 1024
SNAPSHOT_MAX_DISK_BYTES = 1024 * 1024 * 1024
SNAPSHOT_TIMEOUT_SECONDS = 300
SNAPSHOT_MAX_CONCURRENT = 2
EXTRACTION_MAX_TEXT_BYTES = 32 * 1024 * 1024


def validate_validation_limits(limits):
    """Confere todos os orçamentos efetivos sem conversão ou aceitação de bool."""
    keys = {"cpus", "memory_bytes", "installation_seconds", "preparation_seconds", "unit_suite_seconds"}
    if not isinstance(limits, dict) or set(limits) != keys or any(
            type(value) is not int or value <= 0 for value in limits.values()):
        raise ValueError("Os limites da validação devem ser inteiros positivos completos.")
    return dict(limits)


def validate_workers(workers: int) -> int:
    """Valida o número de workers sem aceitar booleanos ou conversões implícitas."""
    if type(workers) is not int or not 1 <= workers <= MAX_WORKERS:
        raise ValueError(f"O número de workers deve ser um inteiro entre 1 e {MAX_WORKERS}.")
    return workers


def validate_config(min_date: str, limit: int, now: datetime) -> datetime:
    """Valida data e limite e retorna a data mínima à meia-noite UTC.

    Recebe em ``now`` o instante de início da coleta com fuso horário.
    Levanta ``ValueError`` com mensagem em português para valores inválidos.
    """
    try:
        minimum_date = date.fromisoformat(min_date)
    except (TypeError, ValueError) as error:
        raise ValueError("A data mínima deve ser uma data ISO válida.") from error

    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("O limite de repositórios deve ser um inteiro positivo.")

    start = datetime.combine(minimum_date, time.min, tzinfo=timezone.utc)
    if start > now:
        raise ValueError("A data mínima não pode ser posterior ao início da coleta.")

    return start
