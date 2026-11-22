"""Conferência integral das publicações parciais das etapas."""

import json

from . import extraction_input, triage, validation_format, validation_input
from .extraction_input import strict_float, strict_object


def _invalid_constant(value):
    raise ValueError(f"Constante JSON inválida: {value}")


def accept_partial(stage, output):
    """Aceita apenas publicação íntegra, fechada e marcada como parcial."""
    readers = {'discover': triage.load_discovery, 'triage': extraction_input.load_triage,
               'extract': validation_input.load_extraction}
    try:
        if stage in readers:
            _, result = readers[stage](output)
        elif stage == 'validate':
            lines = output.read_text(encoding='utf-8').splitlines()
            records = [json.loads(line, parse_constant=_invalid_constant,
                                  object_pairs_hook=strict_object, parse_float=strict_float)
                       for line in lines]
            result = validation_format.validate_output_records(records)
        else:
            return False
        return result['status'] == 'partial'
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, AttributeError, RuntimeError):
        return False
