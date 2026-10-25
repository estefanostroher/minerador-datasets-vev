"""T14 extraction checkpoint for T19; no runtime eligibility or coverage execution."""

import hashlib
import json
import tarfile
from dataclasses import asdict, dataclass
from typing import Literal

from pydantic import ConfigDict

from robo_minerador.domain import Assessment
from robo_minerador.environments.plan import EnvironmentPlan
from robo_minerador.execution.docker import PreparedEnvironment
from robo_minerador.extraction.client import (
    AnalysisClient,
    AnalysisError,
    AnalysisRequest,
    AnalysisResponse,
    AnalysisTransportError,
    Documentation,
    Metrics,
    ProtocolError,
    Target,
    TestCall,
)
from robo_minerador.extraction.context import (
    FrozenModel,
    TargetSpan,
    isolate_target,
    load_context,
    load_prepared_context,
    preserve_context,
    preserve_prepared_context,
)
from robo_minerador.extraction.instrumentation import (
    InstrumentationClient,
    InstrumentationRequest,
    verify_overlay,
)
from robo_minerador.identity import stable_digest
from robo_minerador.screening.service import load_report
from robo_minerador.screening.snapshots import Snapshot
from robo_minerador.state.artifacts import ArtifactStore
from robo_minerador.state.store import StateStore


class Evaluation(FrozenModel):
    status: Literal["approved", "not_evaluated"]
    reason: str
    evidence: tuple[str, ...]


class ExtractedTarget(Target):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Association(TestCall):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class DocumentEvidence(Documentation):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class MetricEvidence(Metrics):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Candidate(FrozenModel):
    id: str
    target: ExtractedTarget
    span: TargetSpan | None
    source_status: Evaluation
    associations: tuple[Association, ...]
    documents: tuple[DocumentEvidence, ...]
    metrics: tuple[MetricEvidence, ...]
    test_files: tuple[str, ...]
    context_reference: str


class ExtractionManifest(FrozenModel):
    kind: Literal["extraction"] = "extraction"
    schema_version: Literal["1.0"] = "1.0"
    request_hash: str
    inputs_artifact: str
    repository_id: str
    snapshot_manifest: str
    screening_artifact: str
    plan_artifact: str
    context_reference: str
    prepared_context_reference: str | None
    analysis_request_artifact: str
    analysis_artifact: str | None
    instrumentation_artifact: str | None
    candidates: tuple[Candidate, ...]
    context_evaluation: Evaluation
    eligibility: Evaluation
    limitations: tuple[str, ...]
    artifacts: tuple[str, ...]


@dataclass(frozen=True)
class ExtractionReport:
    task_id: str
    digest: str


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()


def _candidates(
    analysis: AnalysisResponse,
    snapshot: Snapshot,
    artifacts: ArtifactStore,
    reference: str,
    evidence: str,
) -> tuple[Candidate, ...]:
    candidates = []
    for target in analysis.targets:
        span = None
        reason = "target_source_unverified"
        try:
            raw = snapshot.read(target.path, artifacts)
            span = isolate_target(raw, target.start_offset, target.end_offset, target.code)
        except (KeyError, ValueError, UnicodeError):
            pass
        else:
            reason = "original_target_bytes_verified"
        associations = tuple(
            Association.model_validate(c.model_dump())
            for c in analysis.test_calls
            if c.symbol_target_id == target.id
        )
        candidates.append(
            Candidate(
                id=stable_digest({"snapshot": snapshot.manifest_sha256, "target": target.id}),
                target=ExtractedTarget.model_validate(target.model_dump()),
                span=span,
                source_status=Evaluation(
                    status="approved" if span is not None else "not_evaluated",
                    reason=reason,
                    evidence=(evidence, reference),
                ),
                associations=associations,
                documents=tuple(
                    DocumentEvidence.model_validate(d.model_dump())
                    for d in analysis.documents
                    if d.symbol_target_id == target.id
                ),
                metrics=tuple(
                    MetricEvidence.model_validate(m.model_dump())
                    for m in analysis.metrics
                    if m.symbol_target_id == target.id
                ),
                test_files=tuple(sorted({c.callsite_path for c in associations})),
                context_reference=reference,
            )
        )
    return tuple(candidates)


