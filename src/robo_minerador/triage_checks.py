"""Triagem conservadora: indícios estáticos não comprovam execução de suíte.

O parser aceita package.json estrito e comandos diretos conhecidos. Não resolve
shell, aliases, imports dinâmicos, configuração JS nem herança de workspaces.
Nomes de testes isolados e comandos personalizados permanecem pendentes.
As exclusões por diretório são convenções de artefatos, não análise semântica;
arquivos gerados fora dessas convenções podem exigir revisão na extração.
Indícios TS usam conteúdo lexical, não tipagem/AST. Comandos documentados são
extraídos de snippets shell/inline e associados ao manifesto ancestral mais
próximo; seletores, configuração JS e delegação entre pacotes não são resolvidos.
As buscas de existência param ao obter evidência suficiente. Contagens de testes
comprovados descrevem apenas arquivos inspecionados, não o tamanho da suíte.
"""

import json
import re
import shlex
from pathlib import PurePosixPath

from .triage_snapshot import SnapshotUnavailable, is_license_path, is_relevant


ALLOWED_LICENSES = frozenset({"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC"})
CRITERION_NAMES = ("access", "license", "typescript", "test_indicators", "execution_configuration")
SAMPLE_LIMIT = 20
UNIT_FRAMEWORKS = frozenset({"jest", "vitest", "mocha", "ava", "tape", "node:test", "bun:test"})
BROWSER_FRAMEWORKS = frozenset({"@playwright/test", "playwright", "cypress", "webdriverio", "puppeteer"})
TEST_PATH = re.compile(r"(?:^|/)(?:__tests__|tests?|specs?)/|(?:^|/|[._-])(?:test|spec)\.[cm]?[jt]sx?$", re.I)
CODE_PATH = re.compile(r"\.[cm]?[jt]sx?$", re.I)


def _criterion(status: str, reason: str, **evidence) -> dict:
    """Cria um critério uniforme com motivo e evidências limitadas pelo chamador."""
    return {"status": status, "evidence": evidence, "reason": reason}


def _sample(paths: list[str]) -> dict:
    """Mantém contagem total e amostra determinística de até vinte caminhos."""
    return {"paths": sorted(paths)[:SAMPLE_LIMIT], "count": len(paths)}


def _is_browser_path(path: str) -> bool:
    """Identifica convenções explícitas de testes de navegador ou ponta a ponta."""
    return any(part.lower() in {"e2e", "cypress", "playwright", "browser"}
               for part in PurePosixPath(path).parts[:-1])


def _implementation_paths(paths: list[str]) -> list[str]:
    """Seleciona implementações TS sem declarações, testes e artefatos convencionais."""
    return [path for path in paths if re.search(r"\.(?:ts|tsx|mts|cts)$", path, re.I)
            and not re.search(r"\.d\.(?:ts|mts|cts)$", path, re.I)
            and not TEST_PATH.search(path)
            and not re.search(r"(?:^|/)(?:[^/]+\.config|(?:jest|vitest)\.(?:setup|config))\.[cm]?tsx?$", path, re.I)
            and not re.search(r"(?:^|/)(?:fixtures?|__fixtures__|mocks?|__mocks__)/", path, re.I)
            and not re.search(r"(?:[.-](?:generated|gen))\.[^.]+$", path, re.I)]


def _mask_source(text: str) -> str:
    """Mascara comentários e literais para não confundir exemplos com código TS.

    Não analisa JSX, expressões regulares nem interpolação de templates; formas
    não reconhecidas permanecem pendentes em vez de provar ausência semântica.
    """
    pattern = r"('(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`)|//[^\n]*|/\*[\s\S]*?\*/"

    def mask_token(match):
        """Preserva a separação de linhas e marca a presença de literais."""
        return ('""' if match.group(1) else " ") + "\n" * match.group(0).count("\n")

    return re.sub(pattern, mask_token, text)


