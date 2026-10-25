"""Export small Docker audit objects and normalized static reports for fixture review."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

from robo_minerador.state.artifacts import ArtifactStore

ROOT = Path(__file__).resolve().parents[1]


def main():
    destination = Path(
        os.environ.get("ROBO_FRAMEWORK_EVIDENCE", str(ROOT / "results/artifacts/frameworks"))
    )
    for folder in sorted(destination.iterdir()):
        if not folder.is_dir():
            continue
        framework = folder.name.split("-")[0]
        store = ArtifactStore(folder / "storefiles")
        raw = (folder / "raw.json").read_bytes()
        source = ROOT / f"tests/fixtures/projects/{framework}-basic/value.test.ts"
        normalized = subprocess.run(
            ["node", str(ROOT / "tools/ts-analyzer/dist/src/frameworks/normalize.js")],
            input=json.dumps(
                {
                    "raw": json.loads(raw),
                    "sources": {"value.test.ts": source.read_bytes().decode("utf-8")},
                    "evidence_id": hashlib.sha256(raw).hexdigest(),
                }
            ).encode(),
            capture_output=True,
            check=True,
        ).stdout
        (folder / "normalized.json").write_bytes(normalized)
        store.put(normalized)
        omitted = {}
        for filename in ("execution.json", "failure.execution.json", "prepared.json"):
            data = json.loads((folder / filename).read_bytes())
            for key in ("workspace_artifact", "input_archive_artifact"):
                if key in data and not (folder / "storefiles" / data[key]).exists():
                    omitted[f"{filename}:{key}"] = data[key]
            for ordinal, command in enumerate(data.get("commands", []), 1):
                omitted[f"{filename}:command-{ordinal}:workspace_artifact"] = command[
                    "workspace_artifact"
                ]
        (folder / "retention.json").write_text(
            json.dumps(
                {
                    "policy": "small-audit-export-v1",
                    "store": "storefiles/<sha256>",
                    "omitted_large_ephemeral_objects": omitted,
                    "reason": "Workspaces stay in execution storage; fixtures retain locks.",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"{folder.name}: raw={store.put(raw)} normalized={store.put(normalized)}")


if __name__ == "__main__":
    main()
