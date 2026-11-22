"""Coordenador das etapas existentes do minerador."""

import argparse
import hashlib
import os
import signal
import subprocess
import sys

from . import config, pipeline_status

STAGES = ('discover', 'triage', 'extract', 'validate', 'deduplicate', 'build-dataset')
OUTPUT_NAMES = ('OUTPUT_PATH', 'TRIAGE_OUTPUT_PATH', 'EXTRACTION_OUTPUT_PATH',
                'VALIDATION_OUTPUT_PATH', 'DEDUPLICATION_OUTPUT_PATH', 'DATASET_OUTPUT_PATH')


def _positive(value):
    try:
        number = int(value)
        if number > 0:
            return number
    except ValueError:
        pass
    raise argparse.ArgumentTypeError('O limite deve ser um inteiro positivo.')


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description='Executa as etapas selecionadas do minerador.', allow_abbrev=False)
    parser.add_argument('--all', nargs='?', const=config.MAX_REPOSITORIES, type=_positive, metavar='N')
    parser.add_argument('--discover', nargs='?', const=config.MAX_REPOSITORIES, type=_positive, metavar='N')
    for stage in STAGES[1:]:
        parser.add_argument('--' + stage, action='store_true')
    parser.add_argument('--overwrite', action='store_true')
    args = parser.parse_args(argv)
    selected = args.discover is not None or any(getattr(args, s.replace('-', '_')) for s in STAGES[1:])
    if args.all is not None and selected:
        parser.error('--all não pode ser combinado com seletores individuais')
    if args.all is None and not selected:
        parser.print_help(sys.stderr)
        parser.exit(2)
    return args


def _signature(path):
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _content_hash(path):
    """Calcula o hash dos bytes em blocos; distingue arquivo ausente de vazio."""
    try:
        with path.open('rb') as stream:
            digest = hashlib.sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
            return digest.digest()
    except FileNotFoundError:
        return None


def _cancel(process):
    try:
        process.send_signal(signal.CTRL_BREAK_EVENT if os.name == 'nt' else signal.SIGINT)
        print('Aguardando limpeza da etapa; interrompa novamente para forçar.', flush=True)
        process.wait()
        return
    except (OSError, subprocess.TimeoutExpired, KeyboardInterrupt):
        pass
    try:
        process.terminate()
        process.wait(timeout=5)
        return
    except (OSError, subprocess.TimeoutExpired, KeyboardInterrupt):
        pass
    try:
        process.kill()
    except OSError:
        pass
    while True:
        try:
            process.wait()
            break
        except KeyboardInterrupt:
            continue
        except OSError:
            break


def run_stage(argv):
    options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
    process = subprocess.Popen([sys.executable, '-m', 'robo_minerador', *argv], **options)
    try:
        return process.wait()
    except KeyboardInterrupt:
        _cancel(process)
        raise


def main(argv=None):
    args = parse_arguments(argv)
    stages = [(stage, getattr(config, output)) for stage, output in zip(STAGES, OUTPUT_NAMES)
              if args.all is not None or (args.discover is not None if stage == 'discover'
                                         else getattr(args, stage.replace('-', '_')))]
    result = 0
    try:
        if not args.overwrite:
            for stage, path in stages:
                if _signature(path) is not None:
                    print(f'Saída de {stage} já existe: {path}. Use --overwrite.')
                    print('Pipeline encerrado: 2')
                    return 2
        for stage, path in stages:
            before = _content_hash(path) if stage in STAGES[:4] else None
            command = [stage]
            if stage == 'discover':
                command += ['--limit', str(args.all if args.all is not None else args.discover)]
            if args.overwrite:
                command.append('--overwrite')
            print(f'Iniciando {stage}.', flush=True)
            code = run_stage(command)
            print(f'{stage}: {code}')
            if code == 0:
                continue
            if code == 2:
                result = 2
                break
            if code == 1 and stage in STAGES[:4]:
                after = _content_hash(path)
                if after is not None and after != before and pipeline_status.accept_partial(stage, path):
                    result = 1
                    print(f'{stage}: publicação parcial íntegra; continuando.')
                    continue
            result = 1
            print(f'{stage}: execução interrompida.')
            break
    except (OSError, KeyboardInterrupt):
        print('Pipeline interrompido.')
        result = 1
    print(f'Pipeline encerrado: {result}')
    return result


def _interrupt(signum, frame):
    raise KeyboardInterrupt


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, _interrupt)
    raise SystemExit(main())
