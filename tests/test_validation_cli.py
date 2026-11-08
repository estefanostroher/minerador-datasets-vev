"""Protege saída e recursos na fronteira CLI, não coberta pelo publicador T4."""

import json
from types import SimpleNamespace

import pytest

from robo_minerador import cli, config, validation
from robo_minerador.validation_format import validate_output_records
from test_validation import controlled_fixture, FixtureExecutor
from test_validation_input import extraction_rows, empty_repository_rows, write_rows


@pytest.fixture
def cli_paths(monkeypatch, tmp_path):
    """Restringe toda publicação a diretório temporário e remove necessidade de token."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(config, "VALIDATION_INPUT_PATH", tmp_path / "input.jsonl")
    output = tmp_path / "output.jsonl"
    monkeypatch.setattr(config, "VALIDATION_OUTPUT_PATH", output)
    return output


def forbidden(*args, **kwargs):
    """Falha se uma proteção permitir ferramentas externas ou trabalho desnecessário."""
    raise AssertionError("Dependência externa indevida.")


def test_zero_candidates_without_token_or_external_tools(monkeypatch, cli_paths, tmp_path, extraction_rows):
    """Usa entrada v3 e escritor reais sem Git, Node, Docker, imagens ou receitas aplicáveis."""
    path, _ = write_rows(tmp_path, empty_repository_rows(extraction_rows))
    monkeypatch.setattr(config, "VALIDATION_INPUT_PATH", path)
    monkeypatch.setattr(cli, "GitTransport", forbidden)
    monkeypatch.setattr(cli, "preflight", forbidden)
    monkeypatch.setattr(cli, "DockerExecutor", forbidden)
    monkeypatch.setattr(cli, "load_recipes", lambda: {"recipes": {}})
    assert cli.main(["validate"]) == 0
    rows = [json.loads(line) for line in cli_paths.read_bytes().splitlines()]
    assert validate_output_records(rows)["status"] == "completed"
    assert rows[0]["source"]["eligible_count"] == 0


@pytest.mark.parametrize("failure", ["input", "catalog", "limits", "git", "node", "docker"])
def test_invalid_prerequisites_preserve_output_even_with_overwrite(monkeypatch, cli_paths, failure, capsys):
    """Impede truncamento por entrada/configuração/preflight inválidos e fecha executor precoce."""
    cli_paths.write_bytes(b"previous-output\r\n")
    group, catalog, source, execution = controlled_fixture()
    executor = FixtureExecutor(execution)

    def invalid(*args, **kwargs):
        """Representa erro global sem expor valores sensíveis ao terminal."""
        raise ValueError("Segredo que não deve ser publicado.")

    monkeypatch.setattr(cli, "load_extraction", invalid if failure == "input" else lambda p: ([group], source))
    monkeypatch.setattr(cli, "load_recipes", invalid if failure == "catalog" else lambda: catalog)
    monkeypatch.setattr(cli, "GitTransport", lambda: SimpleNamespace(preflight=invalid if failure == "git" else lambda: None))
    monkeypatch.setattr(cli, "preflight", invalid if failure == "node" else lambda: None)
    monkeypatch.setattr(executor, "preflight", invalid if failure == "docker" else lambda: None, raising=False)
    monkeypatch.setattr(cli, "DockerExecutor", lambda: executor)
    if failure == "limits":
        monkeypatch.setattr(config, "VALIDATION_CPUS", True)
    assert cli.main(["validate", "--overwrite"]) == 2
    assert cli_paths.read_bytes() == b"previous-output\r\n"
    assert executor.closed == (failure == "docker")
    assert "Segredo" not in capsys.readouterr().out


def test_existing_output_refuses_before_expensive_work(monkeypatch, cli_paths):
    """Recusa substituição sem autorização antes de ler entrada ou sondar ferramentas."""
    cli_paths.write_bytes(b"preserve")
    monkeypatch.setattr(cli, "load_extraction", forbidden)
    monkeypatch.setattr(cli, "DockerExecutor", forbidden)
    assert cli.main(["validate"]) == 2
    assert cli_paths.read_bytes() == b"preserve"


@pytest.mark.parametrize("option", [["--workers", "1"], ["--limit", "1"], ["--input", "a"], ["--output", "b"]])
def test_validate_rejects_unapproved_options(cli_paths, option):
    """Não permite seleção parcial nem paralelismo/caminhos fora do contrato inicial."""
    with pytest.raises(SystemExit) as error:
        cli.main(["validate", *option])
    assert error.value.code == 2
    assert not cli_paths.exists()


@pytest.mark.parametrize("state,expected", [("completed", 0), ("partial", 1), ("failed", 1), ("write", 1), ("cancel", 1), ("open", 1)])
def test_cli_states_and_cleanup(monkeypatch, cli_paths, state, expected, capsys):
    """Liga CLI ao gerador real para decisões/cleanup e injeta falha somente na fronteira I/O."""
    group, catalog, source, execution = controlled_fixture(counts=(3, 2))
    executor = FixtureExecutor(execution)
    monkeypatch.setattr(executor, "preflight", lambda: None, raising=False)
    monkeypatch.setattr(cli, "DockerExecutor", lambda: executor)
    monkeypatch.setattr(cli, "GitTransport", lambda: SimpleNamespace(preflight=lambda: None))
    monkeypatch.setattr(cli, "preflight", lambda: None)
    monkeypatch.setattr(cli, "load_extraction", lambda p: ([group], source))
    monkeypatch.setattr(cli, "load_recipes", lambda: catalog)
    monkeypatch.setattr(validation, "SnapshotStore", lambda: SimpleNamespace(cancel=lambda: None))

    def context(*args):
        """Produz impedimento completo ou contexto controlado conforme o cenário."""
        if state == "failed":
            raise ValueError("Snapshot indisponível.")
        return execution["verified"]

    monkeypatch.setattr(validation, "load_repository_context", context)
    monkeypatch.setattr(validation, "prepare_workspace", lambda *args: SimpleNamespace(environment=execution["environment"]))
    monkeypatch.setattr(validation, "configure_instrument", lambda *args: SimpleNamespace(collect=lambda *args: execution["observation"]))
    if state == "partial":
        executor.cleanup_errors.append({"error": "Limpeza não confirmada."})
    if state in {"write", "cancel", "open"}:
        def interrupted(path, records, **kwargs):
            """Interrompe após a abertura do gerador para conferir cancelamento e fechamento."""
            if state != "open":
                assert next(iter(records))["record_type"] == "run_started"
            raise KeyboardInterrupt if state == "cancel" else OSError("Disco indisponível.")

        monkeypatch.setattr(cli, "write_jsonl", interrupted)
    assert cli.main(["validate"]) == expected
    assert executor.closed
    if state in {"write", "cancel", "open"}:
        assert "cancel" in executor.calls
    else:
        rows = [json.loads(line) for line in cli_paths.read_bytes().splitlines()]
        assert validate_output_records(rows)["status"] == state
        assert f"estado: {state}" in capsys.readouterr().out
