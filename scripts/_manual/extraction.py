"""Conservative manual extraction gate: runtime eligibility remains pending."""

import json
import re
from dataclasses import asdict
from pathlib import PurePosixPath

from robo_minerador.environments.catalog import load_catalog, npm_satisfies
from robo_minerador.environments.plan import persist_plan, plan_environment
from robo_minerador.extraction.client import AnalysisClient
from robo_minerador.extraction.service import ExtractionService, load_extraction
from robo_minerador.identity import stable_digest
from robo_minerador.screening.commands import plan_commands
from robo_minerador.screening.service import load_report
from robo_minerador.screening.snapshots import load_snapshot

from .analyzer import AnalyzerSession, DockerTransport


def static_inputs(snapshot, artifacts, report, config, root):
    contents = snapshot.contents(artifacts)
    commands = plan_commands(contents)
    if stable_digest(asdict(commands)) != stable_digest(report["commands"]):
        raise ValueError("command plan differs from snapshot")
    plan = plan_environment(contents, commands, load_catalog(root), config.execution)
    if plan.plan is None:
        return None, plan.reason_code
    if len(commands.frameworks) != 1 or commands.frameworks[0] not in {"jest", "vitest"}:
        return None, "framework_scope_unresolved"
    if commands.workspaces:
        return None, "workspace_scope_unresolved"
    configs = sorted(p for p in contents if PurePosixPath(p).name == "tsconfig.json")
    if not configs:
        return None, "tsconfig_unavailable"
    # A deliberately narrow supported static subset. JSONC, extends and project
    # references require a future resolver rather than an invented default.
    for path in configs:
        try:
            model = json.loads(contents[path])
            if not isinstance(model, dict) or "extends" in model or "references" in model:
                return None, "tsconfig_scope_unresolved"
        except (ValueError, UnicodeError):
            return None, "tsconfig_scope_unresolved"
    tests = sorted(
        p for p in contents if p.endswith((".test.ts", ".spec.ts", ".test.tsx", ".spec.tsx"))
    )
    if not tests:
        return None, "test_sources_unavailable"
    if snapshot.context_status != "complete":
        return None, "unsupported_context"
    return (plan.plan, commands.frameworks[0], tuple(configs)), "static_inputs_verified"


def extract_one(value, config, root, state, artifacts, entry, session):
    report = load_report(artifacts, entry["report_ref"])
    if report != entry["report"]:
        raise ValueError("screening report changed")
    snapshot = load_snapshot(artifacts, report["snapshot_manifest"])
    if snapshot.repository.model_dump(mode="json") != report["repository"]:
        raise ValueError("snapshot identity mismatch")
    inputs, reason = static_inputs(snapshot, artifacts, report, config, root)
    if inputs is None:
        return {"status": "not_evaluated", "reason_code": reason}
    plan, framework, tsconfigs = inputs
    plan_ref = persist_plan(plan, artifacts)
    analyzer_root = session.transfer(snapshot, artifacts, value["run_id"])
    with AnalysisClient(
        "http://analyzer:3000", timeout=60, transport=DockerTransport(session.container)
    ) as client:
        ref = ExtractionService(client, state, artifacts).extract(
            run_id=value["run_id"],
            snapshot=snapshot,
            screening_digest=entry["report_ref"],
            plan=plan,
            plan_artifact=plan_ref,
            analyzer_root=analyzer_root,
            framework=framework,
            tsconfig_paths=tsconfigs,
        )
    manifest = load_extraction(artifacts, ref.digest)
    metadata_ref = artifacts.put(json.dumps(session.metadata, sort_keys=True).encode())
    state.register_artifact(metadata_ref)
    return project_extraction(
        manifest, snapshot, artifacts, ref.digest, plan, plan_ref, session.metadata, metadata_ref
    )


def project_extraction(
    manifest, snapshot, artifacts, digest, plan, plan_ref, metadata, metadata_ref
):
    """One projection for fresh output and fully offline verification of that output."""
    candidates = []
    for candidate in manifest.candidates:
        model = candidate.model_dump(mode="json")
        model["test_context"] = [
            {
                "path": path,
                "code": snapshot.read(path, artifacts).decode("utf-8"),
                "snapshot_ref": snapshot.manifest_sha256,
            }
            for path in candidate.test_files
        ]
        candidates.append(model)
    return {
        "status": "candidate" if manifest.analysis_artifact else "not_evaluated",
        "reason_code": "static_candidates_only"
        if manifest.analysis_artifact
        else "analysis_unavailable",
        "extraction_ref": digest,
        "extraction_manifest": manifest.model_dump(mode="json"),
        "snapshot_ref": snapshot.manifest_sha256,
        "environment_plan": {"status": "planned", "artifact": plan_ref, "plan": asdict(plan)},
        "analyzer": metadata,
        "analyzer_ref": metadata_ref,
        "candidates": candidates,
    }


def screening_gate(report, digest):
    assessments = {a["criterion_id"]: a for a in report["assessments"]}
    required = [assessments.get(f"H-R{n:02}") for n in range(1, 5)]
    rejected = [a for a in required if a and a["status"] == "rejected"]
    pending = [a for a in required if not a or a["status"] != "approved"]
    return {
        "repository": report["repository"],
        "screening_ref": digest,
        "status": "skipped" if rejected else "not_evaluated",
        "reason_code": "screening_rejected" if rejected else "screening_unresolved",
        "blocking_assessments": rejected or pending,
        "accepted": False,
        "H-R05": "not_evaluated",
        "coverage": None,
        "runtime_confirmed": False,
        "candidates": [],
    }, not pending


