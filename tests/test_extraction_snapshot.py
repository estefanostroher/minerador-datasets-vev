"""Protege integridade de blobs, arquivos parciais e contexto omitido."""

import hashlib

import pytest
from github import GithubException

from robo_minerador import extraction_snapshot, triage_snapshot
from test_triage_checks import COMMIT, MemoryClient

CANDIDATE = {"repository_id": 7, "commit_sha": COMMIT}


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
