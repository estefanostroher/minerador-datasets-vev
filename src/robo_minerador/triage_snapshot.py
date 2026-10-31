"""Recupera somente objetos imutáveis, sem executar conteúdo dos candidatos."""

import base64
import binascii
import hashlib
import json
import re
from pathlib import PurePosixPath

from github import GithubException
from requests.exceptions import RequestException


MAX_FILES = 20_000
MAX_FILE_BYTES = 1_048_576
MAX_TOTAL_BYTES = 8_388_608
MAX_CONTENT_READS = 512
IGNORED_DIRECTORIES = frozenset({"node_modules", ".git"})
ROOT_ARTIFACT_DIRECTORIES = frozenset({
    "dist", "build", "coverage", "generated", "__generated__", ".next", ".nuxt", "out",
})


def _api_call(operation, *args, **kwargs):
    """Converte somente JSON HTTP inválido em erro operacional com mensagem sanitizada.

    Deve envolver apenas get_repo/requestJsonAndCheck, nunca parsers locais ou
    avaliação científica. Preserva a causa para diagnóstico sem expor seu corpo.
    """
    try:
        return operation(*args, **kwargs)
    except json.JSONDecodeError as error:
        raise RequestException("Resposta JSON inválida recebida da API do GitHub.") from error


class SnapshotUnavailable(ValueError):
    """Representa limitação de inspeção, nunca ausência científica de evidência."""

    def __init__(self, code: str, message: str):
        """Expõe código estável e mensagem em português para o coordenador."""
        super().__init__(message)
        self.code = code
        self.message = message


def is_relevant(path: str) -> bool:
    """Exclui dependências inequívocas e artefatos na raiz, sem resolver links.

    Nomes internos como packages/build ou src/vendor não demonstram artefatos.
    Não se infere a natureza de diretórios aninhados somente por seu nome.
    """
    directories = [part.lower() for part in PurePosixPath(path).parts[:-1]]
    return (not any(part in IGNORED_DIRECTORIES for part in directories)
            and not (directories and directories[0] in ROOT_ARTIFACT_DIRECTORIES))


def is_license_path(path: str) -> bool:
    """Reconhece nomes candidatos, inclusive licenças adicionais de subpacotes."""
    return bool(re.match(r"^((?:.+-)?licen[cs]e|copying|copyright|unlicen[cs]e)([._-].*)?$",
                         PurePosixPath(path).name, re.IGNORECASE))


