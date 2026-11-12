"""Verifica agrupamento exato e integridade portátil das origens."""

from copy import deepcopy
import json

import pytest

from robo_minerador.deduplication import deduplicate_instances
from robo_minerador.deduplication_format import load_deduplication, validate_deduplication_records
from test_deduplication_representation import sample


def candidate(number, content="fixture"):
    """Cria uma origem distinta com evidências controladas."""
    unit, files = sample()
    unit.update(instance_id=f"{number:064x}", repository_id=number,
                repository_url=f"https://github.com/owner/repo{number}", commit_sha=f"{number:040x}")
    files["fixture.txt"] = content
    return {"unit": unit, "files": files}


def source(count):
    """Produz proveniência parcial coerente com a população selecionada."""
    return {"sources": {name: {"path": name, "sha256": char * 64, "schema_version": 3,
                                "status": "partial"} for name, char in (("extraction", "a"), ("validation", "b"))},
            "input_instance_count": count + 2, "eligible_count": count,
            "input_decisions": {"approved": count, "rejected": 1, "not_evaluated": 1}}


def test_collisions_preserve_distinct_groups_and_origins(monkeypatch, tmp_path):
    """Confirma bytes integrais mesmo em dois grupos com hash e tamanho iguais."""
    monkeypatch.setattr("robo_minerador.deduplication.representation_digest", lambda raw: "c" * 64)
    candidates = [candidate(4, "other!!"), candidate(3, "fixture"), candidate(2, "other!!"), candidate(1)]
    records = deduplicate_instances(candidates, source(4))
    reverse = deduplicate_instances(list(reversed(candidates)), source(4))
    for rows in (records, reverse):
        for row in rows:
            row.pop("timestamp")
    assert records == reverse
    instances = records[1:-1]
    assert [row["repository_id"] for row in instances] == [1, 2, 3, 4]
    assert [row["representative_instance_id"] for row in instances] == [f"{n:064x}" for n in (1, 2, 1, 2)]
    assert [row["decision"] for row in instances] == ["approved", "approved", "rejected", "rejected"]
    assert records[-1]["duplicate_group_count"] == 2
    assert len({row["representation_byte_count"] for row in instances}) == 1
    # Restaura horários válidos para conferir a releitura estrita de todas as origens.
    for row in records:
        row["timestamp"] = "2026-01-01T00:00:00+00:00"
    path = tmp_path / "output.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in records) + "\n", encoding="utf-8")
    loaded, started = load_deduplication(path)
    assert loaded == instances and started == records[0]


@pytest.mark.parametrize("count", [0, 2])
def test_no_duplicates_and_empty_selection(count):
    """Aceita população vazia e representantes sem duplicatas."""
    records = deduplicate_instances([candidate(n, str(n)) for n in range(1, count + 1)], source(count))
    assert len(records) == count + 2
    assert records[-1]["representative_count"] == count
    assert records[-1]["duplicate_count"] == records[-1]["duplicate_group_count"] == 0
    assert records[-1]["status"] == "completed"


@pytest.mark.parametrize("corruption", ["reference", "policy", "bool", "total", "decision", "input", "order", "hash"])
def test_rejects_corrupt_publication(corruption):
    """Recusa vínculos, política, tipos e totais adulterados."""
    rows = deepcopy(deduplicate_instances([candidate(1), candidate(2)], source(2)))
    if corruption == "reference":
        rows[2]["representative_instance_id"] = rows[2]["instance_id"]
    elif corruption == "policy":
        rows[0]["policy"]["version"] = True
    elif corruption == "bool":
        rows[1]["repository_id"] = True
    elif corruption == "total":
        rows[-1]["duplicate_group_count"] = 0
    elif corruption == "decision":
        rows[2]["criteria"]["exact_uniqueness"]["status"] = "approved"
    elif corruption == "input":
        rows[0]["input_decisions"]["approved"] = 1
    elif corruption == "order":
        rows[1], rows[2] = rows[2], rows[1]
    else:
        rows[2]["representation_sha256"] = "d" * 64
    with pytest.raises(ValueError):
        validate_deduplication_records(rows)


def test_duplicate_ids_are_rejected():
    """Impede que duas origens compartilhem a mesma identidade de instância."""
    with pytest.raises(ValueError):
        deduplicate_instances([candidate(1), candidate(1)], source(2))
