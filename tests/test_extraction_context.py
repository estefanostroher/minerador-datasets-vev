"""Exerce consumo offline com objetos Git locais e evidências reais do analisador."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from robo_minerador.extraction import _extract_candidate
from robo_minerador.extraction_analyzer import Analyzer, preflight
from robo_minerador.extraction_context import load_instance_context
from robo_minerador.extraction_format import compact_records
from robo_minerador.git_transport import GitTransport
from robo_minerador.snapshot_store import SnapshotStore
from test_git_transport import LocalTransport
from test_extraction_format import stream


def make_local_snapshot(tmp_path, contents):
    """Cria árvore plana confiável via plumbing Git, sem checkout ou rede."""
    runner = GitTransport()
    origin = tmp_path / "origin.git"
    runner.run(["init", "--bare", "--template=", str(origin)])
    entries = []
    for path, data in contents.items():
        raw = data.encode() if isinstance(data, str) else data
        oid = runner.run(["hash-object", "-w", "--stdin"], repository=origin, input_data=raw).strip()
        entries.append(b"100644 blob " + oid + b"\t" + path.encode() + b"\0")
    tree = runner.run(["mktree", "-z"], repository=origin, input_data=b"".join(entries)).strip()
    sha = runner.run(["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                      "commit-tree", tree.decode()], repository=origin, input_data=b"Fixture\n").decode().strip()
    runner.run(["update-ref", "refs/heads/main", sha], repository=origin)
    transport = LocalTransport(origin)
    calls = []

    def get_repo(identity):
        """Conta metadados locais para provar ausência de API no cache hit."""
        calls.append(identity)
        return SimpleNamespace(id=1, private=False, full_name="fixture/public")

    return SimpleNamespace(store=SnapshotStore(tmp_path / "cache", transport), transport=transport,
                           client=SimpleNamespace(get_repo=get_repo), calls=calls,
                           candidate={"repository_id": 1, "repository_url": "https://github.com/fixture/public",
                                      "commit_sha": sha})


@pytest.fixture
def snapshot_case(tmp_path):
    """Produz casos aprovado, ambíguo e rejeitado com ambiente/configuração/binário."""
    contents = {
        "math.ts": "/** Incrementa a entrada. */\nexport function increment(value: number) { return value + 1; }\n"
                   "/** Valor constante. */\nexport function constant() { return 1; }\n"
                   "export function undocumented(value: number) { return value + 2; }\n"
                   "/** Escolhe entrada. */\nexport function ambiguous(value: unknown) { return value; }\n",
        "math.test.ts": "import { test } from 'vitest';\nimport { increment, constant, undocumented, ambiguous } from './math';\n"
                        "test('incrementa', () => { increment(Number(process.env.INPUT ?? '1')); });\n"
                        "test('constante', () => { constant(); });\n"
                        "test('sem doc', () => { undocumented(1); });\n"
                        "test('ambigua', () => { ambiguous({}); });\n",
        "vitest.config.ts": "export default { test: { environment: 'node' } };\n",
        "input.bin": b"\0\xff\x80",
    }
    return make_local_snapshot(tmp_path, contents)


def published_case(state):
    """Publica o grupo real em V3 sem iniciar execução de candidatos."""
    group = _extract_candidate(state.client, state.candidate, Analyzer(preflight()), store=state.store)
    rows = list(compact_records(stream(*group), schema_version=3))
    repository = next(row for row in rows if row["record_type"] == "repository")
    reasons = {row["reason_id"]: row["text"] for row in rows if row["record_type"] == "criterion_reason"}
    units = [row for row in rows if row["record_type"] == "instance"]
    return repository, reasons, units


def test_offline_consumer_selects_only_approved(snapshot_case):
    """Recupera evidências e binário sem fetch; recusa pendentes/rejeitados."""
    state = snapshot_case
    repository, reasons, units = published_case(state)
    assert {unit["decision"] for unit in units} == {"approved", "rejected", "not_evaluated"}
    offline = SnapshotStore(state.store.root)
    for unit in units:
        if unit["decision"] != "approved":
            with pytest.raises(ValueError, match="Somente"):
                load_instance_context(unit, repository, reasons, offline)
            continue
        loaded = load_instance_context(unit, repository, reasons, offline)
        assert loaded["instance"]["function"]["name"] == "increment"
        assert loaded["instance"]["validation"]["executed_test_cases"] is None
        assert offline.read_blob(loaded["snapshot_artifact"], "input.bin") == b"\0\xff\x80"
        assert all(item["status"] == "not_evaluated" for item in loaded["execution_requirements"])
    assert state.calls == [1] and state.transport.fetches == 1


@pytest.mark.parametrize("tamper", ["sha", "manifest", "missing", "path", "snippet", "criteria", "promotion", "validation", "transformation", "legacy"])
def test_loader_refuses_forged_or_unavailable_evidence(snapshot_case, tamper):
    """Alterações no índice não substituem objetos, identidade ou replay dos critérios."""
    state = snapshot_case
    repository, reasons, units = published_case(state)
    unit = deepcopy(next(item for item in units if item["decision"] == "approved"))
    if tamper == "sha":
        repository["context"]["snapshot"]["commit_sha"] = "b" * 40
    elif tamper == "manifest":
        artifact = state.store.open_verified(repository["context"]["snapshot"])
        (artifact.storage_path / "manifest.json").write_bytes(b"{}")
    elif tamper == "missing":
        state.store = SnapshotStore(state.store.root / "missing")
    elif tamper == "path":
        unit.pop("context_ref", None)
        unit["context_paths"] = ["../outside.ts"]
    elif tamper == "snippet":
        unit["function"]["reference_code"] += " // editado"
    elif tamper == "criteria":
        next(iter(unit["criteria"].values()))["reason_ref"] = "inventado"
    elif tamper == "promotion":
        unit = deepcopy(next(item for item in units if item["decision"] == "not_evaluated"))
        unit["decision"] = "approved"
        for criterion in unit["criteria"].values():
            criterion["status"] = "approved"
    elif tamper == "validation":
        unit["validation"] = {"status": "approved"}
    elif tamper == "transformation":
        unit["transformation"] = "changed"
    else:
        unit["schema_version"] = 2
    with pytest.raises(ValueError):
        load_instance_context(unit, repository, reasons, state.store)
    assert state.transport.fetches == 1
