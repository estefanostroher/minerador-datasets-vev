"""Execução do minerador como módulo Python."""

import signal

from .cli import main

if __name__ == "__main__":
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    raise SystemExit(main())
