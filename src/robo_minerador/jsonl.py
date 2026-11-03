"""Escrita de registros no formato JSONL em UTF-8."""

import json
from collections.abc import Iterable
from pathlib import Path


def write_jsonl(path: Path, records: Iterable[dict], *, ensure_ascii: bool = False, compact: bool = False) -> None:
    """Grava JSONL UTF-8; escapes ASCII opcionais preservam também surrogates isolados."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=ensure_ascii, allow_nan=False,
                                    separators=(",", ":") if compact else None) + "\n")
