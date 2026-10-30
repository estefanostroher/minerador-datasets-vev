"""Configuração e validação dos limites da descoberta."""

from datetime import date, datetime, time, timezone
from pathlib import Path

MIN_REPOSITORY_DATE = "2025-01-01"
MAX_REPOSITORIES = 1000
OUTPUT_PATH = Path("results/01_descoberta.jsonl")


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