def load_extraction(artifacts: ArtifactStore, digest: str) -> ExtractionManifest:
    """Offline evidence verification, retaining the full analyzer response verbatim as data."""
    manifest = ExtractionManifest.model_validate_json(artifacts.get(digest))
    for reference in manifest.artifacts:
        artifacts.get(reference)
    inputs = json.loads(artifacts.get(manifest.inputs_artifact))
    if stable_digest(inputs) != manifest.request_hash or any(
        inputs[key] != value
        for key, value in (
            ("snapshot", manifest.snapshot_manifest),
            ("screening", manifest.screening_artifact),
            ("plan", manifest.plan_artifact),
        )
    ):
        raise ValueError("extraction inputs differ from immutable request")
    context = load_context(artifacts, manifest.context_reference)
    if context.manifest.snapshot_manifest != manifest.snapshot_manifest:
        raise ValueError("extraction context does not match snapshot")
    if manifest.prepared_context_reference:
        prepared = load_prepared_context(artifacts, manifest.prepared_context_reference)
        if prepared.manifest.snapshot_manifest != manifest.snapshot_manifest:
            raise ValueError("prepared context differs from original snapshot")
    if manifest.analysis_artifact:
        from robo_minerador.screening.snapshots import load_snapshot

        analysis = AnalysisResponse.model_validate_json(artifacts.get(manifest.analysis_artifact))
        request = AnalysisRequest.model_validate_json(
            artifacts.get(manifest.analysis_request_artifact)
        )
        if analysis.request_id != request.request_id:
            raise ValueError("extraction analysis correlation mismatch")
        if (
            request.request_id != manifest.request_hash
            or request.root != inputs["root"]
            or request.framework != inputs["framework"]
            or request.tsconfig_paths != inputs["tsconfig_paths"]
            or request.snapshot_id != "sha256:" + context.manifest.tree_sha256
        ):
            raise ValueError("analyzer request differs from extraction inputs")
        expected = _candidates(
            analysis,
            load_snapshot(artifacts, manifest.snapshot_manifest),
            artifacts,
            manifest.context_reference,
            manifest.analysis_artifact,
        )
        if expected != manifest.candidates:
            raise ValueError("extraction candidates differ from analyzer evidence")
    return manifest


