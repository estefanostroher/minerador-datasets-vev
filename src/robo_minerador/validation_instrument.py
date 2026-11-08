"""Ponte T1/T2 para instrumento readonly e coleta sem repetição da suíte.

configure_instrument deve ser chamado após instalação/preparação e inventário de
dependências, antes de run_phase(unit_suite). Seu retorno InstrumentSession retém
plano/hash e collect(executor, phase_result) incorpora relatórios antes de close.
Nenhum módulo/configuração candidato é importado no host.
"""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from .validation_evidence import validate_events, validate_census, decode_events
from .validation_recipe import _hash


HELPER = Path(__file__).with_name("typescript")
MATRIX = {
    ("22.23.4", "4.1.10", "7.3.6"),
    ("22.23.4", "4.1.11", "8.3.0"),
    ("24.22.2", "5.0.5", "8.3.4"),
    ("26.13.0", "5.0.5", "8.3.5"),
}


class InstrumentUnavailable(ValueError):
    """Impede iniciar suíte quando a instalação/API não está comprovada."""


def _json_bytes(value):
    """Serializa metadados completos sem NaN nem dependência de encoding do host."""
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()


def _command_packages(context, recipe):
    """Confere folhas Vitest declaradas nos scripts originais da receita.

    Shell arbitrário e delegação dinâmica não reconhecida ficam unsupported. Não
    seleciona testes: apenas verifica quais processos a passagem original deve abrir.
    """
    manifests = {}
    for file in context["files"]:
        if file["path"].endswith("package.json"):
            try:
                manifests[file["path"]] = json.loads(file["content"])
            except ValueError as error:
                raise InstrumentUnavailable("Manifesto original não interpretável como dados.") from error
    results = []
    for command in recipe["phases"]["unit_suite"]:
        argv = command["argv"]
        if argv[:2] == ["npm", "test"]:
            script = "test"
        elif len(argv) >= 2 and argv[0] == "pnpm":
            script = argv[2] if argv[1] == "run" and len(argv) > 2 else argv[1]
        else:
            raise InstrumentUnavailable("Launcher unitário fora das formas npm/pnpm comprovadas.")
        base = "" if command["cwd"] == "." else command["cwd"] + "/"
        original = manifests.get(base + "package.json", {}).get("scripts", {}).get(script, "")
        if re.match(r"^vitest run(?:\s|$)", original) and not re.search(r"[;&|`$<>\r\n]", original):
            results.append(["/workspace" + ("/" + command["cwd"] if command["cwd"] != "." else "")])
        elif argv[0] == "pnpm" and original in {"pnpm -r " + script, "pnpm -r run " + script}:
            leaves = []
            for package in recipe["unit_scope"]["packages"]:
                prefix = "" if package["path"] == "." else package["path"] + "/"
                body = manifests.get(prefix + "package.json", {}).get("scripts", {}).get(script, "")
                if re.match(r"^vitest run(?:\s|$)", body) and not re.search(r"[;&|`$<>\r\n]", body):
                    leaves.append("/workspace/" + package["path"] if package["path"] != "." else "/workspace")
                elif body and package["path"] != command["cwd"]:
                    raise InstrumentUnavailable("Folha recursiva possui delegação dinâmica não comprovada.")
            if not leaves:
                raise InstrumentUnavailable("Nenhuma folha Vitest comprovada na passagem recursiva.")
            results.append(leaves)
        else:
            raise InstrumentUnavailable("Script original não permite comprovar os processos Vitest esperados.")
    return results


