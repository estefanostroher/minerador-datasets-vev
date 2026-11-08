"""Materializa bytes Git verificados em volume Linux exclusivo, sem checkout/filtros."""

import base64
from dataclasses import dataclass, field
import hashlib
import io
import json
from pathlib import PurePosixPath
import posixpath
import re
import tarfile

from .validation_recipe import _hash, _validate_recipe


@dataclass
class ValidationWorkspace:
    """Retém identidade, receita congelada e montagens próprias para as fases.

    recipe_json é a especificação canônica anterior à execução. instrument_mount
    e bootstrap_argv permitem à T3 montar instrumento confiável em /instrument
    somente leitura e envolver argv originais sem editar comandos da receita.
    """

    volume: str
    run_id: str
    repository_id: int
    commit_sha: str
    recipe_json: str
    recipe_sha256: str
    manifest: list
    environment: dict
    tool_volume: str | None = None
    instrument_mount: str | None = None
    bootstrap_argv: tuple = ()
    instrument_environment: dict = field(default_factory=dict)
    next_phase: int = 0
    first_failure: dict | None = None

    def recipe(self):
        """Obtém cópia da receita congelada; mutações externas não alteram orçamentos."""
        return json.loads(self.recipe_json)


def _safe_path(raw):
    """Recusa nomes que escapem da raiz ou colidam com metadados próprios."""
    path = raw.decode("utf-8", errors="strict")
    parts = path.split("/")
    if (not path or path.startswith("/") or any(p in {"", ".", "..", ".git"} for p in parts)
            or "\\" in path or "\0" in path):
        raise ValueError("Caminho Git inseguro para materialização.")
    return path


def _add(archive, path, content, mode=0o644, link=None):
    """Acrescenta entrada determinística sem aplicar atributos Git ou EOL do host."""
    info = tarfile.TarInfo(path)
    info.mode = mode
    if link is not None:
        info.type, info.linkname = tarfile.SYMTYPE, link
        archive.addfile(info)
    else:
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))


def prepare_workspace(store, verified, recipe, executor):
    """Confere blobs/modos/locks e entrega cópia Linux; falhas são de infraestrutura.

    Recebe o dicionário load_repository_context da T1. A árvore é novamente
    conferida offline contra o artefato recebido; nenhum fetch é permitido.
    Submódulos/LFS são recusados conservadoramente, pois o executor não possui
    contrato que prove sua dispensabilidade para os comandos originais.
    """
    _validate_recipe(recipe)
    if recipe["resolution_status"] != "resolved":
        raise ValueError("Receita ainda não resolvida para execução.")
    artifact = verified["snapshot_artifact"]
    if (artifact.reference() != verified["snapshot"] or artifact.repository_id != recipe["repository_id"]
            or artifact.commit_sha != recipe["commit_sha"]):
        raise ValueError("Identidade da receita diverge do contexto verificado.")
    bare = artifact.storage_path / "repository.git"
    tree, entries, total = store.transport.inventory(bare, artifact.commit_sha)
    if (tree != artifact.tree_oid or len(entries) != artifact.file_count or total != artifact.total_blob_bytes):
        raise ValueError("Árvore materializada diverge do artefato verificado.")
    paths = []
    for entry in entries:
        if entry["mode"] not in {"100644", "100755", "120000"} or entry.get("lfs_pointer"):
            raise ValueError("Conteúdo de submódulo/LFS não adquirido; materialização recusada.")
        raw = entry["path"].encode() if "path" in entry else base64.b64decode(entry["path_base64"], validate=True)
        paths.append(_safe_path(raw))
    contents = store.transport.read_blobs(bare, entries)
    for entry, content in zip(entries, contents):
        if entry["mode"] != "120000" and re.fullmatch(
                rb"version https://git-lfs.github.com/spec/v1\r?\n"
                rb"(?:ext-[0-9]+-[a-z0-9.-]+ [^\r\n]+\r?\n)*"
                rb"oid sha256:[0-9a-f]{64}\r?\nsize (?:0|[1-9][0-9]*)\r?\n?", content):
            raise ValueError("Conteúdo LFS não adquirido; ponteiro não equivale ao arquivo original.")
    files = dict(zip(paths, contents))
    for lock in recipe["lockfiles"]:
        index = paths.index(lock["path"]) if lock["path"] in paths else -1
        if (index < 0 or entries[index]["mode"] not in {"100644", "100755"}
                or hashlib.sha256(files[lock["path"]]).hexdigest() != lock["sha256"]):
            raise ValueError("Lockfile original ausente ou divergente.")
    manifest = []
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path, entry, content in zip(paths, entries, contents):
            link = None
            if entry["mode"] == "120000":
                link = content.decode("utf-8", errors="strict")
                target = posixpath.normpath(posixpath.join(posixpath.dirname(path), link))
                if (not link or PurePosixPath(link).is_absolute() or "\\" in link or "\0" in link
                        or target == ".." or target.startswith("../") or ".git" in target.split("/")):
                    raise ValueError("Link Git escaparia do workspace.")
            _add(archive, path, content, 0o755 if entry["mode"] == "100755" else 0o644, link)
            manifest.append({"path": path, "mode": entry["mode"], "oid": entry["oid"],
                             "sha256": hashlib.sha256(content).hexdigest(), "link": link})
        # Apenas objetos próprios e referências fixadas; nunca config/hooks/alternates candidatos.
        for file in (bare / "objects").rglob("*"):
            if file.is_file():
                _add(archive, ".git/objects/" + file.relative_to(bare / "objects").as_posix(), file.read_bytes())
        _add(archive, ".git/HEAD", (artifact.commit_sha + "\n").encode())
        _add(archive, ".git/refs/minerador/snapshot", (artifact.commit_sha + "\n").encode())
        _add(archive, ".git/config", b"[core]\n repositoryformatversion = 0\n bare = false\n filemode = true\n autocrlf = false\n hooksPath = /dev/null\n")
        if (bare / "shallow").exists():
            _add(archive, ".git/shallow", (bare / "shallow").read_bytes())
    return executor.materialize(artifact, recipe, _hash(recipe), manifest, stream.getvalue())
