"""Classificação conservadora de documentação e marcadores de CI do snapshot."""

import re
import unicodedata


_HEADINGS = {'quality', 'qualité', 'qualidade', 'test', 'testing', 'tests',
             'tests unitaires', 'unit tests', 'testes'}
_HEADING = re.compile(r'^(#{1,6})[ \t]+(.+?)[ \t]*$')
_FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})(.*)$')
_OPEN_COMMAND = re.compile(r'^```(?:sh|bash|shell|console|text)?$')
_COMMANDS = {
    'test': re.compile(r'npm[ \t]+test'),
    'install': re.compile(r'npm[ \t]+ci'),
    'lint': re.compile(r'npm[ \t]+(?:run[ \t]+lint|lint)'),
}
_CI_ROOT = {'.gitlab-ci.yml', '.circleci/config.yml', 'azure-pipelines.yml',
            '.travis.yml', 'jenkinsfile'}


def _fold(value):
    return unicodedata.normalize('NFC', value).casefold()


def _sort(paths):
    return sorted(paths, key=lambda path: path.encode('utf-8'))


def document_candidates(inventory, package_path):
    """Retorna todas as grafias existentes dos quatro caminhos documentais."""
    prefix = '' if package_path == '.' else package_path.rstrip('/') + '/'
    names = {_fold(path) for path in ('README.md', 'docs/testing.md',
                                      prefix + 'README.md', prefix + 'docs/testing.md')}
    return _sort({path for path in inventory if _fold(path) in names})


def _ci_path(path):
    folded = _fold(path)
    return (folded in _CI_ROOT or
            (folded.startswith('.github/workflows/') and
             folded.endswith(('.yml', '.yaml'))))


def _scan(text):
    """Identifica headings e fences globalmente, sem interpretar seus conteúdos."""
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    headings, fences = [], {}
    opened = None
    for index, line in enumerate(lines):
        if opened is not None:
            start, marker = opened
            if re.fullmatch(r' {0,3}' + re.escape(marker[0]) +
                            '{' + str(len(marker)) + r',}[ \t]*', line):
                fences[start] = index
                opened = None
            continue
        fence = _FENCE.match(line)
        if fence:
            opened = (index, fence.group(1))
            continue
        match = _HEADING.match(line)
        if match:
            title = re.sub(r'[ \t]+#+[ \t]*$', '', match.group(2))
            title = re.sub(r'[ \t]+', ' ', _fold(title).strip())
            unprefixed = re.sub(r'^✅\ufe0f? ', '', title, count=1)
            if unprefixed in {'quality', 'qualité', 'qualidade'}:
                title = unprefixed
            headings.append((index, len(match.group(1)), title))
    return lines, headings, fences, opened


def _sections(headings, length):
    sections = []
    for i, (start, level, title) in enumerate(headings):
        if title not in _HEADINGS:
            continue
        end = next((pos for pos, other_level, _ in headings[i + 1:]
                    if other_level <= level), length)
        if sections and start < sections[-1][1]:
            previous_start, previous_end, previous_title = sections[-1]
            sections[-1] = (previous_start, max(previous_end, end), previous_title)
        else:
            sections.append((start, end, title))
    return sections


def _units(lines, start, end, fences):
    units = []
    index = start + 1
    while index < end:
        line = lines[index]
        if index in fences:
            close = fences[index]
            if not _OPEN_COMMAND.fullmatch(line) or not re.fullmatch(r'```[ \t]*', lines[close]):
                units.append((index, 0, None))
            else:
                for pos in range(index + 1, close):
                    value = lines[pos].strip()
                    if value:
                        units.append((pos, 0, value[2:] if value.startswith('$ ') else value))
            index = close + 1
            continue
        if _FENCE.match(line):
            units.append((index, 0, None))
            break
        cursor = 0
        while cursor < len(line):
            start_tick = line.find('`', cursor)
            if start_tick < 0:
                break
            close = line.find('`', start_tick + 1)
            if (close < 0 or close == start_tick + 1 or
                    (close + 1 < len(line) and line[close + 1] == '`')):
                units.append((index, start_tick, None))
                break
            units.append((index, start_tick, line[start_tick + 1:close].strip()))
            cursor = close + 1
        index += 1
    return units


def classify_documents(files, inventory, package_path):
    """Classifica somente evidência documental e impedimentos de CI reconhecidos."""
    evidence, reasons = [], []

    def issue(code, message, path='', line=0, **detail):
        reasons.append({'code': code, 'message': message,
                        'evidence': {'path': path, 'line': line, **detail}})

    paths = document_candidates(inventory, package_path)
    groups = {}
    for path in paths:
        groups.setdefault(_fold(path), []).append(path)
    for group in groups.values():
        if len(group) > 1:
            issue('UNIT_SCOPE_UNRESOLVED', 'Grafias documentais concorrentes.', group[0],
                  paths=group, reason='DOCUMENT_PATH_AMBIGUOUS')
    for path in _sort({p for p in inventory if _ci_path(p)}):
        issue('UNIT_SCOPE_UNRESOLVED', 'CI não suportada.', path, reason='CI_WORKFLOW_UNSUPPORTED')

    seen = set()
    for path in paths:
        if path not in files or not isinstance(files[path], str):
            issue('UNIT_SCOPE_UNRESOLVED', 'Documento indisponível.', path)
            continue
        lines, headings, fences, opened = _scan(files[path])
        if opened:
            issue('UNIT_SCOPE_UNRESOLVED', 'Fence não fechado.', path, opened[0] + 1)
        for start, end, title in _sections(headings, len(lines)):
            units = _units(lines, start, end, fences)
            kinds = [next((kind for kind, pattern in _COMMANDS.items()
                           if value is not None and pattern.fullmatch(value)), None)
                     for _, _, value in units]
            first = next((i for i, kind in enumerate(kinds) if kind == 'test'), None)
            for index, ((pos, column, value), kind) in enumerate(zip(units, kinds)):
                key = (path, pos, column)
                if kind == 'test' and key not in seen:
                    evidence.append({'path': path, 'heading': title, 'line': pos + 1,
                                     'command': value})
                    seen.add(key)
                if first is not None and index < first and kind != 'install':
                    code = 'AUTO_RECIPE_PREPARATION_UNRESOLVED'
                elif kind is None:
                    code = 'UNIT_SCOPE_UNRESOLVED'
                else:
                    continue
                issue(code, 'Comando ou sintaxe documental não suportada.', path,
                      pos + 1, column=column + 1, command=value)
    if not evidence:
        issue('UNIT_SCOPE_UNRESOLVED', 'Nenhuma confirmação documental de npm test.')

    # Entrada ambígua precede CI, preparo e os demais motivos de escopo.
    def order(item):
        info = item['evidence']
        priority = (0 if info.get('reason') == 'DOCUMENT_PATH_AMBIGUOUS' else
                    1 if info.get('reason') == 'CI_WORKFLOW_UNSUPPORTED' else
                    2 if item['code'] == 'AUTO_RECIPE_PREPARATION_UNRESOLVED' else 3)
        return priority, info['path'].encode('utf-8'), info['line'], info.get('column', 0), item['code']

    reasons.sort(key=order)
    return {'evidence': evidence, 'issues': reasons, 'confirmed': bool(evidence) and not reasons}