class Snapshot:
    """Snapshot público limitado em memória; falhas da API são propagadas.

    Limites: 20 mil entradas, 512 blobs, 1 MiB por blob e 8 MiB acumulados.
    Links relevantes impedem inferir ausência; não se inspecionam submódulos.
    A licença reconhecida pelo GitHub não audita licenças de cada subpacote.
    """

    def __init__(self, client, candidate: dict):
        """Confirma identidade pública e recupera árvore recursiva no commit fixado."""
        self.commit_sha = candidate.get("commit_sha")
        if not isinstance(self.commit_sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", self.commit_sha):
            raise SnapshotUnavailable("missing_commit_sha", "SHA imutável ausente ou inválido.")
        self.client = client
        repository = _api_call(client.get_repo, candidate["repository_id"])
        if repository.id != candidate["repository_id"]:
            raise SnapshotUnavailable("repository_mismatch", "Identidade do repositório divergente.")
        if repository.private:
            raise SnapshotUnavailable("repository_not_public", "O repositório não está público.")
        self.full_name = repository.full_name
        self._base_url = f"/repos/{self.full_name}"
        # GitHub aceita commit-ish em git/trees e resolve sua árvore sem seguir branch.
        _, tree = _api_call(
            client.requester.requestJsonAndCheck,
            "GET", f"{self._base_url}/git/trees/{self.commit_sha}",
            parameters={"recursive": "1"},
        )
        if tree.get("truncated") is not False:
            raise SnapshotUnavailable("truncated_tree", "Árvore incompleta; ausência não verificável.")
        if len(tree["tree"]) > MAX_FILES:
            raise SnapshotUnavailable("file_limit", "Limite de entradas da árvore excedido.")
        self._entries = {}
        for entry in tree["tree"]:
            path = entry["path"]
            if entry["type"] == "blob" and entry["mode"] in {"100644", "100755"}:
                self._entries[path] = entry
            elif is_relevant(f"{path}/_") and entry["mode"] in {"160000", "120000"}:
                raise SnapshotUnavailable("uninspected_link", "Submódulo ou link relevante não inspecionado.")
        self.paths = sorted(self._entries)
        self._texts = {}
        self._total_bytes = 0
        self._content_reads = 0

    def _decode(self, data: dict, expected_sha: str) -> bytes:
        """Valida resposta e hash Git antes de aceitar texto como conteúdo do blob."""
        if data.get("sha") != expected_sha or data.get("encoding") != "base64":
            raise SnapshotUnavailable("blob_mismatch", "Resposta não corresponde ao blob da árvore.")
        encoded = data.get("content", "")
        if len(encoded) > MAX_FILE_BYTES * 2:
            raise SnapshotUnavailable("content_limit", "Conteúdo excede o limite por arquivo.")
        try:
            content = base64.b64decode("".join(encoded.split()), validate=True)
        except (ValueError, binascii.Error) as error:
            raise SnapshotUnavailable("invalid_content", "Conteúdo base64 não verificável.") from error
        if len(content) > MAX_FILE_BYTES:
            raise SnapshotUnavailable("content_limit", "Conteúdo excede o limite por arquivo.")
        header = f"blob {len(content)}\0".encode("ascii")
        if hashlib.sha1(header + content).hexdigest() != expected_sha.lower():
            raise SnapshotUnavailable("blob_mismatch", "Hash do conteúdo diverge do blob da árvore.")
        return content

    def read_text(self, path: str) -> str:
        """Lê UTF-8 estrito pelo blob SHA; cache e orçamento existem só em memória."""
        if path in self._texts:
            return self._texts[path]
        if path not in self._entries:
            raise SnapshotUnavailable("unknown_path", "Caminho não é blob regular da árvore.")
        entry = self._entries[path]
        if entry.get("size", 0) > MAX_FILE_BYTES:
            raise SnapshotUnavailable("content_limit", "Conteúdo excede o limite por arquivo.")
        if self._content_reads >= MAX_CONTENT_READS or self._total_bytes + entry.get("size", 0) > MAX_TOTAL_BYTES:
            raise SnapshotUnavailable("content_budget", "Orçamento de leitura do snapshot excedido.")
        self._content_reads += 1
        _, data = _api_call(
            self.client.requester.requestJsonAndCheck,
            "GET", f"{self._base_url}/git/blobs/{entry['sha']}",
        )
        content = self._decode(data, entry["sha"])
        self._total_bytes += len(content)
        if self._total_bytes > MAX_TOTAL_BYTES:
            raise SnapshotUnavailable("content_budget", "Orçamento de leitura do snapshot excedido.")
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise SnapshotUnavailable("unsupported_encoding", "Texto não é UTF-8 verificável.") from error
        self._texts[path] = text
        return text

    def license(self) -> dict | None:
        """Retorna path/blob_sha/spdx_id reconhecidos no SHA, ou ausência comprovável.

        Um 404 com arquivo candidato não prova ausência. O reconhecimento cobre
        somente o arquivo retornado pelo GitHub, não as licenças de subpacotes.
        Consulta o endpoint mesmo com nomes não reconhecidos pela heurística local.
        """
        if self._content_reads >= MAX_CONTENT_READS or self._total_bytes >= MAX_TOTAL_BYTES:
            raise SnapshotUnavailable("content_budget", "Orçamento de leitura do snapshot excedido.")
        self._content_reads += 1
        try:
            _, data = _api_call(
                self.client.requester.requestJsonAndCheck,
                "GET", f"{self._base_url}/license", parameters={"ref": self.commit_sha},
            )
        except GithubException as error:
            if error.status != 404:
                raise
            if any(is_license_path(path) and is_relevant(path) for path in self.paths):
                raise SnapshotUnavailable("license_unresolved", "API não reconheceu a licença candidata presente na árvore.") from error
            return None
        path = data.get("path")
        entry = self._entries.get(path)
        if entry is None or data.get("sha") != entry["sha"]:
            raise SnapshotUnavailable("license_mismatch", "Licença retornada diverge do caminho/blob da árvore fixada.")
        content = self._decode(data, entry["sha"])
        self._total_bytes += len(content)
        if self._total_bytes > MAX_TOTAL_BYTES:
            raise SnapshotUnavailable("content_budget", "Orçamento de leitura do snapshot excedido.")
        return {"path": path, "blob_sha": entry["sha"],
                "spdx_id": (data.get("license") or {}).get("spdx_id")}