def verify_metadata(metadata, root):
    package = json.loads((root / "tools/ts-analyzer/package.json").read_bytes())
    health = metadata["health"]
    if (
        set(metadata)
        != {"health", "source_verified", "image_id", "container_id", "workspace_volume"}
        or metadata["source_verified"] is not True
        or health["protocol_version"] != "1.0"
        or health["library_version"] != package["version"]
        or health["typescript_version"] != package["dependencies"]["typescript"]
        or health["ts_morph_version"] != package["dependencies"]["ts-morph"]
        or not npm_satisfies(health["node_version"], package["engines"]["node"])
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", metadata["image_id"])
        or not re.fullmatch(r"[0-9a-f]{64}", metadata["container_id"])
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", metadata["workspace_volume"])
    ):
        raise ValueError("invalid frozen analyzer metadata")


def verify_extraction_results(value, config, root, state, artifacts, *, allow_partial=False):
    """Verify every saved result against original CAS, service evidence and inputs.

    Analyzer metadata is historical evidence, not a claim about a running daemon.
    This function never connects Docker, transfers files, or makes an HTTP call.
    """
    results = value["extraction"]
    if len(results) > len(value["screening"]) or (
        not allow_partial and len(results) != len(value["screening"])
    ):
        raise ValueError("incomplete extraction checkpoint")
    for screening, result in zip(value["screening"][: len(results)], results, strict=True):
        report = load_report(artifacts, screening["report_ref"])
        if report != screening["report"]:
            raise ValueError("screening evidence mismatch")
        expected, approved = screening_gate(report, screening["report_ref"])
        if approved:
            snapshot = load_snapshot(artifacts, report["snapshot_manifest"])
            if snapshot.repository.model_dump(mode="json") != report["repository"]:
                raise ValueError("snapshot identity mismatch")
            inputs, reason = static_inputs(snapshot, artifacts, report, config, root)
            if inputs is None:
                expected["reason_code"] = reason
            else:
                plan, framework, tsconfigs = inputs
                manifest = load_extraction(artifacts, result["extraction_ref"])
                plan_ref = result["environment_plan"]["artifact"]
                if (
                    json.loads(artifacts.get(plan_ref)) != json.loads(json.dumps(asdict(plan)))
                    or manifest.plan_artifact != plan_ref
                    or manifest.screening_artifact != screening["report_ref"]
                    or manifest.snapshot_manifest != snapshot.manifest_sha256
                    or manifest.repository_id != snapshot.repository.state_id
                ):
                    raise ValueError("frozen extraction inputs differ")
                request_inputs = {
                    "snapshot": snapshot.manifest_sha256,
                    "screening": screening["report_ref"],
                    "plan": plan_ref,
                    "root": (
                        f"/workspace/snapshots/manual-stages/{value['run_id']}/"
                        f"{snapshot.tree_sha256}"
                    ),
                    "framework": framework,
                    "tsconfig_paths": list(tsconfigs),
                    "framework_version": None,
                    "instrumentation": False,
                    "prepared": None,
                    "prepared_identity": None,
                    "external_dependency_roots": [],
                }
                if json.loads(artifacts.get(manifest.inputs_artifact)) != request_inputs:
                    raise ValueError("extraction request differs from immutable manual inputs")
                owned = False
                for event in state.events(value["run_id"]):
                    if event.kind != "task_completed" or not event.task_id:
                        continue
                    task = state.get_task(event.task_id)
                    if (
                        task.stage == "extraction"
                        and task.entity_id == manifest.repository_id
                        and result["extraction_ref"] in state.task_evidence(task.id)
                    ):
                        owned = True
                if not owned:
                    raise ValueError("extraction does not belong to this run")
                metadata = json.loads(artifacts.get(result["analyzer_ref"]))
                verify_metadata(metadata, root)
                expected.update(
                    project_extraction(
                        manifest,
                        snapshot,
                        artifacts,
                        result["extraction_ref"],
                        plan,
                        plan_ref,
                        metadata,
                        result["analyzer_ref"],
                    )
                )
        if stable_digest(expected) != stable_digest(result):
            raise ValueError("manual extraction projection differs from frozen evidence")
    return results


def extract_repositories(value, config, root, state, artifacts, *, build_analyzer=False):
    results = []
    # Preflight every immutable input before connecting Docker or transferring any tree.
    for entry in value["screening"]:
        report = load_report(artifacts, entry["report_ref"])
        if report != entry["report"]:
            raise ValueError("screening evidence mismatch")
        if report["snapshot_manifest"]:
            original = load_snapshot(artifacts, report["snapshot_manifest"])
            if original.repository.model_dump(mode="json") != report["repository"]:
                raise ValueError("snapshot identity mismatch")
    if value.get("extraction"):
        return verify_extraction_results(value, config, root, state, artifacts)
    value["extraction"] = results  # Keep completed entries available for partial-error publication.
    from contextlib import ExitStack

    with ExitStack() as stack:
        session = None
        for entry in value["screening"]:
            report = load_report(artifacts, entry["report_ref"])
            if report != entry["report"]:
                raise ValueError("screening evidence mismatch")
            result, approved = screening_gate(report, entry["report_ref"])
            if approved:
                snapshot = load_snapshot(artifacts, report["snapshot_manifest"])
                inputs, reason = static_inputs(snapshot, artifacts, report, config, root)
                if inputs is None:
                    result["reason_code"] = reason
                else:
                    if session is None:
                        session = stack.enter_context(AnalyzerSession(root, build=build_analyzer))
                    result.update(
                        extract_one(value, config, root, state, artifacts, entry, session)
                    )
            results.append(result)
    return results
