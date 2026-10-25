"""T11 engineering evidence: real exact-patch baseline/traced pairs, never dataset suites."""

import hashlib
import io
import json
import os
import tarfile
import time
from copy import copy
from dataclasses import asdict, replace
from pathlib import Path

import docker
import pytest

from robo_minerador.config import ExecutionConfig
from robo_minerador.environments.catalog import load_catalog
from robo_minerador.environments.plan import Command, plan_environment
from robo_minerador.execution.docker import DockerExecutor, ProjectExecutionFailure
from robo_minerador.execution.results import load_report
from robo_minerador.extraction.instrumentation import (
    InstrumentationRequest,
    check_instrumentation,
)
from robo_minerador.screening.commands import plan_commands
from robo_minerador.state.artifacts import ArtifactStore
from robo_minerador.validation.cases import (
    TraceEvent,
    correlate_cases,
    valid_case_ids,
    validate_cases,
)

ROOT = Path(__file__).resolve().parents[2]
VERSIONS = [("jest", "29.7.0"), ("jest", "30.0.0"), ("vitest", "3.2.4"), ("vitest", "4.0.0")]


def archive(files):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as tar:
        for name, body in files.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(body)
            entry.uid = entry.gid = 1000
            entry.mode = 0o644
            tar.addfile(entry, io.BytesIO(body))
    return stream.getvalue()


def fixture_files(framework, version):
    base = ROOT / "tests/fixtures/projects" / f"{framework}-basic"
    files = {
        p.relative_to(base).as_posix(): p.read_bytes()
        for p in base.rglob("*")
        if p.is_file() and "versions" not in p.relative_to(base).parts
    }
    alternate = base / "versions" / version
    if alternate.exists():
        files.update({p.name: p.read_bytes() for p in alternate.iterdir() if p.is_file()})
    return files


def workspace_file(store, execution, name):
    with tarfile.open(fileobj=io.BytesIO(store.get(execution.workspace_artifact))) as tar:
        return tar.extractfile("workspace/" + name).read()


def overlay_archive(raw, files):
    output = io.BytesIO()
    with (
        tarfile.open(fileobj=io.BytesIO(raw)) as source,
        tarfile.open(fileobj=output, mode="w") as dest,
    ):
        for original_entry in source:
            entry = copy(original_entry)
            entry.pax_headers = dict(entry.pax_headers)
            if entry.name == "workspace":
                continue
            assert entry.name.startswith("workspace/")
            name = entry.name.removeprefix("workspace/")
            body = files.get(name)
            if body is None and entry.isfile():
                body = source.extractfile(entry).read()
            if entry.islnk():
                body = source.extractfile(entry).read()
                entry.type = tarfile.REGTYPE
                entry.linkname = ""
            entry.pax_headers.pop("path", None)
            entry.pax_headers.pop("linkpath", None)
            entry.name = name
            if body is not None:
                entry.size = len(body)
            dest.addfile(entry, io.BytesIO(body) if body is not None else None)
    return output.getvalue()


@pytest.fixture(scope="module")
def analyzer():
    """Use the installed analyzer image, supplement current static build without rebuild."""
    client = docker.from_env()
    image = client.images.get("robo-minerador-analyzer")
    container = client.containers.create(
        image.id, ["node", "dist/src/server.js"], network_mode="none", user="1000:1000"
    )
    files = {}
    for folder, prefix in [
        (ROOT / "tools/ts-analyzer/dist/src", "app/dist/src"),
        (
            ROOT / "tools/ts-analyzer/src/instrumentation/supplements",
            "app/src/instrumentation/supplements",
        ),
        (ROOT / "schemas", "app/schemas"),
    ]:
        for path in folder.rglob("*"):
            if path.is_file():
                files[prefix + "/" + path.relative_to(folder).as_posix()] = path.read_bytes()
    assert container.put_archive("/", archive(files))
    container.start()
    try:
        for _ in range(60):
            result = container.exec_run(
                [
                    "node",
                    "-e",
                    "fetch('http://127.0.0.1:3000/health').then(r=>r.text())"
                    ".then(console.log).catch(()=>process.exit(1))",
                ]
            )
            if result.exit_code == 0:
                assert json.loads(result.output)["typescript_version"] == "5.8.3"
                break
            time.sleep(0.1)
        else:
            pytest.fail(container.logs().decode())
        yield container
    finally:
        container.remove(force=True)
        client.close()


