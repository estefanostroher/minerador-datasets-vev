"""Protege integridade, referências, contagens e recursos da publicação v2."""

from copy import deepcopy

import pytest

from robo_minerador.extraction_analyzer import VALIDATION
from robo_minerador.extraction_format import compact_records, expand_instance
from robo_minerador.extraction_format import CONTEXT_POLICY
from robo_minerador.jsonl import write_jsonl


def repository(identity=1, issues=None):
    """Cria contexto legado com conteúdo e dependências verificáveis."""
    return {"record_type": "repository", "repository_id": identity,
            "repository_url": f"https://example.org/{identity}", "commit_sha": "a" * 40,
            "extracted_at": "2026-11-02T00:00:00Z", "analyzer": {"version": "fixed"},
            "extraction_status": "partial", "summary": {"instance_count": 1, "approved": 0},
            "context": {"files": [{"path": "b.ts", "content": "b", "sha256": "hash-b"},
                                   {"path": "a.ts", "content": "a", "sha256": "hash-a"}],
                        "local_dependencies": [{"source": "b.ts", "target": "a.ts"}],
                        "unresolved_references": []}, "issues": issues or []}


def instance(repo):
    """Cria evidências completas cuja expansão deve ser idêntica ao original."""
    return {"record_type": "instance", **{key: repo[key] for key in
            ("repository_id", "repository_url", "commit_sha", "extracted_at")},
            "instance_id": f"unit-{repo['repository_id']}", "decision": "not_evaluated",
            "function": {"reference_code": "function f(x) { return x + 2; }", "signature": "f(x)",
                         "path": "a.ts", "start_line": 1, "end_line": 1},
            "documentation": {"text": "Documentação intacta."},
            "tests": [{"name": "caso\ud800", "code": "f(2)", "assertion_count": 0}],
            "criteria": {"first": {"status": "approved", "reason": "ok", "counter": 2},
                         "second": {"status": "not_evaluated", "reason": "pendente\ud800"}},
            "context_paths": ["a.ts", "b.ts"], "validation": deepcopy(VALIDATION),
            "reasons": ["pendente\ud800"]}


def diagnostic(event, **fields):
    """Cria diagnóstico legado com instante explícito e campos operacionais."""
    return {"record_type": "diagnostic", "stage": "extraction", "event": event,
            "observed_at": "2026-11-02T00:00:00Z", **fields}


def stream(*rows):
    """Envolve registros em execução cujo resumo não pode ser reinterpretado."""
    return [diagnostic("run_started"), *rows, diagnostic("run_finished", status="partial",
            repository_count=2, instance_count=2, approved=0, rejected=0, not_evaluated=2,
            error_count=99)]


@pytest.mark.parametrize("variation", ["default", "subset", "order", "override", "extra", "reason_order"])
def test_roundtrip_preserves_all_evidence_and_catalog(variation):
    """Confere igualdade integral, catálogo global e ausência de mutação da entrada."""
    first, second = repository(), repository(2)
    unit, other = instance(first), instance(second)
    if variation == "subset":
        unit["context_paths"] = ["b.ts"]
    elif variation == "order":
        unit["context_paths"].reverse()
    elif variation == "override":
        unit["validation"] = {**VALIDATION, "executed_test_cases": 7}
    elif variation == "extra":
        unit["reasons"].append("outra evidência")
    elif variation == "reason_order":
        unit["criteria"]["first"]["status"] = "rejected"
        unit["reasons"] = ["pendente\ud800", "ok"]
    raw = stream(first, unit, second, other)
    before = deepcopy(raw)
    output = list(compact_records(iter(raw)))
    catalog, current, expanded = {}, None, []
    for row in output:
        if row["record_type"] == "criterion_reason":
            assert row["reason_id"] not in catalog
            catalog[row["reason_id"]] = row["text"]
        elif row["record_type"] == "repository":
            current = row
            assert row["context"] == first["context"]
        elif row["record_type"] == "instance":
            expanded.append(expand_instance(row, current, catalog))
            if row["repository_id"] == 2 or variation not in ("subset", "order"):
                assert row["context_ref"] == "repository" and "context_paths" not in row
            else:
                assert row["context_paths"] == unit["context_paths"] and "context_ref" not in row
    assert expanded == [unit, other]
    assert expand_instance(unit, first, {}) == unit
    assert raw == before
    assert catalog == {"R001": "ok", "R002": "pendente\ud800"}
    assert output[-1] == {**raw[-1], "schema_version": 2, "error_count": 0, "issue_count": 0}


