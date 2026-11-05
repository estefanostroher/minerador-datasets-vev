"""Preserva contexto textual verificado em memória, com limites do Snapshot."""

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from operator import itemgetter
from pathlib import PurePosixPath

from .discovery import EXPECTED_API_ERRORS, _error_context
from .triage_snapshot import Snapshot, SnapshotUnavailable, is_relevant
from . import config

SOURCE_SUFFIXES = frozenset({".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".cjs", ".mjs"})
TEXT_SUFFIXES = SOURCE_SUFFIXES | frozenset({
    ".md", ".mdx", ".markdown", ".txt", ".rst", ".json", ".jsonc", ".yaml", ".yml",
    ".toml", ".xml", ".html", ".css", ".scss", ".csv", ".snap", ".svg", ".lock",
})

ANALYSIS_SUFFIXES = SOURCE_SUFFIXES | frozenset({
    '.md', '.mdx', '.markdown', '.txt', '.rst', '.json', '.jsonc', '.yaml', '.yml', '.toml',
})

_INVENTORY_CAPABILITY = object()


class SnapshotInventory(list):
    """Transporta inventário atestado pelo reader; JSON sozinho não autoriza V3.

    O loader deve chamar project_snapshot novamente sobre o store offline e passar
    este objeto a validate_output. Uma lista desserializada não prova integridade.
    """

    def __init__(self, entries, reference, files, complete, issues, *, _capability=None):
        """Exige capacidade privada da projeção; dados públicos não emitem atestado."""
        if _capability is not _INVENTORY_CAPABILITY:
            raise TypeError('SnapshotInventory somente pode ser emitido por project_snapshot.')
        super().__init__(entries)
        # Bytes imutáveis impedem que referências compartilhadas alterem o vínculo.
        self._binding = json.dumps({
            'reference': reference, 'hashes': {item['path']: item['sha256'] for item in files},
            'complete': complete, 'issues': issues, 'digest': self._fingerprint(),
        }, sort_keys=True, ensure_ascii=True).encode('ascii')

    @property
    def _issues(self):
        """Devolve cópia das issues atestadas sem expor estado mutável compartilhado."""
        return json.loads(self._binding)['issues']

    def _fingerprint(self):
        """Detecta mudanças no inventário depois da verificação dos objetos Git."""
        return hashlib.sha256(json.dumps(self, sort_keys=True, ensure_ascii=True).encode()).hexdigest()

    def verifies(self, reference, files, complete):
        """Confere referência, escopo e bytes textuais realmente projetados."""
        binding = json.loads(self._binding)
        hashes = binding['hashes']
        return (json.dumps(reference, sort_keys=True) == json.dumps(binding['reference'], sort_keys=True)
                and binding['digest'] == self._fingerprint()
                and type(complete) is bool and (not complete or binding['complete'])
                and len(files) == len(hashes)
                and {item['path']: hashlib.sha256(item['content'].encode('utf-8')).hexdigest()
                     for item in files} == hashes
                and all(item.get('sha256') == hashes.get(item['path']) for item in files))


@dataclass
class SnapshotProjection:
    """Agrupa entradas V3 para Analyzer; snapshot íntegro difere de busca completa."""

    files: list[dict]
    issues: list[dict]
    snapshot: dict
    inventory: SnapshotInventory
    analysis_complete: bool
    execution_requirements: list[dict]


def project_snapshot(store, artifact, *, cancel=None):
    """Projeta texto UTF-8 em lotes limitados; preservação integral pertence ao store.

    Falhas de integridade/cancelamento propagam SnapshotUnavailable. O orçamento
    textual não invalida o cache e omissões relevantes tornam a busca incompleta.
    """
    files, issues, requirements, selected = [], [], [], []
    complete = True
    budget = getattr(config, 'SNAPSHOT_MAX_TEXT_BYTES', 32 * 1024 * 1024)
    with store.open_reader(artifact, cancel=cancel) as reader:
        reference = reader.artifact.reference()
        entries = _pinned_entries(reader)
        for entry in sorted(entries, key=_entry_priority):
            if cancel is not None and cancel.is_set():
                raise SnapshotUnavailable('SNAPSHOT_CANCELLED', 'Projeção cancelada.')
            path = entry.get('path')
            kind = 'gitlink' if entry['type'] == 'commit' else 'lfs_pointer' if entry.get('lfs_pointer') else None
            if kind:
                requirements.append({'type': kind, 'path': path or entry['path_base64'], 'status': 'not_evaluated'})
            if path is None:
                raw_path = base64.b64decode(entry['path_base64'], validate=True)
                if not _analysis_relevant(raw_path.decode('utf-8', errors='replace')):
                    continue
                complete = False
                issues.append({'code': 'UNANALYZABLE_PATH', 'message': 'Caminho Git não representável em UTF-8.'})
                continue
            if not _analysis_relevant(path):
                continue
            if (entry['type'] != 'blob' or entry['mode'] == '120000' or kind
                    or path.startswith('node_modules/') or '/node_modules/' in path):
                complete = False
                issues.append({'code': 'UNANALYZED_TEXT', 'path': path, 'message': 'Texto relevante fora do escopo estático regular.'})
                continue
            if entry['size'] > budget:
                complete = False
                issues.append({'code': 'ANALYSIS_TEXT_LIMIT', 'path': path, 'message': 'Orçamento de análise textual esgotado; bytes permanecem no snapshot.'})
                continue
            selected.append(path)
            budget -= entry['size']
        # Um único cat-file batch, limitado previamente pelo orçamento textual.
        contents = reader.read_blobs(selected) if selected else {}
        for path, raw in contents.items():
            try:
                text = raw.decode('utf-8')
                if '\0' in text:
                    raise UnicodeError()
            except UnicodeError:
                complete = False
                issues.append({'code': 'UNANALYZED_TEXT', 'path': path, 'message': 'Extensão textual contém bytes não analisáveis.'})
                continue
            files.append({'path': path, 'content': text, 'sha256': hashlib.sha256(raw).hexdigest()})
        files.sort(key=itemgetter('path'))
        inventory = SnapshotInventory(entries, reference, files, complete, issues,
                                      _capability=_INVENTORY_CAPABILITY)
    return SnapshotProjection(files, issues, reference, inventory, complete, requirements)


def _pinned_entries(reader):
    """Enumera o manifesto fixado pelo reader sem repetir fsck ou inventário Git.

    A API pública do reader fornece blobs, mas não entradas. O hash publicado
    permite reler somente o JSON e exigir os mesmos bytes da abertura verificada.
    """
    try:
        raw = (reader.artifact.storage_path / 'manifest.json').read_bytes()
        if hashlib.sha256(raw).hexdigest() != reader.artifact.manifest_sha256:
            raise ValueError('Manifesto divergente.')
        return json.loads(raw)['entries']
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SnapshotUnavailable('SNAPSHOT_CORRUPT', 'Manifesto diverge da abertura verificada.') from error


def _entry_priority(entry):
    """Prioriza fontes e mantém ordem lexical sem decodificar blobs binários."""
    return _source_priority(entry.get('path', entry.get('path_base64', '')))


def _analysis_relevant(path):
    """Inclui documentação e configurações sem extensão, mas exclui imagens e estilos."""
    name = PurePosixPath(path).name.lower()
    return (PurePosixPath(path).suffix.lower() in ANALYSIS_SUFFIXES
            or bool(re.fullmatch(r'readme|\.mocharc|\.jestrc|mocha\.opts', name)))


def _source_priority(path: str) -> tuple[bool, str]:
    """Prioriza fontes para preservar análise útil quando o orçamento se esgota."""
    return PurePosixPath(path).suffix.lower() not in SOURCE_SUFFIXES, path


class ExtractionSnapshot(Snapshot):
    """Reutiliza integridade e orçamentos e conserva também o BOM original."""

    def _decode(self, data: dict, expected_sha: str) -> bytes:
        """Registra o BOM somente depois de validar o conteúdo e o hash Git."""
        content = super()._decode(data, expected_sha)
        self._last_bom = content.startswith(b"\xef\xbb\xbf")
        return content

    def read_text(self, path: str) -> str:
        """Preserva bytes UTF-8 originais, inclusive BOM, sem alterar os limites."""
        if path in self._texts:
            return self._texts[path]
        text = super().read_text(path)
        if self._last_bom:
            text = "\ufeff" + text
            self._texts[path] = text
        return text


def collect_context(client, candidate: dict) -> tuple[list[dict], list[dict]]:
    """Recupera fontes antes de contexto; omissões jamais comprovam ausência.

    Falhas individuais preservam arquivos anteriores. Extensões desconhecidas,
    binários e limites geram issues globais conservadoras enviadas ao analisador.
    Erros de programação não são convertidos em pendências.
    """
    files, issues = [], []
    try:
        snapshot = ExtractionSnapshot(client, candidate)
    except SnapshotUnavailable as error:
        return files, [{"code": error.code, "message": error.message}]
    paths = [path for path in snapshot.paths if is_relevant(path)]
    paths.sort(key=_source_priority)
    for path in paths:
        suffix = PurePosixPath(path).suffix.lower()
        if suffix and suffix not in TEXT_SUFFIXES:
            issues.append({"code": "UNINSPECTED_CONTEXT", "path": path,
                           "message": "Arquivo de contexto desconhecido ou binário não inspecionado."})
            continue
        try:
            text = snapshot.read_text(path)
            if "\0" in text:
                issues.append({"code": "BINARY_CONTEXT", "path": path,
                               "message": "Arquivo contém conteúdo binário não inspecionado."})
                continue
        except SnapshotUnavailable as error:
            issues.append({"code": error.code, "message": error.message, "path": path})
            continue
        except EXPECTED_API_ERRORS as error:
            issues.append({"code": "SNAPSHOT_API_ERROR", "path": path,
                           "message": "Falha operacional ao recuperar conteúdo do snapshot.",
                           **_error_context(error)})
            break
        files.append({"path": path, "content": text,
                      "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()})
    return sorted(files, key=itemgetter("path")), issues
