"""Real Compose service smoke test; fixture transfer uses Docker's archive API."""

import hashlib
import io
import json
import tarfile
from contextlib import closing
from pathlib import Path

import docker
import pytest

from robo_minerador.extraction.client import check_response


@pytest.mark.docker
def test_compose_analyzer_protocol_over_named_volume() -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures/projects/static-symbols"
    entries = sorted(p for p in fixture.rglob("*") if p.is_file())
    digest = hashlib.sha256()
    for entry in entries:
        digest.update(entry.relative_to(fixture).as_posix().encode())
        digest.update(b"\0")
        digest.update(entry.read_bytes())
    snapshot_id = "sha256:" + digest.hexdigest()
    directory = "snapshots/t08-smoke-" + digest.hexdigest()
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as archive:
        for entry in entries:
            content = entry.read_bytes()
            info = tarfile.TarInfo(directory + "/" + entry.relative_to(fixture).as_posix())
            info.size = len(content)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(content))
    with closing(docker.from_env()) as client:
        containers = client.containers.list(
            filters={"label": "com.docker.compose.service=analyzer"}
        )
        assert len(containers) == 1, "Start Compose analyzer before this test"
        analyzer = containers[0]
        analyzer.reload()
        assert analyzer.attrs["State"]["Health"]["Status"] == "healthy"
        workspace = next(m for m in analyzer.attrs["Mounts"] if m["Destination"] == "/workspace")
        assert workspace["Type"] == "volume" and workspace["RW"] is False
        assert not any(m["Destination"].endswith("docker.sock") for m in analyzer.attrs["Mounts"])
        assert not any(e.startswith("GITHUB_TOKEN=") for e in analyzer.attrs["Config"]["Env"])
        helper = client.containers.run(
            analyzer.image.id,
            command=["node", "-e", "setInterval(()=>{},1000)"],
            user="root",
            network_mode="none",
            volumes={workspace["Name"]: {"bind": "/workspace", "mode": "rw"}},
            detach=True,
        )
        try:
            assert helper.put_archive("/workspace", data.getvalue())
        finally:
            helper.remove(force=True)
        health = analyzer.exec_run(
            [
                "node",
                "-e",
                "fetch('http://127.0.0.1:3000/health').then(r=>r.text()).then(console.log)",
            ]
        )
        assert health.exit_code == 0
        versions = json.loads(health.output)
        assert versions["node_version"] == "22.22.3"
        assert versions["typescript_version"] == "5.8.3"
        assert versions["ts_morph_version"] == "26.0.0"
        payload = {
            "protocol_version": "1.0",
            "request_id": "t08-docker-smoke",
            "operation": "analyze_snapshot",
            "snapshot_id": snapshot_id,
            "root": "/workspace/" + directory,
            "tsconfig_paths": ["tsconfig.json"],
            "framework": "vitest",
        }
        script = (
            "fetch('http://127.0.0.1:3000/v1/analyze',"
            "{method:'POST',body:process.argv[1]}).then(async r=>{"
            "if(r.status!==200)process.exitCode=1;console.log(await r.text())})"
        )
        result = analyzer.exec_run(["node", "-e", script, json.dumps(payload)])
        assert result.exit_code == 0, result.output.decode()
        response = check_response(json.loads(result.output), payload["request_id"])
        assert response.status == "ok"
        assert len(response.targets) == 4
        assert len([call for call in response.test_calls if call.link_kind == "direct"]) == 5
        assert len(response.documents) == len(response.metrics) == 4
        same = next(
            target
            for target in response.targets
            if target.path == "src/first.ts" and target.qualified_name == "same"
        )
        document = next(d for d in response.documents if d.symbol_target_id == same.id)
        assert document.status == "approved"
        original = (fixture / "api.md").read_text(encoding="utf-8")
        source = next(s for s in document.sources if s.path == "api.md")
        assert source.text == original[source.start_offset : source.end_offset]
        metric = next(m for m in response.metrics if m.symbol_target_id == same.id)
        assert metric.cyclomatic_complexity == 1
        assert metric.triviality.status == "approved"
        assert all(call.runtime_confirmed is False for call in response.test_calls)
        print(json.dumps({"health": versions, "snapshot_id": snapshot_id, "targets": 4}))
