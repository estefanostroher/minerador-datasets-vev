"""Coordenação e publicação da construção offline do dataset."""

from importlib import metadata

from . import config
from .dataset import build_dataset
from .dataset_format import validate_dataset_records
from .dataset_input import load_dataset_inputs
from .dataset_metrics import DatasetToolError, measure_instances
from .jsonl import write_jsonl


def run_dataset(args) -> int:
    """Verifica e prepara todas as linhas antes de abrir a saída."""
    writing = False
    try:
        try:
            output = config.DATASET_OUTPUT_PATH
            inputs = (config.DATASET_DISCOVERY_PATH, config.DATASET_TRIAGE_PATH,
                      config.DATASET_EXTRACTION_PATH, config.DATASET_VALIDATION_PATH,
                      config.DATASET_DEDUPLICATION_PATH)
            if output.is_dir() or any(parent.exists() and not parent.is_dir() for parent in output.parents):
                raise ValueError("Caminho de saída inválido.")
            for path in inputs:
                if output.resolve() == path.resolve() or (output.exists() and path.exists() and output.samefile(path)):
                    raise ValueError("Entrada e saída coincidem.")
            if output.exists() and not args.overwrite:
                print("A saída já existe. Use --overwrite para autorizar a substituição.")
                return 2
            prepared = load_dataset_inputs(*inputs)
            measurements = measure_instances(prepared["candidates"])
            try:
                version = metadata.version("robo-minerador")
            except Exception:
                raise DatasetToolError("Versão instalada indisponível.") from None
            records = build_dataset(prepared, measurements, builder_version=version)
            count = validate_dataset_records(records)
            if count != len(prepared["candidates"]):
                raise ValueError("Quantidade de representantes inconsistente.")
        except (ValueError, OSError):
            print("Entrada ou configuração inválida; a saída foi preservada.")
            return 2
        except DatasetToolError:
            print("Falha operacional na construção do dataset; a saída foi preservada.")
            return 1
        writing = True
        try:
            write_jsonl(output, records, ensure_ascii=True, compact=True, sort_keys=True)
        except (OSError, ValueError, RuntimeError):
            print("Falha ao escrever o dataset; o arquivo pode estar incompleto.")
            return 1
        print(f"Instâncias: {count}; saída: {output}")
        return 0
    except KeyboardInterrupt:
        print("Construção cancelada; " + ("o arquivo pode estar incompleto." if writing else "a saída foi preservada."))
        return 1