def _without_declarations(code: str) -> str:
    """Remove declarações simples e blocos ambientes, sem prometer parser TypeScript."""
    pattern = re.compile(r"(?:^|(?<=[;\n}]))\s*(?:export\s+(?:default\s+)?)?(declare\s+|interface\s+|type\s+)")
    while match := pattern.search(code):
        depth = 0
        end = match.end()
        for position in range(end, len(code)):
            character = code[position]
            if character in "({[":
                depth += 1
            elif character in ")}]":
                depth -= 1
                if depth == 0 and character == "}" and not match.group(1).startswith("type"):
                    end = position + 1
                    break
            elif depth == 0 and character in ";\n":
                end = position + 1
                break
        else:
            end = len(code)
        code = code[:match.start()] + "\n" + code[end:]
    return code


def _implementation_kind(text: str) -> str:
    """Distingue indício de runtime, ausência simples e sintaxe não interpretada."""
    code = _without_declarations(_mask_source(text))
    prefix = r"(?:^|[;\n}])\s*(?:export\s+(?:default\s+)?)?"
    if (re.search(prefix + r"(?:const|let|var)\s+(?!enum\b)[^;\n=]+=(?!=)", code)
            or re.search(prefix + r"(?:async\s+)?function\b[^;]*?\([^;]*?\)[^;{]*\{", code)
            or re.search(prefix + r"(?:abstract\s+)?class\s+[\w$]+[^;{]*\{", code)
            or re.search(prefix + r"(?:const\s+)?enum\s+[\w$]+[^;{]*\{", code)
            or re.search(r"(?:^|[;\n])\s*export\s+default\s*[\[{]", code)):
        return "implementation"
    code = re.sub(r"(?:^|[;\n])\s*(?:import\b[^;\n]*|export\s*\{[^}]*\}(?:\s*from[^;\n]*)?)\s*;?", "", code)
    return "absent" if not code.strip(" ;\r\n\t") else "unknown"


def _typescript(snapshot, paths: list[str]) -> dict:
    """Lê candidatos até achar runtime; extensão, declaração ou arquivo vazio não bastam."""
    candidates = _implementation_paths(paths)
    inspected, unknown, confirmed = [], [], []
    limitation = None
    for path in candidates:
        try:
            kind = _implementation_kind(snapshot.read_text(path))
        except SnapshotUnavailable as error:
            limitation = error
            break
        inspected.append(path)
        if kind == "implementation":
            confirmed.append(path)
            break
        if kind == "unknown":
            unknown.append(path)
    evidence = {**_sample(confirmed), "candidate_files": _sample(candidates),
                "inspected_files": _sample(inspected), "unknown_files": _sample(unknown)}
    if limitation:
        evidence["limitation_code"] = limitation.code
    if confirmed:
        return _criterion("approved", "Conteúdo TypeScript com indício de implementação encontrado; sem análise semântica completa.", **evidence)
    if limitation or unknown:
        return _criterion("not_evaluated", limitation.message if limitation else
                          "Conteúdo TypeScript fora das formas interpretadas pelo parser conservador.", **evidence)
    return _criterion("rejected", "Ausência de implementação TS: sem candidatos ou somente conteúdos vazios/declarativos.", **evidence)


def _manifests(snapshot, paths: list[str]) -> dict:
    """Carrega todos os manifests relevantes; JSON inválido é limitação do parser."""
    result = {}
    for path in paths:
        if PurePosixPath(path).name != "package.json":
            continue
        try:
            data = json.loads(snapshot.read_text(path))
        except json.JSONDecodeError as error:
            raise SnapshotUnavailable("manifest_parse", "Manifesto não interpretável como JSON estrito.") from error
        if not isinstance(data, dict):
            raise SnapshotUnavailable("manifest_parse", "Manifesto não contém objeto JSON.")
        for field in ("scripts", "dependencies", "devDependencies", "peerDependencies"):
            if field in data and (not isinstance(data[field], dict)
                                  or not all(isinstance(value, str) for value in data[field].values())):
                raise SnapshotUnavailable("manifest_parse", "Campos do manifesto fora do formato interpretável.")
        result[path] = data
    return result


def _owner(path: str, manifests: dict) -> str | None:
    """Associa o arquivo ao manifesto ancestral mais próximo no monorepositório."""
    owners = [manifest for manifest in manifests
              if str(PurePosixPath(manifest).parent) == "."
              or path.startswith(str(PurePosixPath(manifest).parent) + "/")]
    return max(owners, key=len) if owners else None