@dataclass
class InstrumentSession:
    """Retém identidade e plano; coleta somente arquivos da passagem original."""

    workspace: object
    prefix: str
    plan: dict
    instrument_sha256: str
    installations: list
    settings_sha256: str
    command_packages: list

    def collect(self, executor, phase_result):
        """Incorpora protocolo/censo e cobertura global antes de limpar o executor.

        Falta de relatório não equivale a zero. Relatórios posteriores a interrupção
        permanecem indisponíveis, sem segunda execução. Corrupção do protocolo gera
        complete=False e preserva o relatório bruto para diagnóstico operacional.
        """
        commands = []
        for index, command in enumerate(phase_result["commands"]):
            report = None
            if command.get("status") == "not_started":
                commands.append({"complete": False, "limitations": ["Comando não iniciado."], "report": None})
                continue
            relative = f"{self.prefix}/command-{index}.json"
            try:
                raw = executor.read_workspace_files(self.workspace, [relative])[relative]
                decoded = decode_events(raw)
                if len(decoded) != 1 or type(decoded[0].get("protocol_version")) is not int or decoded[0]["protocol_version"] != 1:
                    raise ValueError("Envelope do relatório inválido.")
                report = decoded[0]
                identity = {"session_id": self.workspace.run_id, "repository_id": self.workspace.repository_id,
                            "commit_sha": self.workspace.commit_sha, "instrument_id": self.instrument_sha256}
                if report["original_argv"] != command["argv"] or any(report["expected"].get(key) != value for key, value in identity.items()):
                    raise ValueError("Identidade/argv do relatório diverge da passagem executada.")
                declarations = dict(self.plan["declarations"])
                for worker in report["workers"]:
                    for key, declaration in worker["declarations"].items():
                        original = declarations.get(key)
                        if original and original.get("associated"):
                            if declaration != original:
                                raise ValueError("Declaração associada diverge do catálogo confiável.")
                        elif original:
                            declarations[key] = {**original, "modifiers": declaration["modifiers"]}
                        else:
                            declarations[key] = {**declaration, "associated": False, "static_verified": False}
                emitters = {}
                for event in report["events"]:
                    if event["event"] == "run_started" and event["role"] == "coordinator":
                        emitters[event["emitter_id"]] = "coordinator"
                for suite in report["suites"]:
                    for module in suite["modules"]:
                        if module.get("worker_id"):
                            emitters[module["worker_id"]] = "worker"
                census = validate_census(report, self.command_packages[index], self.plan)
                expected = {**identity, "declarations": declarations, "points": self.plan["points"],
                            "emitters": emitters, "census_complete": census["complete"]}
                evidence = validate_events(report["events"], expected)
                evidence["complete"] = evidence["complete"] and report["complete"] is True and report["original_exit"]["code"] == command["exitcode"] and not any(command.get(key) for key in (
                    "oom", "timeout", "cancelled", "capture_loss", "daemon_error", "inspect_error", "cleanup_error"))
                coverage = {"associated_tests": {"supported": False, "status": "unavailable", "scope": "associated_tests",
                             "lines": None, "branches": None, "reason": "Provider global não fornece atribuição confiável por caso."},
                            "full_unit_suite": [{"supported": suite["coverage"] is not None,
                             "status": "available" if suite["coverage"] is not None else "unavailable", "scope": "full_unit_suite",
                             "provider_data": suite["coverage"], "suite_id": suite["suite_id"],
                             "reason": None if suite["coverage"] is not None else "Provider compatível ausente ou coleta global não habilitada na configuração original."}
                             for suite in report["suites"]]}
                commands.append({"complete": evidence["complete"], "evidence": evidence, "census": census,
                                 "coverage": coverage, "report": report,
                                 "report_sha256": hashlib.sha256(raw).hexdigest()})
            except (ValueError, KeyError, OSError, RuntimeError, TypeError, AttributeError, IndexError) as error:
                commands.append({"complete": False, "limitations": ["Relatório indisponível/inválido: " + str(error)],
                                 "report": report})
        return {"complete": len(commands) == len(self.workspace.recipe()["phases"]["unit_suite"])
                and bool(commands) and all(command["complete"] for command in commands),
                "commands": commands, "instrument_sha256": self.instrument_sha256,
                "plan": self.plan, "installations": self.installations, "settings_sha256": self.settings_sha256}


