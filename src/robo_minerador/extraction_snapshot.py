"""Preserva contexto textual verificado em memória, com limites do Snapshot."""

import hashlib
from operator import itemgetter
from pathlib import PurePosixPath

from .discovery import EXPECTED_API_ERRORS, _error_context
from .triage_snapshot import Snapshot, SnapshotUnavailable, is_relevant

SOURCE_SUFFIXES = frozenset({".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".cjs", ".mjs"})
TEXT_SUFFIXES = SOURCE_SUFFIXES | frozenset({
    ".md", ".mdx", ".markdown", ".txt", ".rst", ".json", ".jsonc", ".yaml", ".yml",
    ".toml", ".xml", ".html", ".css", ".scss", ".csv", ".snap", ".svg", ".lock",
})


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