def _imports(text: str) -> set[str]:
    """Extrai imports estáticos e requires simples, preservando strings e removendo comentários.

    Não é um parser JS: imports dinâmicos, aliases e construção de nomes não são
    interpretados; associação direta entre teste e função pertence à extração.
    """
    tokens = re.compile(r"('(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`)|//[^\n]*|/\*[\s\S]*?\*/")

    def preserve_string(match):
        """Preserva literais e remove comentários sem colar linhas de código."""
        literal = match.group(1)
        return literal if literal and not literal.startswith("`") else "\n"

    clean = tokens.sub(preserve_string, text)
    pattern = r"^\s*(?:import\s+(?:[^\n;]*?\s+from\s+)?|(?:const|let|var)\s+[^\n;=]+?=\s*require\(\s*)['\"]([^'\"]+)['\"]"
    return set(re.findall(pattern, clean, re.M))


def _unit_matches_command(unit: dict, command: dict) -> bool:
    """Confirma o vínculo estático entre arquivo unitário, pacote e runner conhecido."""
    return (command["kind"] == "unit" and command["owner"] == unit["owner"]
            and command["framework"] in unit["frameworks"])


def _has_matching_command(units: list[dict], commands: list[dict]) -> bool:
    """Indica se já há evidência suficiente para evitar leituras adicionais."""
    return any(_unit_matches_command(unit, command) for unit in units for command in commands)


def _test_indicators(snapshot, paths: list[str], manifests: dict, sources: dict,
                     inspected_imports: dict) -> tuple[dict, list[dict]]:
    """Busca existência até vincular teste e comando; reusa imports no fallback documental.

    Sem vínculo continua nos demais pacotes. O cache contém somente resultados de
    leitura no processo atual; unidades e contagens nunca representam a suíte toda
    quando inspection_complete é falso. Limitações permanecem explícitas.
    """
    candidates = [path for path in paths if TEST_PATH.search(path) and CODE_PATH.search(path)
                  and not re.search(r"\.d\.(?:ts|mts|cts)$", path, re.I)]
    units, browser, unknown, inspected = [], [], [], []
    limitation = None
    matched = False
    for path in candidates:
        # Reclassificar imports em memória não provoca novas leituras de blobs.
        if matched and (path not in inspected_imports or isinstance(inspected_imports[path], SnapshotUnavailable)):
            break
        if path not in inspected_imports:
            try:
                inspected_imports[path] = _imports(snapshot.read_text(path))
            except SnapshotUnavailable as error:
                inspected_imports[path] = error
        imports = inspected_imports[path]
        if isinstance(imports, SnapshotUnavailable):
            limitation = imports
            break
        inspected.append(path)
        owner = _owner(path, manifests)
        data = manifests.get(owner, {})
        dependencies = set().union(*(data.get(field, {}) for field in
                                    ("dependencies", "devDependencies", "peerDependencies")))
        imported_frameworks = UNIT_FRAMEWORKS & imports
        if "@jest/globals" in imports:
            imported_frameworks = imported_frameworks | {"jest"}
        # Imports explícitos prevalecem: dependência irmã não muda o runner do arquivo.
        frameworks = imported_frameworks or (UNIT_FRAMEWORKS & dependencies) or {
            item["framework"] for item in sources["commands"]
            if item["kind"] == "unit" and item["owner"] == owner
        }
        if _is_browser_path(path) or imports & BROWSER_FRAMEWORKS:
            browser.append(path)
        elif frameworks:
            unit = {"path": path, "owner": owner, "frameworks": sorted(frameworks)}
            units.append(unit)
            matched = matched or any(_unit_matches_command(unit, command) for command in sources["commands"])
        else:
            unknown.append(path)
    inspection = {"candidate_files": _sample(candidates), "inspected_files": _sample(inspected),
                  "inspection_complete": len(inspected) == len(candidates)}
    sources["test_inspection"] = inspection
    sources["test_limitation"] = limitation
    evidence = {**inspection, "unit_files": _sample([item["path"] for item in units]), "browser_files": _sample(browser),
                "unknown_files": _sample(unknown), "unit_associations": units[:SAMPLE_LIMIT],
                "unit_association_count": len(units)}
    if limitation:
        return _criterion("not_evaluated", limitation.message, limitation_code=limitation.code, **evidence), units
    if units:
        return _criterion("approved", "Arquivos e framework/imports indicam testes unitários; execução não verificada.", **evidence), units
    if unknown:
        return _criterion("not_evaluated", "Convenções de nomes sem framework/import verificável.", **evidence), units
    declared_frameworks = set().union(*(set(data.get(field, {})) for data in manifests.values()
                                      for field in ("dependencies", "devDependencies", "peerDependencies")))
    if (declared_frameworks & UNIT_FRAMEWORKS
            or any(item["kind"] in {"unit", "unknown"} for item in sources["commands"])
            or any(_configuration_framework(path) for path in sources["configurations"])
            or any(key in data for data in manifests.values() for key in ("jest", "vitest", "ava", "mocha"))
            or sources["limitation"]):
        return _criterion("not_evaluated", "Runner/configuração pode selecionar arquivos fora das convenções reconhecidas.", **evidence), units
    return _criterion("rejected", "Não há indícios unitários; testes exclusivamente de navegador não bastam.", **evidence), units


