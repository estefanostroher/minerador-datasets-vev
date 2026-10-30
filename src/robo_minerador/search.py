"""Formatação de consultas e divisão de intervalos de criação."""

from datetime import datetime, timedelta, timezone


def build_query(start: datetime, end: datetime) -> str:
    """Formata em UTC um intervalo com fuso, precisão de segundos e fim exclusivo."""
    last_second = end.astimezone(timezone.utc) - timedelta(seconds=1)
    lower = start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    upper = last_second.strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"language:TypeScript is:public created:{lower}..{upper}"


def split_interval(
    start: datetime, end: datetime
) -> tuple[tuple[datetime, datetime], tuple[datetime, datetime]]:
    """Divide um intervalo UTC em segundos inteiros, retornando a metade recente primeiro."""
    seconds = int((end - start).total_seconds())
    if seconds < 2:
        raise ValueError("O intervalo não pode ser dividido em segundos inteiros.")
    middle = start + timedelta(seconds=seconds // 2)
    return (middle, end), (start, middle)