def instrument(analyzer, files, framework, version):
    digest = hashlib.sha256(archive(files)).hexdigest()
    directory = "workspace/snapshots/" + digest
    assert analyzer.put_archive("/", archive({directory + "/" + k: v for k, v in files.items()}))
    request = InstrumentationRequest(
        request_id="t11-" + framework + version,
        snapshot_id="sha256:" + digest,
        root="/" + directory,
        tsconfig_paths=["tsconfig.json"],
        framework=framework,
        framework_version=version,
    )
    script = (
        "fetch('http://127.0.0.1:3000/v1/instrument',{method:'POST',body:process.argv[1]})"
        ".then(async r=>{if(r.status!==200)process.exitCode=1;console.log(await r.text())})"
    )
    result = analyzer.exec_run(["node", "-e", script, request.model_dump_json()])
    assert result.exit_code == 0, result.output.decode()
    return check_instrumentation(json.loads(result.output), request)


def normalize(analyzer, raw, files, manifest=None):
    payload = {
        "raw": json.loads(raw),
        "sources": {k: v.decode() for k, v in files.items() if k.endswith(".ts")},
        "evidence_id": hashlib.sha256(raw).hexdigest(),
    }
    if manifest is not None:
        payload["instrumentation"] = manifest.model_dump()
    assert analyzer.put_archive("/tmp", archive({"normalize.json": json.dumps(payload).encode()}))
    result = analyzer.exec_run(
        [
            "node",
            "-e",
            "process.stdout.write(require('child_process').execFileSync('node',"
            "['/app/dist/src/frameworks/normalize.js'],"
            "{input:require('fs').readFileSync('/tmp/normalize.json'),maxBuffer:16777216}))",
        ]
    )
    assert result.exit_code == 0, result.output.decode()
    return result.output


def configuration(framework, traced):
    if framework == "jest":
        extra = (
            ",testEnvironment:'/opt/robo-reporters/jest-environment.cjs',"
            "setupFiles:['/opt/robo-reporters/jest-setup.cjs']"
            if traced
            else ""
        )
        return (
            "module.exports={...require('/workspace/jest.config.cjs'),rootDir:'/workspace',"
            "testMatch:['**/value.test.ts','**/test/direct.case.ts']" + extra + "};"
        ).encode()
    extra = ",setupFiles:['/opt/robo-reporters/vitest-setup.mjs']" if traced else ""
    return (
        "export default {root:'/workspace',test:{include:['value.test.ts','test/direct.case.ts'],"
        "includeTaskLocation:true,pool:'forks',maxWorkers:1,fileParallelism:false" + extra + "}};"
    ).encode()