def test_grouping_details_paths_timestamps_and_late_close():
    """Não funde payloads distintos nem perde ocorrências repetidas e fechamento tardio."""
    base = {"code": "HTTP", "message": "falha", "http_status": 403,
            "rate_limit_headers": {"remaining": "0"}, "other": {"detail": [1]}}
    issues = [{**base, "path": "b.ts"}, {**base, "path": "a.ts"}, {**base, "path": "a.ts"},
              base, {**base, "path": "c.ts", "http_status": 503},
              {**base, "path": "d.ts", "rate_limit_headers": {"remaining": "1"}},
              {**base, "path": "e.ts", "message": "outra"}]
    repo = repository(issues=issues)
    diagnostics = [diagnostic("repository_error", repository_id=1, **issue) for issue in issues]
    diagnostics[0]["observed_at"] = "2026-11-02T00:00:03Z"
    diagnostics[1]["observed_at"] = "2026-11-02T00:00:01Z"
    diagnostics[2]["observed_at"] = "2026-11-02T00:00:02Z"
    close = diagnostic("repository_error", repository_id=None, message="fechamento", http_status=503)
    output = list(compact_records(stream(repo, *diagnostics, repository(2), close)))
    groups = output[1]["issues"]
    assert len(groups) == 5
    assert groups[0] == {**base, "issue_id": "I001", "affected_count": 3,
                         "affected_paths": ["b.ts", "a.ts", "a.ts"]}
    assert "affected_paths" not in groups[1]
    errors = [row for row in output if row.get("event") == "repository_error"]
    assert len(errors) == 6 and errors[-1] == close
    assert errors[0] == {"record_type": "diagnostic", "stage": "extraction", "event": "repository_error",
                         "repository_id": 1, "issue_ref": "I001", "affected_count": 3,
                         "observed_at": "2026-11-02T00:00:01Z", "last_observed_at": "2026-11-02T00:00:03Z"}
    assert output[-1]["error_count"] == 6 and output[-1]["issue_count"] == 8
    assert output[-1]["status"] == "partial"


@pytest.mark.parametrize("finish", [False, True])
def test_prefix_flush_counts_only_seen_occurrences(finish):
    """Publica apenas diagnósticos observados em prefixos sem inventar encerramento."""
    issue = {"code": "budget", "message": "limite", "path": "a.ts"}
    repo = repository(issues=[issue, {**issue, "path": "b.ts"}])
    rows = [diagnostic("run_started"), repo, diagnostic("repository_error", repository_id=1, **issue)]
    if finish:
        rows.append(diagnostic("run_finished", status="partial", error_count=2))
    output = list(compact_records(rows))
    errors = [row for row in output if row.get("event") == "repository_error"]
    assert len(errors) == 1 and errors[0]["affected_count"] == 1
    assert any(row.get("event") == "run_finished" for row in output) == finish
    if finish:
        assert output[-1]["issue_count"] == output[-1]["error_count"] == 1


@pytest.mark.parametrize("failure", ["close", "origin", "write", "schema", "duplicate"])
def test_consumer_failure_closes_source(tmp_path, failure):
    """Fecha origem em cancelamento, falha de publicação ou rejeição de contrato."""
    repo = repository()
    unit = instance(repo)
    closed = []
    rows = stream(repo, unit)
    if failure == "origin":
        unit["commit_sha"] = "divergente"
    elif failure == "schema":
        rows[0]["schema_version"] = 2
    elif failure == "duplicate":
        rows.insert(-1, unit)

    def source():
        """Registra liberação do produtor mesmo quando o consumo é interrompido."""
        try:
            yield from rows
        finally:
            closed.append(True)

    records = compact_records(source())
    if failure == "close":
        next(records)
        records.close()
    elif failure == "write":
        next(records)
        next(records)
        with pytest.raises(UnicodeEncodeError):
            try:
                write_jsonl(tmp_path / "temporary.jsonl", records, compact=True)
            finally:
                records.close()
    else:
        with pytest.raises(ValueError):
            list(records)
    assert closed == [True]


@pytest.mark.parametrize("failure", ["repository", "reason", "duplicate", "context", "missing_default", "origin", "schema"])
def test_expansion_rejects_invalid_references(failure):
    """Impede expansão silenciosa de vínculos ausentes, duplicados ou divergentes."""
    repo = repository()
    output = list(compact_records(stream(repo, instance(repo))))
    compact = next(row for row in output if row["record_type"] == "instance")
    published = output[1]
    catalog = {row["reason_id"]: row["text"] for row in output if row["record_type"] == "criterion_reason"}
    if failure == "repository":
        compact["repository_id"] = 99
    elif failure == "reason":
        catalog.clear()
    elif failure == "duplicate":
        compact["criteria"]["first"]["reason"] = "divergente"
    elif failure == "context":
        compact["context_ref"] = "unknown"
    elif failure == "missing_default":
        published.pop("instance_defaults")
    elif failure == "origin":
        compact["commit_sha"] = "divergente"
    elif failure == "schema":
        compact["schema_version"] = 3
    with pytest.raises(ValueError):
        expand_instance(compact, published, catalog)


