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