def _has_unresolved_selection(tokens: list[str]) -> bool:
    """Recusa seletores e argumentos desconhecidos sem fingir resolver configuração.

    Só subcomandos run/watch do Vitest e test do Bun são posicionais conhecidos.
    Flags de relatório/cobertura consomem seu valor sem tratá-lo como arquivo de
    teste. Root, config, project, filtros e qualquer outro posicional ficam
    pendentes, inclusive quando apontam aparentemente ao próprio pacote.
    """
    arguments = tokens[1:]
    if (tokens[0] == "vitest" and arguments[:1] in (["run"], ["watch"])
            or tokens[0] == "bun" and arguments[:1] == ["test"]):
        arguments = arguments[1:]
    switches = {
        "--coverage", "--collectcoverage", "--coverage.enabled", "--coverage.all",
        "--coverage.clean", "--coverage.cleanonrerun", "--coverage.reportonfailure",
        "--coverage.skipfull", "--coverage.excludeafterremap", "--experimental-test-coverage",
        "--run", "--runinband", "--ci", "--silent", "--verbose", "--watch", "--watchall",
        "--color", "--colors", "--nocolor", "--no-color", "--test", "--tap", "--serial",
    }
    value_options = {
        "--coveragedirectory", "--coverage-directory", "--coverage-dir", "--coveragereporters",
        "--coverage-reporter", "--coverage-reporters", "--coverageprovider", "--coverage-provider",
        "--coverage.reporter", "--coverage.reportsdirectory", "--coverage.provider",
        "--coverage.include", "--coverage.exclude", "--collectcoveragefrom",
        "--coveragepathignorepatterns", "--coveragethreshold", "--reporter", "--reporters",
        "--test-reporter", "--test-reporter-destination",
    }
    position = 0
    while position < len(arguments):
        flag, separator, value = arguments[position].partition("=")
        flag = flag.lower()
        if flag in switches:
            if separator and value.lower() not in {"true", "false"}:
                return True
            if not separator and position + 1 < len(arguments) and arguments[position + 1].lower() in {"true", "false"}:
                position += 1
        elif flag in value_options or flag.startswith("--coverage.thresholds."):
            if not separator:
                position += 1
                if position >= len(arguments) or arguments[position].startswith("-"):
                    return True
            elif not value:
                return True
        else:
            return True
        position += 1
    return False


