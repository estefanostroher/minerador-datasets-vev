"""Smoke opt-in da imagem construída por `docker compose build`."""

import os
from pathlib import Path
import subprocess
import uuid

import pytest
from dataset_fixtures import failed_dataset_chain  # noqa: F401


pytestmark = pytest.mark.skipif(
    os.environ.get("ROBO_MINERADOR_DOCKER_TESTS") != "1",
    reason="Smoke Docker requer ROBO_MINERADOR_DOCKER_TESTS=1 e imagem Compose construída.",
)
ROOT = Path(__file__).resolve().parents[1]


def docker(*args, **kwargs):
    return subprocess.run(["docker", *args], cwd=ROOT, check=True, capture_output=True,
                          text=True, timeout=600, **kwargs).stdout


@pytest.fixture
def image():
    return docker("compose", "config", "--images").strip()


def test_image_help_preflight_and_persistence(image, tmp_path):
    results = tmp_path / "results"
    cache = tmp_path / "cache"
    results.mkdir()
    cache.mkdir()
    name = "robo-smoke-" + uuid.uuid4().hex
    args = ["run", "--name", name, "--init", "--mount", f"type=bind,src={results},dst=/app/results",
            "--mount", f"type=bind,src={cache},dst=/app/.cache/robo-minerador/snapshots",
            "--mount", "type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock"]
    try:
        assert "--discover" in docker(*args, image, "--help")
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60)
    script = '''
import subprocess, sys
from pathlib import Path
from robo_minerador.extraction_analyzer import preflight, HELPER_DIRECTORY
from robo_minerador.validation_docker import DockerExecutor
assert sys.version_info >= (3, 11)
for tool in ("git", "node", "npm", "docker"):
    print(subprocess.check_output([tool, "--version"], text=True).strip())
assert int(subprocess.check_output(["node", "-p", "process.versions.node.split('.')[0]"], text=True)) >= 22
assert str(HELPER_DIRECTORY) == "/app/src/robo_minerador/typescript"
assert preflight()["analyzer"]["version"] == "6.0.3"
executor = DockerExecutor()
try:
    assert executor.preflight()["OSType"] == "linux"
finally:
    executor.close()
Path('/app/results/smoke.txt').write_bytes(b'fixture persistence')
'''
    name = "robo-smoke-" + uuid.uuid4().hex
    args[2] = name
    try:
        docker(*args, "-i", "--entrypoint", "python", image, "-", input=script)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60)
    assert (results / "smoke.txt").read_bytes() == b"fixture persistence"


