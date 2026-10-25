"""Resolve engineering-only fixture locks in the frozen npm container (no host npm)."""

import io
import json
import tarfile
from pathlib import Path

import docker

ROOT = Path(__file__).resolve().parents[1]


def main():
    lock = json.loads((ROOT / "config/environments.lock.json").read_bytes())
    client = docker.from_env()
    image = lock["runtimes"]["node22-npm"]["image_id"]
    manifests = [
        ROOT / "tests/fixtures/projects" / name / suffix
        for name, suffix in [
            ("jest-basic", "package.json"),
            ("jest-basic", "versions/30.0.0/package.json"),
            ("vitest-basic", "package.json"),
            ("vitest-basic", "versions/4.0.0/package.json"),
        ]
    ]
    try:
        for manifest in manifests:
            container = client.containers.create(
                image,
                [
                    "npm",
                    "install",
                    "--package-lock-only",
                    "--ignore-scripts",
                    "--no-audit",
                    "--no-fund",
                ],
                working_dir="/tmp",
                network_mode="bridge",
            )
            try:
                stream = io.BytesIO()
                with tarfile.open(fileobj=stream, mode="w") as tar:
                    body = manifest.read_bytes()
                    entry = tarfile.TarInfo("package.json")
                    entry.size = len(body)
                    tar.addfile(entry, io.BytesIO(body))
                container.put_archive("/tmp", stream.getvalue())
                container.start()
                status = container.wait(timeout=180)
                assert status["StatusCode"] == 0, container.logs().decode()
                chunks, _ = container.get_archive("/tmp/package-lock.json")
                with tarfile.open(fileobj=io.BytesIO(b"".join(chunks))) as tar:
                    raw = tar.extractfile("package-lock.json").read()
                manifest.with_name("package-lock.json").write_bytes(raw)
                print(f"{manifest.relative_to(ROOT)}: locked in {image}")
            finally:
                container.remove(force=True)
    finally:
        client.close()


if __name__ == "__main__":
    main()