def _command_details(command: str) -> tuple[str, str | None]:
    """Reconhece runner direto; aliases, seletores e configuração não resolvida ficam pendentes."""
    command = command.strip()
    if not command or re.fullmatch(r"echo\s+(['\"]?)(?:Error: )?no test specified\1\s*(?:&&|;)\s*exit\s+1", command, re.I):
        return "placeholder", None
    if re.fullmatch(r"(?:echo\b.*|exit\s+[01]|true|false)", command, re.I):
        return "placeholder", None
    if re.search(r"[;&|<>`\n]|\$\(", command):
        return "unknown", None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return "unknown", None
    if not tokens:
        return "placeholder", None
    if tokens[0] in {"npx", "yarn", "pnpm", "bun"} and len(tokens) > 1:
        if tokens[1] in {"exec", "x"}:
            tokens = tokens[2:]
        elif tokens[0] != "bun" or tokens[1] != "test":
            tokens = tokens[1:]
    if not tokens:
        return "unknown", None
    flags = {token.split("=", 1)[0].lower() for token in tokens[1:]}
    if (flags & {"-h", "-v", "--help", "--version", "--listtests", "--list-tests", "--list",
                 "--showconfig", "--print-config", "--collect-only", "--collectonly", "--dry-run",
                 "--list-reporters", "--list-interfaces", "--listrelatedtests"}
            or tokens[0] == "vitest" and len(tokens) > 1 and tokens[1] in {"list", "init", "bench"}):
        return "non_run", None
    if tokens[0] in {"playwright", "cypress", "wdio"}:
        return "browser", tokens[0]
    if _has_unresolved_selection(tokens):
        return "unknown", None
    if tokens[0] in {"jest", "vitest", "mocha", "ava", "tape"}:
        return "unit", tokens[0]
    if tokens[0] == "node" and "--test" in tokens[1:]:
        return "unit", "node:test"
    if tokens[:2] == ["bun", "test"]:
        return "unit", "bun:test"
    return "unknown", None


def _configuration_paths(paths: list[str]) -> list[str]:
    """Lista configurações convencionais; presença não implica resolver seu conteúdo."""
    return [path for path in paths if re.match(
        r"^(?:tsconfig(?:\.[^.]+)*\.json|(?:jest|vitest|vite|mocha|ava|tape|playwright|cypress)\.config\.[cm]?[jt]s|\.mocharc\..+|\.nvmrc|\.node-version)$",
        PurePosixPath(path).name, re.I)]


def _configuration_framework(path: str) -> str | None:
    """Identifica somente nomes de configuração que podem selecionar testes unitários."""
    name = PurePosixPath(path).name.lower()
    if name.startswith(".mocharc."):
        return "mocha"
    for framework in ("jest", "vitest", "vite", "mocha", "ava", "tape"):
        if name.startswith(framework + ".config."):
            return "vitest" if framework == "vite" else framework
    return None


def _documented_commands(text: str) -> list[str]:
    """Lê comandos em blocos shell, inline ou com prompt; não interpreta prosa livre."""
    commands = []
    fence = None
    shell_block = False
    start = re.compile(r"^(?:npx|npm|yarn|pnpm|bun|node|jest|vitest|mocha|ava|tape)(?:\s|$)")
    for line in text.splitlines():
        line = line.strip()
        marker = re.match(r"^(```+|~~~+)(.*)$", line)
        if marker:
            if fence is None:
                fence = marker.group(1)[0]
                shell_block = marker.group(2).strip().lower() in {
                    "", "sh", "bash", "shell", "console", "terminal", "text", "cmd", "powershell", "zsh",
                }
            elif marker.group(1)[0] == fence:
                fence = None
                shell_block = False
            continue
        snippets = re.findall(r"`([^`]+)`", line) if fence is None else []
        if shell_block or fence is None and line.startswith(("$ ", "> ")):
            snippets.append(re.sub(r"^[$>]\s+", "", line))
        commands.extend(snippet.strip() for snippet in snippets if start.match(snippet.strip()))
    return commands


def _resolve_documented_command(command: str, owner: str | None, manifests: dict) -> str:
    """Resolve apenas npm/pnpm/yarn/bun run de script local, sem seguir delegações."""
    match = re.fullmatch(r"(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(test(?::[\w-]+)?)", command)
    if match and command != "bun test":
        return manifests.get(owner, {}).get("scripts", {}).get(match.group(1), command)
    return command