def configure_instrument(workspace, verified, recipe, executor):
    """Negocia instalação efetiva, gera plano T1 e monta pacote confiável readonly.

    Não executa suíte nem importa configs. Recusa divergências antes de preencher
    bootstrap_argv. O executor possui a área temporária até close(). Fontes originais
    e lockfiles não são escritos; apenas namespace próprio é criado no volume.
    """
    if workspace.bootstrap_argv or workspace.instrument_mount or workspace.next_phase != 2 or workspace.first_failure:
        raise InstrumentUnavailable("Instrumento requer instalação/preparação concluídas uma única vez.")
    if _hash(recipe) != workspace.recipe_sha256 or verified["snapshot"] != verified["snapshot_artifact"].reference():
        raise InstrumentUnavailable("Receita ou snapshot diverge do workspace.")
    artifact = verified["snapshot_artifact"]
    if artifact.repository_id != workspace.repository_id or artifact.commit_sha != workspace.commit_sha:
        raise InstrumentUnavailable("Contexto verificado pertence a outro workspace.")
    inventory = executor.collect_dependencies(workspace)
    paths = sorted({package["path"].removesuffix("/package.json") for package in inventory["packages"] if package.get("name") == "vitest"})
    if not paths:
        raise InstrumentUnavailable("Vitest efetivo ausente após instalação original.")
    installations = executor.inspect_instrument_installations(workspace, paths)
    for installation in installations:
        tools = installation["tools"]
        if (tools["node"], tools["vitest"], tools["vite"]) not in MATRIX or tools["vitest"] != recipe["tools"]["vitest"]:
            raise InstrumentUnavailable("Combinação Node/Vitest/Vite não comprovada: " + json.dumps(tools))
    packages = _command_packages(verified["context"], recipe)
    payload = {"files": verified["context"]["files"], "instances": list(verified["instances"].values()),
               "identity": {"repository_id": workspace.repository_id, "commit_sha": workspace.commit_sha}}
    result = subprocess.run(["node", str(HELPER / "validation-plan.mjs")], input=_json_bytes(payload),
                            capture_output=True, timeout=180)
    if result.returncode:
        raise InstrumentUnavailable("Gerador AST confiável falhou: " + result.stderr.decode(errors="replace"))
    plan = json.loads(result.stdout)
    directory = tempfile.TemporaryDirectory(prefix="robo-validation-instrument-")
    try:
        root = Path(directory.name)
        for file in HELPER.glob("validation-*.mjs"):
            shutil.copyfile(file, root / file.name)
        compiler = HELPER / "node_modules" / "typescript"
        if json.loads((compiler / "package.json").read_text())["version"] != "6.0.3":
            raise InstrumentUnavailable("TypeScript confiável deve ser 6.0.3.")
        shutil.copytree(compiler, root / "node_modules" / "typescript")
        manifest = {file.relative_to(root).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
                    for file in sorted(root.rglob("*")) if file.is_file()}
        instrument_id = hashlib.sha256(_json_bytes(manifest)).hexdigest()
        prefix = ".robo-validation-" + workspace.run_id
        settings = {"identity": {"session_id": workspace.run_id, "repository_id": workspace.repository_id,
                                 "commit_sha": workspace.commit_sha, "instrument_id": instrument_id},
                    "workspace_root": "/workspace", "output_root": "/workspace/" + prefix,
                    "plan": plan, "installations": installations, "command_packages": packages, "manifest": manifest}
        settings_bytes = _json_bytes(settings)
        (root / "settings.json").write_bytes(settings_bytes)
        executor.write_workspace_files(workspace, {prefix + "/identity.json": _json_bytes(settings["identity"])})
        executor.register_instrument_directory(directory)
        workspace.instrument_mount = str(root)
        workspace.bootstrap_argv = ("node", "/instrument/validation-command.mjs")
        return InstrumentSession(workspace, prefix, plan, instrument_id, installations, hashlib.sha256(settings_bytes).hexdigest(), packages)
    except BaseException:
        directory.cleanup()
        raise
