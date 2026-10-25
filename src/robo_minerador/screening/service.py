"""Persist preliminary screening decisions and replay their immutable evidence."""

import base64
import json
from dataclasses import asdict, dataclass
from typing import Any, cast

from robo_minerador.config import ExecutionConfig, MiningConfig
from robo_minerador.discovery.github import Envelope, GitHubClient, GitHubError
from robo_minerador.domain import Assessment, CriterionStatus
from robo_minerador.identity import stable_digest
from robo_minerador.screening.commands import CommandPlan, plan_commands
from robo_minerador.screening.licenses import LicenseAssessment, assess_license
from robo_minerador.screening.snapshots import (
    Repository,
    Snapshot,
    SnapshotError,
    capture_snapshot,
    load_snapshot,
)
from robo_minerador.state.artifacts import ArtifactStore
from robo_minerador.state.store import StateStore, TaskOutcome


@dataclass(frozen=True)
class ScreeningReport:
    task_id: str
    digest: str


def load_report(artifacts: ArtifactStore, digest: str) -> dict[str, Any]:
    model = json.loads(artifacts.get(digest))
    if model.get("kind") != "screening" or model.get("schema_version") != "1.0":
        raise ValueError("unsupported screening report")
    for reference in model["artifacts"]:
        artifacts.get(reference)
    if model["snapshot_manifest"]:
        load_snapshot(artifacts, model["snapshot_manifest"])
    return cast(dict[str, Any], model)


class ScreeningService:
    def __init__(self, github: GitHubClient, state: StateStore, artifacts: ArtifactStore) -> None:
        self.github, self.state, self.artifacts = github, state, artifacts

    def _identity(self, run_id: str, repository: Repository) -> None:
        rows = self.state.repositories(run_id)
        if not any(
            row["id"] == repository.state_id
            and row["external_id"] == str(repository.repo_id)
            and row["full_name"] == repository.full_name
            and row["commit_sha"] == repository.commit_sha
            for row in rows
        ):
            raise ValueError("repository identity differs from discovery manifest/state")

    def _validate_execution(self, run_id: str, execution: ExecutionConfig) -> None:
        run = self.state.get_run(run_id)
        if run.config_json is None:
            return
        model = json.loads(run.config_json)
        if stable_digest(model) != run.config_hash:
            raise ValueError("run configuration hash mismatch")
        frozen = MiningConfig.model_validate(model)
        if execution != frozen.execution:
            raise ValueError("run execution configuration is immutable; create a new run")

    def _replay(
        self, run_id: str, repository: Repository, request_hash: str
    ) -> ScreeningReport | None:
        for event in self.state.events(run_id):
            if (
                event.kind not in {"task_completed", "task_rejected", "task_unsupported"}
                or not event.task_id
            ):
                continue
            task = self.state.get_task(event.task_id)
            if task.stage != "screening" or task.entity_id != repository.state_id:
                continue
            for digest in cast(list[str], event.payload.get("evidence_ids", [])):
                model = load_report(self.artifacts, str(digest))
                if model["request_hash"] != request_hash:
                    raise ValueError("screening inputs are immutable; create a new run")
                return ScreeningReport(task.id, str(digest))
        return None

    def screen(
        self,
        run_id: str,
        repository: Repository | dict[str, Any],
        execution: ExecutionConfig,
    ) -> ScreeningReport:
        """Screen with the frozen execution parameters, validating before effects/replay.

        Discovery runs store MiningConfig JSON and its stable_digest, as computed
        from model_dump(mode="json"). Both the hash and execution must match.
        Legacy StateStore runs with config_json=None instead freeze screening
        inputs in the first report; replay still checks their request_hash.
        """
        repository = Repository.model_validate(repository)
        self._validate_execution(run_id, execution)
        self._identity(run_id, repository)
        request_hash = stable_digest(
            {
                "repository": repository.model_dump(mode="json"),
                "execution": execution.model_dump(mode="json"),
            }
        )
        replay = self._replay(run_id, repository, request_hash)
        if replay:
            return replay
        task_id = self.state.start_task(run_id, repository.state_id, "screening")
        session = _Screening(self, repository, task_id, request_hash)
        previous = self.github.on_response

        def observe(envelope: Envelope) -> None:
            session.save(envelope)
            if previous:
                previous(envelope)

        self.github.on_response = observe
        try:
            return session.screen(execution.workspace_limit_mb * 1024 * 1024)
        except (OSError, RuntimeError):
            self.state.mark_interrupted(task_id, explanation="screening_infrastructure_interrupted")
            raise
        finally:
            self.github.on_response = previous