def test_lazy_catalog_does_not_consume_next_repository():
    """Impede antecipação do próximo grupo enquanto suas referências são publicadas."""
    first = repository()
    consumed = []

    def source():
        """Marca cada registro solicitado sem acumular uma execução completa."""
        for row in stream(first, instance(first), repository(2)):
            consumed.append(row["record_type"])
            yield row

    records = compact_records(source())
    try:
        next(records)
        next(records)
        assert next(records)["reason_id"] == "R001"
        assert next(records)["reason_id"] == "R002"
        assert next(records)["record_type"] == "instance"
        assert consumed == ["diagnostic", "repository", "instance"]
    finally:
        records.close()


def test_diagnostics_without_exact_issue_match_remain_operational():
    """Preserva detalhes divergentes e não reutiliza referências de outro repositório."""
    issue = {"code": "HTTP", "message": "falha", "path": "a.ts", "http_status": 403}
    changed = diagnostic("repository_error", repository_id=1, **{**issue, "http_status": 503})
    wrong_repo = diagnostic("repository_error", repository_id=2, **issue)
    matched = diagnostic("repository_error", repository_id=1, **issue)
    output = list(compact_records(stream(repository(issues=[issue]), changed, wrong_repo, matched,
                                         repository(2), diagnostic("repository_error", repository_id=2, **issue))))
    assert changed in output and wrong_repo in output
    errors = [row for row in output if row.get("event") == "repository_error"]
    assert len(errors) == 4
    assert sum("issue_ref" in row for row in errors) == 1
    assert output[-1]["error_count"] == output[-1]["issue_count"] == 4


@pytest.mark.parametrize("failure", ["missing_origin", "duplicate_repository", "duplicate_path", "compact_diagnostic"])
def test_publication_rejects_incompatible_definitions(failure):
    """Recusa definições incompletas, repetidas ou já compactadas antes de referenciá-las."""
    repo = repository()
    rows = stream(repo)
    if failure == "missing_origin":
        del repo["commit_sha"]
    elif failure == "duplicate_repository":
        rows.insert(-1, {**repo, "commit_sha": "divergente"})
    elif failure == "duplicate_path":
        repo["context"]["files"].append(deepcopy(repo["context"]["files"][0]))
    elif failure == "compact_diagnostic":
        rows.insert(-1, diagnostic("repository_error", repository_id=1, issue_ref="I001", affected_count=1))
    with pytest.raises(ValueError):
        list(compact_records(rows))


@pytest.mark.parametrize("error_type", [RuntimeError, KeyError, TypeError])
def test_producer_failure_flushes_consumed_diagnostics_and_preserves_exception(error_type):
    """Preserva diagnóstico consumido, identidade da falha e fechamento da origem."""
    issue = {"code": "HTTP", "message": "falha", "path": "a.ts"}
    failure = error_type("Falha do produtor.")

    class FailingSource:
        """Simula iterador que exige fechamento explícito após falhar no avanço."""

        def __init__(self):
            """Prepara prefixo com diagnóstico que será retido pelo adaptador."""
            self.rows = iter([diagnostic("run_started"), repository(issues=[issue]),
                              diagnostic("repository_error", repository_id=1, **issue)])
            self.closed = False

        def __iter__(self):
            """Retorna o próprio produtor para consumo incremental."""
            return self

        def __next__(self):
            """Falha com a mesma exceção após entregar o prefixo conhecido."""
            try:
                return next(self.rows)
            except StopIteration:
                raise failure

        def close(self):
            """Registra a liberação explícita dos recursos do produtor."""
            self.closed = True

    source = FailingSource()
    records = compact_records(source)
    output = []
    with pytest.raises(error_type) as caught:
        for row in records:
            output.append(row)
    assert caught.value is failure
    assert source.closed
    assert [row["record_type"] for row in output] == ["diagnostic", "repository", "diagnostic"]
    assert output[-1] == diagnostic("repository_error", repository_id=1, issue_ref="I001", affected_count=1)
    assert not any(row.get("event") == "run_finished" for row in output)