def test_instrument_publication_between_containers(image):
    script = r'''
import hashlib, io, json, os, tarfile, tempfile
from pathlib import Path
from robo_minerador.validation_docker import DockerExecutor, DockerInfrastructureError, LABEL
from robo_minerador.validation_recipe import load_recipes, _hash
from robo_minerador.validation_workspace import ValidationWorkspace

recipe = next(iter(load_recipes()['recipes'].values()))
executor = DockerExecutor()
executor.run_id = os.environ['ROBO_SMOKE_RUN_ID']
real_cli = executor._cli
try:
    executor.preflight()
    try:
        real_cli(['image', 'inspect', recipe['image']])
    except DockerInfrastructureError:
        real_cli(['pull', '--platform', recipe['platform'], recipe['image']], timeout=600)
    workspace_volume = executor._volume()
    workspace = ValidationWorkspace(workspace_volume, executor.run_id, recipe['repository_id'],
        recipe['commit_sha'], json.dumps(recipe), _hash(recipe), [], {})
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        files = {'settings.json': b'{"fixture":true}', 'validation-command.mjs': b'export const fixture = true;\n', 'bytes.bin': b'\x00\xfffixture'}
        for name, content in files.items():
            (root / name).write_bytes(content)
        volume = executor.publish_instrument(workspace, root)
        expected = {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}
        script = "const fs=require('fs'),c=require('crypto'); const out={}; for(const n of fs.readdirSync('/instrument'))out[n]=c.createHash('sha256').update(fs.readFileSync('/instrument/'+n)).digest('hex'); if(process.env.GITHUB_TOKEN||fs.existsSync('/var/run/docker.sock'))throw Error('secret or socket'); console.log(JSON.stringify(out));"
        name, cid = executor._create(recipe, ['node', '-e', script], instrument_volume=volume)
        event = executor._execute(name, cid, executor.clock() + 120, {'kind': 'fixture'})
        assert executor._success(event), event
        assert json.loads(executor.read_log(event)) == expected
        inspect = event['inspect']
        assert any(m['Destination'] == '/instrument' and not m['RW'] for m in inspect['Mounts'])
        assert not any(m['Destination'] == '/var/run/docker.sock' for m in inspect['Mounts'])
        assert not any('GITHUB_TOKEN=' in v for v in inspect['Config']['Env'])
        def corrupt(args, **kwargs):
            result = real_cli(args, **kwargs)
            if args[:2] == ['cp', '-'] and args[-1].endswith(':/tmp'):
                stream = io.BytesIO()
                with tarfile.open(fileobj=stream, mode='w') as tar:
                    data = b'corrupt'; item = tarfile.TarInfo('settings.json'); item.size = len(data)
                    tar.addfile(item, io.BytesIO(data))
                real_cli(['cp', '-', args[-1].removesuffix(':/tmp') + ':/instrument'], data=stream.getvalue())
            return result
        executor._cli = corrupt
        try:
            executor.publish_instrument(workspace, root)
            raise AssertionError('corruption accepted')
        except DockerInfrastructureError as error:
            assert 'instrument bytes: settings.json' in str(error)
finally:
    executor._cli = real_cli
    executor.close()
assert not executor.cleanup_errors, executor.cleanup_errors
assert not real_cli(['ps', '-aq', '--filter', 'label='+LABEL+'='+executor.run_id]).strip()
assert not real_cli(['volume', 'ls', '-q', '--filter', 'label='+LABEL+'='+executor.run_id]).strip()
'''
    name = "robo-smoke-" + uuid.uuid4().hex
    run_id = uuid.uuid4().hex
    try:
        docker("run", "-i", "--name", name, "--init", "-e", "GITHUB_TOKEN=fixture-token-only",
               "-e", "ROBO_SMOKE_RUN_ID=" + run_id,
               "--mount", "type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock",
               "--entrypoint", "python", image, "-", input=script)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60)
        label = "label=org.robo-minerador.validation.run=" + run_id
        for resource in docker("ps", "-aq", "--filter", label).splitlines():
            docker("rm", "-f", resource)
        for resource in docker("volume", "ls", "-q", "--filter", label).splitlines():
            docker("volume", "rm", resource)


def test_failed_validation_reaches_empty_dataset_in_image(image, failed_dataset_chain, tmp_path):
    """Exerce a imagem e subprocessos finais reais com validação fixture pendente."""
    paths, _ = failed_dataset_chain
    results = tmp_path / 'docker-results'
    results.mkdir()
    for source, target in (('discovery', '01_descoberta.jsonl'), ('triage', '02_triagem.jsonl'),
                           ('extraction', '03_extracao.jsonl'), ('validation', '04_validacao.jsonl')):
        (results / target).write_bytes(paths[source].read_bytes())
    script = '''
import json
from pathlib import Path
from robo_minerador import pipeline
path = Path('results/04_validacao.jsonl')
payload = path.read_bytes()
path.unlink()
real_run = pipeline.run_stage
def run(args):
    if args[0] == 'validate':
        path.write_bytes(payload)
        return 1
    return real_run(args)
pipeline.run_stage = run
assert pipeline.main(['--validate', '--deduplicate', '--build-dataset']) == 1
assert Path('results/06_dataset.jsonl').read_bytes() == b''
rows = [json.loads(line) for line in Path('results/05_deduplicacao.jsonl').read_bytes().splitlines()]
assert rows[0]['sources']['validation']['status'] == 'failed'
assert rows[-1]['status'] == 'completed' and rows[-1]['representative_count'] == 0
'''
    output = docker('run', '--rm', '-i', '--mount', f'type=bind,src={results},dst=/app/results',
                    '--entrypoint', 'python', image, '-', input=script)
    assert 'build-dataset: 0' in output
    assert (results / '06_dataset.jsonl').read_bytes() == b''