@pytest.mark.docker
@pytest.mark.parametrize(("framework", "version"), VERSIONS)
def test_exact_patch_baseline_and_trace(analyzer, tmp_path, framework, version):
    files = fixture_files(framework, version)
    response = instrument(analyzer, files, framework, version)
    manifest = response.manifest
    assert manifest.status == "ready", manifest.limitations
    concurrent = {
        **files,
        "test/concurrent.case.ts": (
            ("import { test } from 'vitest';" if framework == "vitest" else "")
            + "import {magnitude} from '../value';"
            + "test.concurrent('race',async()=>{await Promise.resolve();magnitude(1);});"
        ).encode(),
    }
    gate = instrument(analyzer, concurrent, framework, version)
    assert gate.manifest.status == "not_evaluated"
    assert gate.manifest.files == [] and gate.supplements == {}
    planned = plan_environment(files, plan_commands(files), load_catalog(ROOT), ExecutionConfig())
    assert planned.plan is not None, planned
    plan = replace(
        planned.plan,
        build=None,
        install=Command(("npm", "ci", "--no-audit", "--no-fund"), ".", 180),
    )
    export = Path(os.environ.get("ROBO_T11_EVIDENCE", str(tmp_path))) / f"{framework}-{version}"
    store = ArtifactStore(export / "storefiles")
    client = docker.from_env()
    executor = DockerExecutor(client, store, install_network="bridge")
    extension = "cjs" if framework == "jest" else "mjs"
    reporter = f"{framework}.{extension}"
    supplements = {
        reporter: (ROOT / "tools/ts-analyzer/src/frameworks/reporters" / reporter).read_bytes()
    }
    config_name = "config." + extension
    argv = (
        "node",
        "node_modules/jest/bin/jest.js"
        if framework == "jest"
        else "node_modules/vitest/vitest.mjs",
    )
    argv += ("--runInBand", "--testLocationInResults") if framework == "jest" else ("run",)
    argv += (
        "--config",
        "/opt/robo-reporters/" + config_name,
        "--reporters" if framework == "jest" else "--reporter",
        "/opt/robo-reporters/" + reporter,
    )
    if framework == "vitest":
        # Vite 6.1 writes a bundled config beside its input. Keep the supplement
        # read-only and use an explicitly recorded writable *copy*, never the
        # original project config or a writable root filesystem.
        supplements["launch.cjs"] = (
            b"require('fs').copyFileSync('/opt/robo-reporters/config.mjs',"
            b"'/workspace/.robo-t11-config.mjs');"
            b"const r=require('child_process').spawnSync(process.execPath,"
            b"process.argv.slice(2),{stdio:'inherit'});"
            b"if(r.error)throw r.error;process.exit(r.status??1);"
        )
        argv = tuple(
            "/workspace/.robo-t11-config.mjs" if arg == "/opt/robo-reporters/config.mjs" else arg
            for arg in argv
        )
        argv = ("node", "/opt/robo-reporters/launch.cjs", *argv[1:])
    try:
        prepared = executor.prepare(plan, archive(files))
        baseline = executor.run_fresh(
            prepared.prepared_id,
            argv,
            plan.limits,
            supplements={**supplements, config_name: configuration(framework, False)},
        )
        raw = workspace_file(store, baseline, ".robo-reports/raw.json")
        original_report = load_report(normalize(analyzer, raw, files), raw, store)
        assert len(original_report.cases) == 28
        assert original_report.trace_support.status == "tested"
        if framework == "vitest":
            resolved = json.loads(raw)["configuration"]
            assert resolved["fileParallelism"] is False and resolved["maxWorkers"] == 1
        overlays = {file.path: file.source.encode() for file in manifest.files}
        copied = overlay_archive(executor.prepared_archive(prepared.prepared_id), overlays)
        traced_plan = replace(plan, install=Command(("node", "--version"), ".", 30))
        traced_prepared = executor.prepare(traced_plan, copied)
        traced = executor.run_fresh(
            traced_prepared.prepared_id,
            argv,
            plan.limits,
            supplements={
                **supplements,
                **{k: v.encode() for k, v in response.supplements.items()},
                config_name: configuration(framework, True),
            },
        )
        traced_raw = workspace_file(store, traced, ".robo-reports/raw.json")
        traced_report = load_report(
            normalize(analyzer, traced_raw, files, manifest), traced_raw, store
        )
        trace = workspace_file(store, traced, ".robo-reports/trace.jsonl")
        target = next(t for t in manifest.targets if t.qualified_name == "magnitude")
        validation = validate_cases(
            original_report,
            traced_report,
            manifest,
            target.id,
            baseline,
            traced,
            trace,
            store,
            observable_paths=(".robo-observables.json",),
        )
        assert validation.status == "evaluated", validation
        approved = valid_case_ids(validation)
        titles = [c.title_path[-1] for c in traced_report.cases if c.identity in approved]
        assert sorted(titles) == sorted(
            ["same"] * 6
            + [
                "conventional",
                "alias",
                "reexport",
                "argument evaluation",
                "expected assertion exception",
            ]
            + ["duplicate tuple"] * 3
        ), titles
        associations = {"magnitude": sorted(approved)}
        pairings = {"magnitude": validation.evidence_id}
        for target_name, expected in [
            ("Counter.add", ["resolved method", "dependency mocked"]),
            ("Other.add", ["other class"]),
            ("delayed", ["async return"]),
        ]:
            target_id = next(t.id for t in manifest.targets if t.qualified_name == target_name)
            other = validate_cases(
                original_report,
                traced_report,
                manifest,
                target_id,
                baseline,
                traced,
                trace,
                store,
                observable_paths=(".robo-observables.json",),
            )
            associations[target_name] = sorted(valid_case_ids(other))
            pairings[target_name] = other.evidence_id
            assert sorted(
                c.title_path[-1] for c in traced_report.cases if c.identity in valid_case_ids(other)
            ) == sorted(expected)
        assert workspace_file(store, baseline, ".robo-observables.json") == workspace_file(
            store, traced, ".robo-observables.json"
        )
        events = [json.loads(line) for line in trace.splitlines()]
        assert sum(e["kind"] == "case_collected" for e in events) == 28
        assert sum(case.eligible for case in traced_report.cases) == 24
        unsupported = replace(
            traced_report, support=replace(traced_report.support, status="not_evaluated")
        )
        denied = validate_cases(
            original_report, unsupported, manifest, target.id, baseline, traced, trace, store
        )
        assert denied.status == "not_evaluated" and valid_case_ids(denied) == set()
        with pytest.raises(ValueError, match="provenance"):
            validate_cases(
                original_report,
                traced_report,
                manifest,
                target.id,
                baseline,
                traced,
                trace + b"\n",
                store,
            )
        with pytest.raises(ValueError, match="untrusted observational"):
            validate_cases(
                original_report,
                traced_report,
                manifest,
                target.id,
                baseline,
                replace(traced, storage_verified=False),
                trace,
                store,
            )
        legacy = replace(traced_report, trace_support=None)
        assert (
            valid_case_ids(
                validate_cases(
                    original_report, legacy, manifest, target.id, baseline, traced, trace, store
                )
            )
            == set()
        )
        g1_reproductions = []
        if framework == "vitest":
            serial_config = configuration(framework, True)
            reporter_source = supplements[reporter]
            for variant in ("parallel", "missing-fileParallelism", "missing-maxWorkers", "unknown"):
                variant_reporter = reporter_source
                variant_config = serial_config
                if variant == "parallel":
                    variant_config = serial_config.replace(
                        b"maxWorkers:1,fileParallelism:false", b"maxWorkers:2,fileParallelism:true"
                    )
                    assert variant_config != serial_config
                elif variant == "missing-fileParallelism":
                    variant_reporter = reporter_source.replace(
                        b"fileParallelism: config.fileParallelism ?? null,", b""
                    )
                elif variant == "missing-maxWorkers":
                    variant_reporter = reporter_source.replace(
                        b"maxWorkers: config.maxWorkers ?? null,", b""
                    )
                else:
                    variant_reporter = reporter_source.replace(
                        b"config.fileParallelism ?? null", b"null"
                    ).replace(b"config.maxWorkers ?? null", b"null")
                if variant != "parallel":
                    assert variant_reporter != reporter_source
                execution = executor.run_fresh(
                    traced_prepared.prepared_id,
                    argv,
                    plan.limits,
                    supplements={
                        **supplements,
                        **{k: v.encode() for k, v in response.supplements.items()},
                        reporter: variant_reporter,
                        config_name: variant_config,
                    },
                )
                variant_raw = workspace_file(store, execution, ".robo-reports/raw.json")
                variant_trace = workspace_file(store, execution, ".robo-reports/trace.jsonl")
                variant_report = load_report(
                    normalize(analyzer, variant_raw, files, manifest), variant_raw, store
                )
                assert variant_report.support.status == "tested"  # T10 result scope.
                assert variant_report.trace_support.status == "not_evaluated"
                assert len(variant_report.cases) == 28
                candidate_ids = correlate_cases(
                    variant_report.cases,
                    manifest.callsites,
                    [TraceEvent.model_validate_json(line) for line in variant_trace.splitlines()],
                    target.id,
                )
                assert len(candidate_ids) == 14  # Trace exists; the mode gate must deny it.
                denial = validate_cases(
                    original_report,
                    variant_report,
                    manifest,
                    target.id,
                    baseline,
                    execution,
                    variant_trace,
                    store,
                )
                assert denial.status == "not_evaluated" and valid_case_ids(denial) == set()
                # A lying normalized support flag cannot bypass the independent raw gate.
                forged = replace(variant_report, trace_support=original_report.trace_support)
                independent = validate_cases(
                    original_report,
                    forged,
                    manifest,
                    target.id,
                    baseline,
                    execution,
                    variant_trace,
                    store,
                )
                assert independent.reason == "unsupported_raw_framework_configuration"
                assert valid_case_ids(independent) == set()
                actual = json.loads(variant_raw)["configuration"]
                if variant == "parallel":
                    assert actual["fileParallelism"] is True and actual["maxWorkers"] == 2
                elif variant.startswith("missing-"):
                    assert variant.removeprefix("missing-") not in actual
                else:
                    assert actual["fileParallelism"] is None and actual["maxWorkers"] is None
                g1_reproductions.append(
                    {
                        "variant": variant,
                        "configuration": actual,
                        "raw_artifact": store.put(variant_raw),
                        "normalized_artifact": variant_report.normalized_evidence_id,
                        "trace_artifact": store.put(variant_trace),
                        "execution_artifact": execution.evidence_artifact,
                        "validation_artifact": denial.evidence_id,
                        "independent_validation_artifact": independent.evidence_id,
                        "candidate_count": len(candidate_ids),
                        "approved": [],
                    }
                )
        for name, content in files.items():
            if name.endswith(".ts"):
                assert workspace_file(store, baseline, name) == content
                assert workspace_file(store, traced, name) == overlays.get(name, content)
        export.mkdir(parents=True, exist_ok=True)
        exported = {
            "manifest.json": manifest.model_dump_json().encode(),
            "baseline.raw.json": raw,
            "traced.raw.json": traced_raw,
            "trace.jsonl": trace,
            "baseline.normalized.json": store.get(original_report.normalized_evidence_id),
            "traced.normalized.json": store.get(traced_report.normalized_evidence_id),
            "baseline.execution.json": json.dumps(asdict(baseline)).encode(),
            "traced.execution.json": json.dumps(asdict(traced)).encode(),
            "baseline.prepared.json": json.dumps(asdict(prepared)).encode(),
            "traced.prepared.json": json.dumps(asdict(traced_prepared)).encode(),
            "concurrent-gate.json": gate.model_dump_json().encode(),
            "unsupported-mode.json": store.get(denied.evidence_id),
            "g1-reproductions.json": json.dumps(g1_reproductions).encode(),
            "observables.json": workspace_file(store, baseline, ".robo-observables.json"),
            "pairing.json": store.get(validation.evidence_id),
            "matrix.json": json.dumps(
                {
                    "purpose": "engineering_fixture_not_dataset",
                    "framework": framework,
                    "version": version,
                    "cases": [
                        {
                            "identity": case.identity,
                            "file": case.file,
                            "position": case.declaration_position,
                            "runtime_id": case.runtime_id,
                            "parameter_index": case.parameter_index,
                            "title_path": case.title_path,
                            "status": case.status,
                            "eligible": case.eligible,
                            "expected_failure": case.expected_failure,
                            "targets": [
                                name for name, ids in associations.items() if case.identity in ids
                            ],
                        }
                        for case in traced_report.cases
                    ],
                    "associations": associations,
                    "pairings": pairings,
                    "false_positives": 0,
                    "concurrent": "not_evaluated",
                    "unsupported_configuration": "not_evaluated",
                }
            ).encode(),
        }
        for name, content in exported.items():
            (export / name).write_bytes(content)
        print(
            json.dumps(
                {
                    "framework": framework,
                    "version": version,
                    "cases": len(traced_report.cases),
                    "magnitude_approved": len(approved),
                    "pairing": validation.evidence_id,
                    "evidence": str(export),
                }
            )
        )
    except ProjectExecutionFailure as error:
        pytest.fail(
            store.get(error.result.stderr_artifact).decode()
            + store.get(error.result.stdout_artifact).decode()
        )
    finally:
        executor.close()
        client.close()
