"""Export T11 engineering ledgers from the evidence volume without rebuilding images.

Run after the real Docker tests with ROBO_T11_EVIDENCE=/evidence/t11. Large CAS
objects remain in miner-evidence; storage-index.json records exact resolvers.
This exports observations, never dataset items or original H-R05 suite results.
"""

import hashlib
import io
import json
import os
import tarfile
from pathlib import Path

import docker

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ("jest-29.7.0", "jest-30.0.0", "vitest-3.2.4", "vitest-4.0.0")
FILES = (
    "manifest.json",
    "baseline.raw.json",
    "traced.raw.json",
    "baseline.normalized.json",
    "traced.normalized.json",
    "trace.jsonl",
    "baseline.execution.json",
    "traced.execution.json",
    "baseline.prepared.json",
    "traced.prepared.json",
    "concurrent-gate.json",
    "unsupported-mode.json",
    "g1-reproductions.json",
    "observables.json",
    "pairing.json",
    "matrix.json",
)


def read(container, path):
    chunks, _ = container.get_archive(path)
    with tarfile.open(fileobj=io.BytesIO(b"".join(chunks))) as archive:
        members = archive.getmembers()
        assert len(members) == 1 and members[0].isfile()
        return archive.extractfile(members[0]).read()


def references(value):
    result = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if (
                isinstance(item, str)
                and len(item) == 64
                and (
                    key.endswith("artifact")
                    or key
                    in {
                        "original_execution",
                        "traced_execution",
                        "original_normalized",
                        "traced_normalized",
                        "original_raw",
                        "traced_raw",
                        "trace",
                        "manifest",
                    }
                )
            ):
                result.add(item)
            elif key in {"supplements", "pairings"}:
                result.update(item.values() if isinstance(item, dict) else [v for _, v in item])
            else:
                result.update(references(item))
    elif isinstance(value, list):
        for item in value:
            result.update(references(item))
    return result


def main():
    lock = json.loads((ROOT / "config/environments.lock.json").read_bytes())
    miner = lock["runtimes"]["miner"]
    assert (
        hashlib.sha256((ROOT / "docker/miner.Dockerfile").read_bytes()).hexdigest()
        == miner["recipe_hash"]
    )
    client = docker.from_env()
    assert client.images.get(miner["tag"]).id == miner["image_id"]
    container = client.containers.create(
        miner["image_id"],
        ["python", "--version"],
        network_mode="none",
        read_only=True,
        volumes={"miner-evidence": {"bind": "/evidence", "mode": "ro"}},
    )
    try:
        for version in VERSIONS:
            remote = "/evidence/t11/" + version
            destination = Path(
                os.environ.get(
                    "ROBO_DIRECT_CALL_EVIDENCE", str(ROOT / "results/artifacts/direct-calls")
                )
            )
            folder = destination / version
            folder.mkdir(parents=True, exist_ok=True)
            store = folder / "storefiles"
            store.mkdir(exist_ok=True)
            pending = set()
            index = {}
            for name in FILES:
                body = read(container, remote + "/" + name)
                (folder / name).write_bytes(body)
                digest = hashlib.sha256(body).hexdigest()
                (store / digest).write_bytes(body)
                index[digest] = {"size": len(body), "location": "storefiles/" + digest}
                if name.endswith(".json"):
                    pending.update(references(json.loads(body)))
            while pending:
                digest = pending.pop()
                if digest in index:
                    continue
                path = remote + "/storefiles/" + digest
                # Docker's HEAD archive API obtains size without opening an
                # unconsumed GET stream that holds the container filesystem lock.
                url = client.api._url("/containers/{0}/archive", container.id)
                with client.api.head(url, params={"path": path}) as response:
                    response.raise_for_status()
                    stat = docker.utils.decode_json_header(
                        response.headers["X-Docker-Container-Path-Stat"]
                    )
                if stat["size"] > 4 * 1024 * 1024:
                    index[digest] = {
                        "size": stat["size"],
                        "location": (
                            f"docker-volume:miner-evidence/t11/{version}/storefiles/{digest}"
                        ),
                    }
                    continue
                body = read(container, path)
                assert hashlib.sha256(body).hexdigest() == digest
                (store / digest).write_bytes(body)
                index[digest] = {"size": len(body), "location": "storefiles/" + digest}
                if body.startswith(b"{"):
                    # JSONL traces/logs are retained and hashed as opaque objects;
                    # only single JSON envelopes contain recursive CAS references.
                    try:
                        envelope = json.loads(body)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    pending.update(references(envelope))
            (folder / "storage-index.json").write_text(
                json.dumps(
                    {"purpose": "engineering_fixture_not_dataset", "objects": index},
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            matrix = json.loads((folder / "matrix.json").read_bytes())
            print(
                json.dumps(
                    {
                        "version": version,
                        "cases": len(matrix["cases"]),
                        "associations": {k: len(v) for k, v in matrix["associations"].items()},
                        "objects": len(index),
                    }
                )
            )
    finally:
        container.remove(force=True)
        client.close()


if __name__ == "__main__":
    main()
