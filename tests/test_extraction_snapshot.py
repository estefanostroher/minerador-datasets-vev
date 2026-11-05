"""Protege integridade de blobs, arquivos parciais e contexto omitido."""

import hashlib
from types import SimpleNamespace

import pytest
from github import GithubException

from robo_minerador import extraction_snapshot, triage_snapshot
from test_triage_checks import COMMIT, MemoryClient
from test_git_transport import LocalTransport
from robo_minerador.git_transport import GitTransport
from robo_minerador.snapshot_store import SnapshotStore

CANDIDATE = {"repository_id": 7, "commit_sha": COMMIT}


@pytest.fixture
def snapshot_project(tmp_path):
    """Cria snapshots Git reais de árvores arbitrárias sem rede ou checkout."""
    counter = 0

    def create(texts):
        """Materializa apenas objetos Git de uma fixture controlada e projeta seus bytes."""
        nonlocal counter
        counter += 1
        origin = tmp_path / f'origin-{counter}.git'
        runner = GitTransport()
        runner.run(['init', '--bare', '--template=', str(origin)])
        tree = {}
        for name, content in texts.items():
            current = tree
            parts = name.split('/')
            for part in parts[:-1]:
                current = current.setdefault(part, {})
            current[parts[-1]] = content.encode('utf-8') if isinstance(content, str) else content

        def write_tree(children):
            """Constrói árvores recursivas com nomes e bytes exatos, sem arquivos candidatos."""
            records = []
            for name, content in sorted(children.items()):
                if isinstance(content, dict):
                    oid, mode, kind = write_tree(content), b'040000', b'tree'
                else:
                    oid = runner.run(['hash-object', '-w', '--stdin'], repository=origin,
                                     input_data=content).strip()
                    mode, kind = b'100644', b'blob'
                records.append(mode + b' ' + kind + b' ' + oid + b'\t' + name.encode() + b'\0')
            return runner.run(['mktree', '-z'], repository=origin,
                              input_data=b''.join(records)).strip()

        oid = write_tree(tree)
        sha = runner.run(['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                          'commit-tree', oid.decode()], repository=origin,
                         input_data=b'Fixture local\n').decode().strip()
        runner.run(['update-ref', 'refs/heads/main', sha], repository=origin)
        store = SnapshotStore(tmp_path / f'cache-{counter}', LocalTransport(origin))

        def get_repo(repository_id):
            """Fornece identidade pública controlada sem consultar serviço externo."""
            return SimpleNamespace(id=repository_id, private=False, full_name='fixture/public')

        artifact = store.acquire({'repository_id': 42, 'commit_sha': sha},
                                 SimpleNamespace(get_repo=get_repo))
        return store, artifact, extraction_snapshot.project_snapshot(store, artifact)

    return create


def test_projection_preserves_bytes_inventory_and_batch(snapshot_project, monkeypatch):
    """Preserva BOM/CRLF, inventaria binários e usa um lote sem fsck por arquivo."""
    texts = {'src/a.ts': '\ufeff/** Incrementa. */\r\nexport function a(x:number){return x+1;}\r\n',
             'asset.bin': b'\0\xff', 'style.css': 'x' * 1000, 'package.json': '{}'}
    store, artifact, projected = snapshot_project(texts)
    assert projected.analysis_complete
    assert {entry['path'] for entry in projected.inventory} == set(texts)
    assert projected.files[1]['content'] == texts['src/a.ts']
    assert projected.files[1]['sha256'] == hashlib.sha256(texts['src/a.ts'].encode()).hexdigest()
    calls = []
    original = store.transport.read_blobs

    def read_blobs(*args, **kwargs):
        """Conta subprocessos de lote sem alterar a conferência de OIDs."""
        calls.append(len(args[1]))
        return original(*args, **kwargs)

    monkeypatch.setattr(store.transport, 'read_blobs', read_blobs)
    extraction_snapshot.project_snapshot(store, artifact)
    # O primeiro lote pertence à única verificação de abertura do store;
    # somente o último projeta texto, sem uma verificação por arquivo.
    assert calls == [len(texts), 2]
    assert projected.issues == []


def test_projection_budget_preserves_snapshot_and_records_scope(snapshot_project, monkeypatch):
    """Limite de texto omite análise e mantém inventário e integridade completos."""
    store, artifact, _ = snapshot_project({'a.ts': 'export const a = 1;', 'doc.md': 'descrição' * 20})
    monkeypatch.setattr(extraction_snapshot.config, 'SNAPSHOT_MAX_TEXT_BYTES', 20, raising=False)
    projection = extraction_snapshot.project_snapshot(store, artifact)
    assert not projection.analysis_complete
    assert [item['path'] for item in projection.files] == ['a.ts']
    assert projection.issues[0]['code'] == 'ANALYSIS_TEXT_LIMIT'
    assert len(projection.inventory) == 2
    assert store.open_verified(projection.snapshot) == artifact


def test_projection_refuses_corrupt_snapshot(snapshot_project):
    """Marcador removido não produz projeção ou atestado parcial."""
    store, artifact, _ = snapshot_project({'a.ts': 'export const a=1;'})
    (artifact.storage_path / 'complete.json').unlink()
    with pytest.raises(triage_snapshot.SnapshotUnavailable):
        extraction_snapshot.project_snapshot(store, artifact)


def test_projection_pins_manifest_after_reader_verification(snapshot_project, monkeypatch):
    """Recusa troca do JSON entre abertura verificada e enumeração da projeção."""
    store, artifact, _ = snapshot_project({'a.ts': 'export const a=1;'})
    original = store.open_reader

    def open_reader(*args, **kwargs):
        """Simula adulteração depois de fixar os OIDs na sessão do reader."""
        reader = original(*args, **kwargs)
        (artifact.storage_path / 'manifest.json').write_bytes(b'{}')
        return reader

    monkeypatch.setattr(store, 'open_reader', open_reader)
    with pytest.raises(triage_snapshot.SnapshotUnavailable, match='Manifesto'):
        extraction_snapshot.project_snapshot(store, artifact)


def test_snapshot_original_bom_and_unknown_context():
    """Preserva BOM/CRLF e hashes originais e não alega contexto binário completo."""
    texts = {"src/a.ts": "\ufeffexport function a(x:number) {\r\nreturn x*2;\r\n}\r\n",
             "tests/fixture.yaml": "key: value\n", "package-lock.json": '{"lockfileVersion":3}',
             "assets/fixture.bin": "binary"}
    client = MemoryClient(texts)
    files, issues = extraction_snapshot.collect_context(client, CANDIDATE)
    assert {item["path"] for item in files} == set(texts) - {"assets/fixture.bin"}
    for item in files:
        assert item["content"] == texts[item["path"]]
        assert item["sha256"] == hashlib.sha256(texts[item["path"]].encode()).hexdigest()
    assert issues == [{"code": "UNINSPECTED_CONTEXT", "path": "assets/fixture.bin",
                       "message": "Arquivo de contexto desconhecido ou binário não inspecionado."}]
    assert all(COMMIT in url for _, url, _ in client.requests if "/trees/" in url)


@pytest.mark.parametrize("failure", ["budget", "corrupt", "api", "utf8", "link"])
def test_partial_context_never_becomes_scientific_absence(monkeypatch, failure):
    """Conserva fontes anteriores quando arquivo seguinte falha e explicita a limitação."""
    client = MemoryClient({"a.ts": "export function a(x:number) { return x*2; }", "z.ts": "export const z = 1;"})
    if failure == "budget":
        monkeypatch.setattr(triage_snapshot, "MAX_CONTENT_READS", 1)
    elif failure == "corrupt":
        client.objects["z.ts"]["content"] = client.objects["a.ts"]["content"]
    elif failure == "utf8":
        import base64
        content = b"\xff"
        sha = hashlib.sha1(b"blob 1\0" + content).hexdigest()
        client.objects["z.ts"].update(sha=sha, content=base64.b64encode(content).decode())
        client.tree["tree"][1].update(sha=sha, size=1)
    elif failure == "link":
        client.tree["tree"].append({"path": "linked.ts", "mode": "120000", "type": "blob"})
    elif failure == "api":
        original = client.requestJsonAndCheck

        def request(method, url, parameters=None):
            """Falha somente no segundo blob sem expor seu corpo ao resultado."""
            if url.endswith(client.objects["z.ts"]["sha"]):
                raise GithubException(503, {"message": "secret"})
            return original(method, url, parameters)

        monkeypatch.setattr(client, "requestJsonAndCheck", request)
    files, issues = extraction_snapshot.collect_context(client, CANDIDATE)
    assert [item["path"] for item in files] == ([] if failure == "link" else ["a.ts"])
    assert issues
    assert "secret" not in str(issues)