@pytest.mark.parametrize("interruption", ["close", "throw", "keyboard"])
def test_pending_diagnostics_do_not_delay_cancellation(interruption):
    """Cancela sem publicar pendências por fechamento, throw ou interrupção do produtor."""
    issue = {"code": "HTTP", "message": "falha", "path": "a.ts"}
    closed = []
    failure = KeyboardInterrupt() if interruption == "keyboard" else RuntimeError("Consumidor interrompido.")

    def source():
        """Retém diagnóstico antes de suspender ou interromper o produtor."""
        try:
            yield diagnostic("run_started")
            yield repository(issues=[issue])
            yield diagnostic("repository_error", repository_id=1, **issue)
            if interruption == "keyboard":
                raise failure
            yield diagnostic("no_instances")
            pytest.fail("Cancelamento não deve solicitar outro registro.")
        finally:
            closed.append(True)

    records = compact_records(source())
    next(records)
    next(records)
    if interruption == "keyboard":
        with pytest.raises(KeyboardInterrupt) as caught:
            next(records)
        assert caught.value is failure
    else:
        assert next(records)["event"] == "no_instances"
        if interruption == "close":
            records.close()
        else:
            with pytest.raises(RuntimeError) as caught:
                records.throw(failure)
            assert caught.value is failure
    assert closed == [True]
    assert list(records) == []


def snapshot_repository():
    """Define metadados estruturais V3; integridade de bytes pertence aos testes Git."""
    repo = repository(issues=[{"code": "LIMIT", "message": "limite", "path": "a.ts"}])
    repo["context_policy"] = CONTEXT_POLICY
    repo["execution_requirements"] = [{"type": "environment", "path": "a.ts", "status": "not_evaluated"}]
    repo["context"].update(analysis_complete=True, snapshot={
        "format": "git-bare-v1", "cache_key": "1/" + "a" * 40, "repository_id": 1,
        "commit_sha": "a" * 40, "tree_oid": "b" * 40, "manifest_sha256": "c" * 64,
        "file_count": 3, "total_blob_bytes": 100, "status": "complete"})
    return repo


def test_v3_roundtrip_preserves_origins_hashes_catalog_and_issue_counts():
    """V3 conserva evidências e referências sem duplicar inventário nas unidades."""
    repo = snapshot_repository()
    unit = instance(repo)
    raw = stream(repo, unit, diagnostic("repository_error", repository_id=1, **repo["issues"][0]))
    before = deepcopy(raw)
    rows = list(compact_records(raw, schema_version=3))
    published = next(row for row in rows if row["record_type"] == "instance")
    reasons = {row["reason_id"]: row["text"] for row in rows if row["record_type"] == "criterion_reason"}
    assert expand_instance(published, rows[1], reasons) == unit
    assert rows[0]["schema_version"] == rows[1]["schema_version"] == published["schema_version"] == rows[-1]["schema_version"] == 3
    assert rows[1]["context"] == repo["context"]
    assert rows[-1]["issue_count"] == rows[-1]["error_count"] == 1
    assert "inventory" not in published and "snapshot" not in published
    assert raw == before


@pytest.mark.parametrize("tamper", ["sha", "id", "key", "hash", "missing", "extra", "policy", "status", "count", "requirement", "mixed"])
def test_v3_structural_references_are_strict(tamper):
    """Recusa vínculos incompatíveis antes de publicar ou expandir V3."""
    repo = snapshot_repository()
    reference = repo["context"]["snapshot"]
    if tamper == "sha":
        reference["commit_sha"] = "d" * 40
    elif tamper == "id":
        reference["repository_id"] = 2
    elif tamper == "key":
        reference["cache_key"] = "../outside"
    elif tamper == "hash":
        reference["manifest_sha256"] = "bad"
    elif tamper == "missing":
        del reference["tree_oid"]
    elif tamper == "extra":
        reference["storage_path"] = "C:/outside"
    elif tamper == "policy":
        repo["context_policy"] = "old"
    elif tamper == "status":
        reference["status"] = "partial"
    elif tamper == "count":
        reference["file_count"] = True
    elif tamper == "requirement":
        repo["execution_requirements"][0]["status"] = "approved"
    else:
        unit = instance(repo)
        unit["schema_version"] = 2
        repo["schema_version"] = 3
        with pytest.raises(ValueError):
            expand_instance(unit, repo, {})
        return
    with pytest.raises(ValueError):
        list(compact_records(stream(repo, instance(repo)), schema_version=3))


def test_v3_cannot_be_published_as_v2_and_legacy_does_not_gain_snapshot():
    """Não converte legado em snapshot nem oculta política V3 numa saída V2."""
    with pytest.raises(ValueError):
        list(compact_records(stream(snapshot_repository())))
    legacy = repository()
    expanded = expand_instance(instance(legacy), legacy, {})
    assert "snapshot" not in legacy["context"] and "snapshot" not in expanded
