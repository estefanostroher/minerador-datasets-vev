"""Escrita de registros no formato JSONL em UTF-8."""

import json
from collections.abc import Iterable
from pathlib import Path


def write_jsonl(path: Path, records: Iterable[dict]) -> None:
    """Grava registros em JSONL UTF-8, criando as pastas necessárias."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