class ExtractionService:
    def __init__(
        self,
        analyzer: AnalysisClient,
        state: StateStore,
        artifacts: ArtifactStore,
        *,
        instrumentation: InstrumentationClient | None = None,
    ) -> None:
        self.analyzer, self.state, self.artifacts = analyzer, state, artifacts
        self.instrumentation = instrumentation

    def extract(
        self,
        *,
        run_id: str,
        snapshot: Snapshot,
        screening_digest: str,
        plan: EnvironmentPlan,
        plan_artifact: str,
        analyzer_root: str,
        framework: Literal["jest", "vitest"],
        tsconfig_paths: tuple[str, ...],
        framework_version: str | None = None,
        prepared: PreparedEnvironment | None = None,
        external_dependency_roots: tuple[str, ...] = (),
    ) -> ExtractionReport:
        """Analyze an explicitly supplied analyzer-visible path, never a host bind guess.

        The T19 caller is responsible for sharing the immutable snapshot at that
        path. Every returned target and overlay is verified against our own CAS
        bytes. No claim is made that this HTTP call verifies a remote whole tree.
        """
        self.state.get_run(run_id)
        repo = snapshot.repository
        if not any(
            row["id"] == repo.state_id
            and row["external_id"] == str(repo.repo_id)
            and row["commit_sha"] == repo.commit_sha
            for row in self.state.repositories(run_id)
        ):
            raise ValueError("repository does not belong to run at snapshot commit")
        screening = load_report(self.artifacts, screening_digest)
        if (
            screening["snapshot_manifest"] != snapshot.manifest_sha256
            or screening["repository"] != repo.model_dump()
        ):
            raise ValueError("screening differs from extraction snapshot")
        if json.loads(self.artifacts.get(plan_artifact)) != json.loads(_json(asdict(plan))):
            raise ValueError("environment plan differs from frozen artifact")
        contents = snapshot.contents(self.artifacts)
        if plan.snapshot_hash != stable_digest(
            {p: hashlib.sha256(b).hexdigest() for p, b in contents.items()}
        ):
            raise ValueError("environment plan differs from original snapshot")
        if plan.command_plan_hash != stable_digest(screening["commands"]):
            raise ValueError("environment plan differs from screening commands")
        if self.instrumentation is not None and framework_version is None:
            raise ValueError("instrumentation requires a frozen framework version")
        request_inputs = {
            "snapshot": snapshot.manifest_sha256,
            "screening": screening_digest,
            "plan": plan_artifact,
            "root": analyzer_root,
            "framework": framework,
            "tsconfig_paths": tsconfig_paths,
            "framework_version": framework_version,
            "instrumentation": self.instrumentation is not None,
            "prepared": prepared.evidence_artifact if prepared else None,
            "prepared_identity": stable_digest(asdict(prepared)) if prepared else None,
            "external_dependency_roots": external_dependency_roots,
        }
        request_hash = stable_digest(request_inputs)
        for event in self.state.events(run_id):
            if event.kind != "task_completed" or not event.task_id:
                continue
            task = self.state.get_task(event.task_id)
            if task.stage != "extraction" or task.entity_id != repo.state_id:
                continue
            for digest in self.state.task_evidence(task.id):
                raw = self.artifacts.get(digest)
                try:
                    value = json.loads(raw)
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(value, dict) or value.get("kind") != "extraction":
                    continue
                manifest = load_extraction(self.artifacts, digest)
                if manifest.request_hash != request_hash:
                    raise ValueError("extraction inputs are immutable; create a new run")
                return ExtractionReport(task.id, digest)
        reference = preserve_context(snapshot, self.artifacts)
        request = AnalysisRequest(
            request_id=request_hash,
            snapshot_id="sha256:" + snapshot.tree_sha256,
            root=analyzer_root,
            tsconfig_paths=list(tsconfig_paths),
            framework=framework,
        )
        task_id = self.state.start_task(run_id, repo.state_id, "extraction")
        try:
            references = {
                screening_digest,
                plan_artifact,
                reference.digest,
                snapshot.manifest_sha256,
            }
            references.update(f.artifact for f in reference.manifest.files if f.artifact)

            def save(value: object) -> str:
                digest = self.artifacts.put(_json(value))
                references.add(digest)
                return digest

            inputs_artifact = save(request_inputs)
            derivation = None
            context_reason = "prepared_context_unavailable"
            if prepared:
                try:
                    derivation = preserve_prepared_context(
                        snapshot,
                        plan,
                        prepared,
                        self.artifacts,
                        external_dependency_roots=external_dependency_roots,
                    )
                except (OSError, ValueError, KeyError, tarfile.TarError, RecursionError) as error:
                    context_reason = "prepared_context_unverified"
                    save(
                        {
                            "kind": "prepared_context_failure",
                            "type": type(error).__name__,
                            "prepared_evidence": prepared.evidence_artifact,
                            "prepared_archive": prepared.workspace_artifact,
                            "plan_artifact": prepared.plan_artifact,
                        }
                    )
                else:
                    context_reason = derivation.manifest.reason
                    references.update(
                        (
                            derivation.digest,
                            derivation.manifest.prepared_evidence,
                            derivation.manifest.prepared_archive,
                            *derivation.manifest.command_evidence,
                        )
                    )
            request_artifact = save(request.model_dump(mode="json"))
            analysis_artifact, instrument_artifact = None, None
            candidates: tuple[Candidate, ...] = ()
            limitations = ["runtime_eligibility_pending_T13_T15_T14P3", context_reason]
            try:
                analysis = self.analyzer.analyze(request)
            except (AnalysisError, AnalysisTransportError, ProtocolError) as error:
                save(
                    {
                        "kind": "analysis_failure",
                        "type": type(error).__name__,
                        "errors": [e.model_dump() for e in error.errors]
                        if isinstance(error, AnalysisError)
                        else [],
                    }
                )
                limitations.append("analysis_unavailable")
            else:
                analysis_artifact = save(analysis.model_dump(mode="json"))
                candidates = _candidates(
                    analysis, snapshot, self.artifacts, reference.digest, analysis_artifact
                )
                limitations.extend(issue.code for issue in analysis.limitations)
            if self.instrumentation is not None:
                assert framework_version is not None
                instrument_request = InstrumentationRequest(
                    **{**request.model_dump(), "operation": "instrument_snapshot"},
                    framework_version=framework_version,
                )
                save(instrument_request.model_dump(mode="json"))
                try:
                    instrument = self.instrumentation.instrument(instrument_request)
                    instrument_artifact = save(instrument.model_dump(mode="json"))
                    for file in instrument.manifest.files:
                        verify_overlay(snapshot.read(file.path, self.artifacts), file)
                    limitations.extend(issue.code for issue in instrument.manifest.limitations)
                except (
                    AnalysisError,
                    AnalysisTransportError,
                    ProtocolError,
                    ValueError,
                    KeyError,
                ) as error:
                    save({"kind": "instrumentation_failure", "type": type(error).__name__})
                    limitations.append("instrumentation_unverified")
            manifest = ExtractionManifest(
                request_hash=request_hash,
                inputs_artifact=inputs_artifact,
                repository_id=repo.state_id,
                snapshot_manifest=snapshot.manifest_sha256,
                screening_artifact=screening_digest,
                plan_artifact=plan_artifact,
                context_reference=reference.digest,
                prepared_context_reference=derivation.digest if derivation else None,
                analysis_request_artifact=request_artifact,
                analysis_artifact=analysis_artifact,
                instrumentation_artifact=instrument_artifact,
                candidates=candidates,
                context_evaluation=Evaluation(
                    status="not_evaluated",
                    reason=context_reason,
                    evidence=(reference.digest, derivation.digest)
                    if derivation
                    else (reference.digest,),
                ),
                eligibility=Evaluation(
                    status="not_evaluated",
                    reason="runtime_eligibility_pending_T13_T15_T14P3",
                    evidence=tuple(sorted(references)),
                ),
                limitations=tuple(dict.fromkeys(limitations)),
                artifacts=tuple(sorted(references)),
            )
            digest = self.artifacts.put(manifest.model_dump_json().encode())
            with self.state.transaction():
                for artifact in (*references, digest):
                    self.state.register_artifact(artifact)
                self.state.append_assessment(
                    Assessment(
                        repo.state_id,
                        "H-D04",
                        "not_evaluated",
                        manifest.context_evaluation.reason,
                        (digest, reference.digest),
                    ),
                    task_id=task_id,
                    rule_version="T14-extraction-v1",
                    stage="extraction",
                    explanation="Reference preserved; reproduction and eligibility pending.",
                )
                self.state.finish_task(
                    task_id,
                    "completed",
                    evidence_ids=tuple(sorted(references | {digest})),
                    explanation="extraction_checkpoint_complete_eligibility_pending",
                )
            return ExtractionReport(task_id, digest)
        except (OSError, RuntimeError, ValueError):
            self.state.mark_interrupted(task_id, explanation="extraction_interrupted")
            raise