class _Screening:
    def __init__(
        self, service: ScreeningService, repository: Repository, task_id: str, request_hash: str
    ) -> None:
        self.github, self.state, self.artifacts = service.github, service.state, service.artifacts
        self.repository, self.task_id, self.request_hash = repository, task_id, request_hash
        self.references: set[str] = set()
        self.snapshot: Snapshot | None = None
        self.archive: str | None = None
        self.snapshot_reason = "snapshot_unverifiable"
        self.snapshot_status: CriterionStatus = "not_evaluated"

    def register(self, digest: str) -> str:
        self.state.register_artifact(digest)
        self.references.add(digest)
        return digest

    def save(self, value: object) -> str:
        raw = json.dumps(
            value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
        ).encode()
        return self.register(self.artifacts.put(raw))

    def recover_snapshot(self, max_bytes: int) -> None:
        repo = self.repository
        try:
            response = self.github.get_archive(repo.full_name, repo.commit_sha, max_bytes=max_bytes)
            self.archive = self.register(
                self.artifacts.put(base64.b64decode(response.envelope["body_base64"]))
            )
            self.snapshot = capture_snapshot(repo, response, self.artifacts, max_bytes=max_bytes)
        except GitHubError as error:
            if error.envelope["error"] == "http_permanent" and error.envelope["status"] in (
                403,
                404,
                410,
                451,
            ):
                self.snapshot_status, self.snapshot_reason = "rejected", "snapshot_inaccessible"
            else:
                self.snapshot_reason = "snapshot_download_unverifiable"
        except SnapshotError:
            self.snapshot_reason = "snapshot_invalid_or_unsupported"
        else:
            self.register(self.snapshot.manifest_sha256)
            for entry in self.snapshot.files:
                if entry.artifact:
                    self.register(entry.artifact)
            self.snapshot_status, self.snapshot_reason = "approved", "snapshot_recovered_at_commit"

    def license(self, contents: dict[str, bytes]) -> LicenseAssessment:
        if self.snapshot is None:
            return LicenseAssessment("not_evaluated", "snapshot_unavailable")
        try:
            response = self.github.get_license(
                self.repository.full_name, self.repository.commit_sha
            )
        except GitHubError as error:
            return assess_license(
                contents,
                self.repository.commit_sha,
                None,
                api_missing=error.envelope["status"] == 404,
            )
        return assess_license(contents, self.repository.commit_sha, response)

    def screen(self, max_bytes: int) -> ScreeningReport:
        self.recover_snapshot(max_bytes)
        contents = self.snapshot.contents(self.artifacts) if self.snapshot else {}
        license_result = self.license(contents)
        commands = plan_commands(contents) if self.snapshot else None
        decisions = self.decisions(license_result, commands)
        model = {
            "kind": "screening",
            "schema_version": "1.0",
            "request_hash": self.request_hash,
            "repository": self.repository.model_dump(mode="json"),
            "snapshot_manifest": self.snapshot.manifest_sha256 if self.snapshot else None,
            "archive_sha256": self.archive,
            "license": asdict(license_result),
            "commands": asdict(commands) if commands else None,
            "readme": "present"
            if any(p.lower().split("/")[-1].startswith("readme") for p in contents)
            else "absent"
            if self.snapshot
            else "not_evaluated",
            "execution": "not_evaluated",
            "eligible": False,
            "assessments": [
                {"criterion_id": criterion, "status": status, "reason_code": reason}
                for criterion, status, reason in decisions
            ],
            "artifacts": sorted(self.references),
        }
        report_digest = self.save(model)
        outcome: TaskOutcome = (
            "rejected" if any(s == "rejected" for _, s, _ in decisions) else "completed"
        )
        with self.state.transaction():
            for criterion, status, reason in decisions:
                self.state.append_assessment(
                    Assessment(
                        self.repository.state_id, criterion, status, reason, (report_digest,)
                    ),
                    task_id=self.task_id,
                    rule_version="T06-v1",
                    stage="screening",
                    explanation=reason,
                )
            self.state.finish_task(
                self.task_id,
                outcome,
                evidence_ids=(report_digest,),
                explanation="static_screening_complete",
            )
        return ScreeningReport(self.task_id, report_digest)

    def decisions(
        self, license_result: LicenseAssessment, commands: CommandPlan | None
    ) -> list[tuple[str, CriterionStatus, str]]:
        environment_status: CriterionStatus = "not_evaluated"
        framework_status: CriterionStatus = "not_evaluated"
        environment_reason, framework_reason = "snapshot_unavailable", "snapshot_unavailable"
        if commands:
            environment_reason = (
                "environment_plan_pending" if commands.status == "planned" else commands.reason_code
            )
            framework_reason = (
                "framework_execution_pending"
                if commands.status == "planned"
                else commands.reason_code
            )
            if commands.status == "no_test_configuration":
                environment_status = framework_status = "rejected"
            elif commands.status == "framework_unsupported":
                framework_status = "rejected"
            if self.snapshot and self.snapshot.context_status != "complete":
                environment_status, environment_reason = "not_evaluated", "unsupported_context"
                if commands.status == "no_test_configuration":
                    # Missing submodule contents may contain the test configuration.
                    framework_status, framework_reason = "not_evaluated", "unsupported_context"
        return [
            ("H-R01", self.snapshot_status, self.snapshot_reason),
            ("H-R02", license_result.status, license_result.reason_code),
            ("H-R03", environment_status, environment_reason),
            ("H-R04", framework_status, framework_reason),
            ("H-R05", "not_evaluated", "suite_execution_pending"),
        ]