def _execution_sources(paths: list[str], manifests: dict) -> dict:
    """Reúne scripts, configurações e caminhos documentais, sem ler documentação.

    README é opcional. Documentação textual também pode definir execução, mesmo
    fora de docs/. Falha de leitura preserva limitação, nunca falsa ausência.
    """
    sources = {"commands": [], "documentation": [], "configurations": _configuration_paths(paths),
               "inspected_documentation": [], "limitation": None, "test_limitation": None}
    for path, data in manifests.items():
        for name, command in data.get("scripts", {}).items():
            if re.match(r"^test(?::|$)", name):
                kind, framework = _command_details(command)
                sources["commands"].append({"path": path, "owner": path, "source": "script", "script": name,
                                            "command": command[:500], "kind": kind, "framework": framework})
    documents = [path for path in paths if not is_license_path(path) and (
        re.match(r"^(?:readme|testing|contributing)(?:\..*)?$", PurePosixPath(path).name, re.I)
        or PurePosixPath(path).suffix.lower() in {".md", ".mdx", ".rst", ".txt", ".adoc"})]
    sources["documentation"] = documents
    return sources


def _search_documentation(snapshot, paths: list[str], manifests: dict, sources: dict,
                          inspected_imports: dict) -> tuple[dict, list[dict]]:
    """Consulta documentos só como fallback, parando ao vincular comando e teste.

    Imports já lidos são reclassificados em memória para que comandos documentados
    também possam reconhecer testes com globais de framework, em qualquer pacote.
    """
    for path in sources["documentation"]:
        try:
            text = snapshot.read_text(path)
        except SnapshotUnavailable as error:
            sources["limitation"] = error
            break
        sources["inspected_documentation"].append(path)
        owner = _owner(path, manifests)
        previous_count = len(sources["commands"])
        for command in _documented_commands(text):
            resolved = _resolve_documented_command(command, owner, manifests)
            kind, framework = _command_details(resolved)
            sources["commands"].append({"path": path, "owner": owner, "source": "documentation", "script": None,
                                        "command": command[:500], "kind": kind, "framework": framework})
        if len(sources["commands"]) != previous_count:
            criterion, units = _test_indicators(snapshot, paths, manifests, sources, inspected_imports)
            if _has_matching_command(units, sources["commands"]):
                return criterion, units
    return _test_indicators(snapshot, paths, manifests, sources, inspected_imports)


def _execution_configuration(manifests: dict, paths: list[str], units: list[dict], sources: dict) -> dict:
    """Associa comando ao pacote/framework do teste, sem resolver delegação de workspace."""
    commands = sources["commands"]
    kinds = {item["kind"] for item in commands}
    locks = [path for path in paths if PurePosixPath(path).name in
             {"package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lock", "bun.lockb"}]
    declarations = []
    frameworks = []
    for path, data in manifests.items():
        engines = data.get("engines", {})
        declarations.append({"path": path,
                             "engines": {str(key)[:100]: str(value)[:200] for key, value in
                                         list(engines.items())[:SAMPLE_LIMIT]} if isinstance(engines, dict) else None,
                             "package_manager": str(data["packageManager"])[:200] if "packageManager" in data else None})
        for field in ("dependencies", "devDependencies", "peerDependencies"):
            for name, version in data.get(field, {}).items():
                if name in UNIT_FRAMEWORKS | BROWSER_FRAMEWORKS | {"@jest/globals", "typescript", "ts-node", "tsx"}:
                    frameworks.append({"path": path, "section": field, "name": name, "version": version[:200]})
    matches = []
    match_count = 0
    for unit in units:
        for command in commands:
            if _unit_matches_command(unit, command):
                match_count += 1
                if len(matches) < SAMPLE_LIMIT:
                    matches.append({"unit_file": unit["path"], "owner": unit["owner"],
                                    "framework": command["framework"], "command_path": command["path"],
                                    "command": command["command"], "source": command["source"]})
    limitation = sources["limitation"] or sources["test_limitation"]
    evidence = {**sources["test_inspection"], "manifests": _sample(list(manifests)), "commands": commands[:SAMPLE_LIMIT],
                "command_count": len(commands), "locks": _sample(locks),
                "runtime_declarations": declarations[:SAMPLE_LIMIT], "runtime_declaration_count": len(declarations),
                "framework_declarations": frameworks[:SAMPLE_LIMIT], "framework_declaration_count": len(frameworks),
                "configurations": _sample(sources["configurations"]), "documentation": _sample(sources["documentation"]),
                "inspected_documentation": _sample(sources["inspected_documentation"]),
                "documentation_inspection_complete": (len(sources["inspected_documentation"]) == len(sources["documentation"])
                                                       and sources["limitation"] is None),
                "unit_files": _sample([unit["path"] for unit in units]),
                "matched_commands": matches, "matched_command_count": match_count}
    if limitation:
        evidence["limitation_code"] = limitation.code
    if matches:
        return _criterion("approved", "Comando reconhecido ligado a arquivo unitário do mesmo pacote e framework; execução não verificada.", **evidence)
    if (kinds & {"unknown", "unit"} or sources["documentation"] or sources["configurations"]
            or any(item["name"] in UNIT_FRAMEWORKS for item in frameworks)
            or limitation or any(key in data for data in manifests.values()
                                           for key in ("jest", "vitest", "ava", "mocha"))):
        return _criterion("not_evaluated", "Documentação/configuração ou comando sem vínculo verificável ao pacote/framework unitário; delegações não resolvidas.", **evidence)
    return _criterion("rejected", "Sem fonte de execução unitária: scripts ausentes, informativos, placeholders ou somente navegador, sem documentação/configuração alternativa.", **evidence)


def evaluate_snapshot(snapshot) -> dict:
    """Retorna criteria/readme/reasons; o coordenador acrescenta identidade e decisão.

    SnapshotUnavailable vira critério pendente com código; falhas da API propagam.
    Após rejeição de licença, os demais critérios permanecem não avaliados.
    """
    criteria = {name: _criterion("not_evaluated", "Não avaliado por interrupção antecipada.")
                for name in CRITERION_NAMES}
    paths = [path for path in snapshot.paths if is_relevant(path)]
    readmes = [path for path in paths if re.match(r"^readme(?:\..*)?$", PurePosixPath(path).name, re.I)]
    result = {"criteria": criteria, "readme": {"present": bool(readmes), **_sample(readmes)},
              "reasons": ["Triagem estática; não comprova suíte executável nem cobertura por função.",
                          "Reconhecimento de licença limitado ao arquivo retornado pelo GitHub; não audita subpacotes."]}
    criteria["access"] = _criterion("approved", "Repositório público e árvore completa no SHA selecionado.")
    try:
        license_data = snapshot.license()
        license_paths = [path for path in paths if is_license_path(path)]
        if license_data is None:
            criteria["license"] = _criterion("rejected", "Licença ausente no snapshot.", candidates=_sample(license_paths))
        else:
            allowed = license_data.get("spdx_id") in ALLOWED_LICENSES
            criteria["license"] = _criterion(
                "approved" if allowed else "rejected",
                "Licença reconhecida no SHA e permitida por H-R02." if allowed else
                "Licença não verificável ou fora da lista H-R02.",
                **license_data, candidates=_sample(license_paths),
            )
    except SnapshotUnavailable as error:
        criteria["license"] = _criterion("not_evaluated", error.message, limitation_code=error.code)
    if criteria["license"]["status"] != "approved":
        result["reasons"].append(criteria["license"]["reason"])
        return result
    criteria["typescript"] = _typescript(snapshot, paths)
    try:
        manifests = _manifests(snapshot, paths)
    except SnapshotUnavailable as error:
        for name in ("test_indicators", "execution_configuration"):
            criteria[name] = _criterion("not_evaluated", error.message, limitation_code=error.code)
    else:
        sources = _execution_sources(paths, manifests)
        inspected_imports = {}
        criteria["test_indicators"], units = _test_indicators(snapshot, paths, manifests, sources, inspected_imports)
        if not _has_matching_command(units, sources["commands"]):
            criteria["test_indicators"], units = _search_documentation(
                snapshot, paths, manifests, sources, inspected_imports,
            )
        criteria["execution_configuration"] = _execution_configuration(manifests, paths, units, sources)
    result["reasons"].extend(item["reason"] for item in criteria.values() if item["status"] != "approved")
    return result
