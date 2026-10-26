"""T13 repository fail-fast validation and CAS-backed, attempt-local recovery.

The selected environment is an input, never resolved here. A caller may supply a
T10 report reader; missing/unsupported collection evidence is inconclusive scope.
Terminal reports are replayed offline. Partial/crashed attempts restart at one.
"""

import hashlib
import io
import json
import shlex
import tarfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pydantic import TypeAdapter

from robo_minerador.domain import Assessment, CriterionStatus
from robo_minerador.environments.plan import EnvironmentPlan, Limits
from robo_minerador.execution.docker import (
    ExecutionResult,
    InfrastructureInterruption,
    PreparedEnvironment,
    ProjectExecutionFailure,
)
from robo_minerador.execution.results import FrameworkReport, SuiteResult, load_report
from robo_minerador.identity import stable_digest
from robo_minerador.screening.commands import CommandPlan
from robo_minerador.state.artifacts import ArtifactStore
from robo_minerador.state.store import StateStore, TaskOutcome


def run_three(execute: Callable[[int], SuiteResult]) -> tuple[SuiteResult, ...]:
    results = []
    for ordinal in range(1, 4):
        result = execute(ordinal)
        results.append(result)
        if result.outcome != "passed":
            break
    return tuple(results)


class RepositoryExecutor(Protocol):
    # T12 retains a result before cleanup, whose infrastructure exception can
    # otherwise mask an already observed project failure.
    results: list[ExecutionResult]

    def prepare(self, plan: EnvironmentPlan, archive: bytes) -> PreparedEnvironment: ...

    def run_fresh(
        self, prepared_id: str, argv: tuple[str, ...], limits: Limits
    ) -> ExecutionResult: ...

    def dispose(self, prepared_id: str) -> None: ...


@dataclass(frozen=True)
class ValidationResult:
    task_id: str
    outcome: TaskOutcome
    evidence_id: str


def validation_identity(plan: EnvironmentPlan, commands: CommandPlan, archive: bytes) -> str:
    return stable_digest(
        {
            "plan": asdict(plan),
            "commands": asdict(commands),
            "archive": hashlib.sha256(archive).hexdigest(),
        }
    )


def _workspace_files(artifacts: ArtifactStore, execution: ExecutionResult) -> dict[str, bytes]:
    if not execution.workspace_artifact:
        return {}
    with tarfile.open(fileobj=io.BytesIO(artifacts.get(execution.workspace_artifact))) as tar:
        return {
            entry.name.removeprefix("workspace/"): body.read()
            for entry in tar
            if entry.isfile() and (body := tar.extractfile(entry))
        }


def prove_suite_scope(
    plan: EnvironmentPlan,
    commands: CommandPlan,
    execution: ExecutionResult,
    report: FrameworkReport | None,
    artifacts: ArtifactStore,
) -> bool:
    """Single-project Jest/Vitest collection proof, separate from T10's unknown.

    The reporter's raw envelope must be present in this execution's captured tree.
    T06 encompassing command, T07 hash/cwd, actual argv, resolved config, complete
    file/case counts and source hashes must agree. Other adapters stay unevaluated.
    """
    if report is None or not report.normalized_evidence_id:
        return False
    if (
        commands.status != "planned"
        or commands.workspaces
        or commands.frameworks != (report.framework,)
        or len(commands.packages) != 1
        or commands.cwd != "."
        or plan.command_plan_hash != stable_digest(asdict(commands))
        or execution.argv != plan.suite.argv
        or execution.cwd != "/workspace"
        or execution.exit_code != 0
        or execution.reason != "passed"
    ):
        return False
    raw_bytes = artifacts.get(report.evidence_id)
    verified = load_report(artifacts.get(report.normalized_evidence_id), raw_bytes, artifacts)
    if verified != report or report.support.status != "tested":
        return False
    files = _workspace_files(artifacts, execution)
    if files.get(".robo-reports/raw.json") != raw_bytes:
        return False
    raw = json.loads(raw_bytes)
    # process.argv.slice(2) is the original test script flags plus manager flags.
    script = shlex.split(commands.packages[0].scripts.get("test", ""))
    if plan.suite.argv[:3] != ("npm", "test", "--") or script[:1] != [report.framework]:
        return False
    expected_argv = tuple(script[1:]) + plan.suite.argv[3:]
    if report.scope_evidence.argv != expected_argv:
        return False
    paths = (
        _jest_collection(raw, report)
        if report.framework == "jest"
        else _vitest_collection(raw, report)
    )
    if (
        not paths
        or len(set(paths)) != len(paths)
        or set(paths) != set(report.scope_evidence.observed_files)
        or set(paths) != set(raw.get("source_hashes", {}))
        or not report.cases
        or any(case.status in {"failed", "not_executed"} for case in report.cases)
    ):
        return False
    # Skipped/pending/todo and expected failures are part of a complete collected
    # suite. Their per-case eligibility/count belongs to T14, not H-R05.
    return all(
        path in files
        and hashlib.sha256(files[path]).hexdigest() == raw["source_hashes"][path]
        and b".only" not in files[path]
        for path in paths
    )


def _jest_collection(raw: dict[str, Any], report: FrameworkReport) -> list[str]:
    configuration = raw.get("configuration", {})
    projects = configuration.get("projects", [])
    global_config = configuration.get("global", {})
    # Jest 30 serializes its empty selector as a tagged TestPathPatterns object;
    # Jest 29 uses the singular empty string checked below.
    path_patterns = global_config.get("testPathPatterns")
    empty_patterns = path_patterns in (None, []) or (
        report.framework_version == "30.0.0"
        and path_patterns == {"patterns": [], "type": "TestPathPatterns"}
    )
    if (
        len(projects) != 1
        or projects[0].get("rootDir") != "/workspace"
        or any(
            global_config.get(key)
            for key in (
                "onlyChanged",
                "onlyFailures",
                "findRelatedTests",
                "runTestsByPath",
                "testNamePattern",
                "testPathPattern",
                "filter",
                "shard",
                "watch",
                "watchAll",
                "selectProjects",
            )
        )
        or not empty_patterns
    ):
        return []
    results = raw.get("results", {})
    suites = results.get("testResults", [])
    paths = [suite.get("testFilePath", "").removeprefix("/workspace/") for suite in suites]
    if (
        results.get("numTotalTestSuites") != len(paths)
        or results.get("numPassedTestSuites", 0) + results.get("numPendingTestSuites", 0)
        != len(paths)
        or results.get("numFailedTestSuites") != 0
        or results.get("numTotalTests") != len(report.cases)
        # Jest sets aggregate success only AFTER onRunComplete reporters. The
        # final process outcome plus collection counts are authoritative here.
        or results.get("numFailedTests") != 0
    ):
        return []
    return paths


def _vitest_collection(raw: dict[str, Any], report: FrameworkReport) -> list[str]:
    configuration = raw.get("configuration", {})
    # These resolved selectors can narrow collection without appearing in argv.
    # A legacy envelope lacking any field cannot prove their absence. Official
    # include/exclude remain the project's suite definition, not a partial run.
    for key in (
        "shard",
        "changed",
        "related",
        "project",
        "filters",
        "cliExclude",
        "standalone",
        "mergeReports",
    ):
        if key not in configuration:
            return []
        value = configuration[key]
        if key in {"related", "project", "filters", "cliExclude"}:
            if value is not None and value != []:
                return []
        elif key in {"changed", "standalone"}:
            if value is not None and value is not False:
                return []
        elif value is not None:
            return []
    if (
        configuration.get("projectCount") != 1
        or configuration.get("pool") != "forks"
        or configuration.get("testNamePattern") != ""
        or not configuration.get("include")
        or not isinstance(configuration.get("exclude"), list)
        or raw.get("errors") != []
        or raw.get("reason") != "passed"
    ):
        return []
    files = raw.get("files", [])
    if any(file.get("result", {}).get("state") not in {"pass", "skip", "todo"} for file in files):
        return []

    def collected_tests(tasks: list[dict[str, Any]]) -> int:
        return sum(
            collected_tests(task.get("tasks", []))
            if task.get("type") == "suite"
            else int(task.get("type") == "test")
            for task in tasks
        )

    if sum(collected_tests(file.get("tasks", [])) for file in files) != len(report.cases):
        return []
    return [file.get("filepath", "").removeprefix("/workspace/") for file in files]


class RepositoryValidation:
    def __init__(
        self,
        executor: RepositoryExecutor | None,
        state: StateStore,
        artifacts: ArtifactStore,
        *,
        read_report: Callable[[ExecutionResult], FrameworkReport | None] | None = None,
        subsequent_criteria: tuple[str, ...] = (),
    ) -> None:
        self.executor, self.state, self.artifacts = executor, state, artifacts
        self.read_report = read_report
        self.subsequent_criteria = subsequent_criteria

    def _save(self, task_id: str, kind: str, value: dict[str, Any]) -> str:
        digest = self.artifacts.put(json.dumps(value, sort_keys=True, allow_nan=False).encode())
        self.state.register_artifact(digest)
        self.state.record_task_checkpoint(task_id, kind, digest)
        return digest

    def _history(
        self,
        task_id: str,
        request_hash: str,
    ) -> list[tuple[int | None, str, dict[str, Any]]]:
        history = []
        for event in self.state.task_checkpoints(task_id):
            digest = str(event.payload["digest"])
            model = json.loads(self.artifacts.get(digest))
            if model["request_hash"] != request_hash:
                raise ValueError("repository validation inputs are immutable; create a new run")
            for reference in model.get("artifacts", []):
                self.artifacts.get(reference)
            history.append((event.attempt, digest, model))
        return history

    def recover(
        self,
        task_id: str,
        request_hash: str,
        plan: EnvironmentPlan | None = None,
        commands: CommandPlan | None = None,
    ) -> ValidationResult | None:
        """Verify every checkpoint and finish durable terminal facts without execution."""
        history = self._history(task_id, request_hash)
        for _, digest, model in reversed(history):
            if model["kind"] == "validation" and model["outcome"] != "interrupted":
                return ValidationResult(task_id, model["outcome"], digest)
        failed = any(model.get("outcome") == "failed" for _, _, model in history)
        attempt = self.state.get_task(task_id).attempt
        results = [
            model
            for seq, _, model in history
            if seq == attempt and model["kind"] == "result" and model["phase"] == "run"
        ]
        interrupted = any(
            seq == attempt and model.get("outcome") == "interrupted" for seq, _, model in history
        )
        if failed:
            return self._finish(task_id, request_hash, "rejected", history)
        if (
            not interrupted
            and [r["ordinal"] for r in results] == [1, 2, 3]
            and all(r["outcome"] == "passed" for r in results)
        ):
            scopes = [
                model for seq, _, model in history if seq == attempt and model["kind"] == "scope"
            ]
            proven = plan is not None and commands is not None and len(scopes) == 3
            if proven:
                assert plan is not None and commands is not None
                for result, scope in zip(results, scopes, strict=True):
                    if (
                        scope["ordinal"] != result["ordinal"]
                        or not scope["scope_proven"]
                        or not scope.get("report")
                    ):
                        proven = False
                        break
                    report = load_report(
                        self.artifacts.get(scope["report"][1]),
                        self.artifacts.get(scope["report"][0]),
                        self.artifacts,
                    )
                    execution = TypeAdapter(ExecutionResult).validate_json(
                        json.dumps(result["execution"])
                    )
                    if not prove_suite_scope(plan, commands, execution, report, self.artifacts):
                        proven = False
                        break
            outcome: TaskOutcome = "completed" if proven else "unsupported"
            return self._finish(task_id, request_hash, outcome, history)
        return None

    def _finish(
        self,
        task_id: str,
        request: str,
        outcome: TaskOutcome,
        history: list[tuple[int | None, str, dict[str, Any]]],
    ) -> ValidationResult:
        task = self.state.get_task(task_id)
        if task.outcome == "interrupted":
            self.state.start_task(task.run_id, task.entity_id, task.stage)
        refs = tuple(digest for _, digest, _ in history)
        reason = {
            "rejected": "first_execution_failure",
            "completed": "three_full_suite_passes",
            "unsupported": "suite_scope_unproven",
            "interrupted": "infrastructure_interrupted",
        }[outcome]
        status: CriterionStatus = (
            "rejected"
            if outcome == "rejected"
            else "approved"
            if outcome == "completed"
            else "not_evaluated"
        )
        with self.state.transaction():
            digest = self._save(
                task_id,
                "validation",
                {
                    "kind": "validation",
                    "schema_version": "1.0",
                    "request_hash": request,
                    "outcome": outcome,
                    "reason_code": reason,
                    "artifacts": refs,
                    "repository_excluded": outcome == "rejected",
                    "prepared": next(
                        (
                            m.get("prepared")
                            for _, _, m in reversed(history)
                            if m["kind"] == "prepared"
                        ),
                        None,
                    ),
                },
            )
            decisions = [
                ("H-R05", status, reason),
                (
                    "H-R06",
                    "not_evaluated" if outcome == "interrupted" else "approved",
                    "infrastructure_interrupted"
                    if outcome == "interrupted"
                    else "fail_fast_observed",
                ),
            ]
            if outcome != "completed":
                decisions.extend(
                    (criterion, "not_evaluated", "prerequisite_not_met")
                    for criterion in self.subsequent_criteria
                )
            for criterion, decision, code in decisions:
                self.state.append_assessment(
                    Assessment(task.entity_id, criterion, decision, code, (digest,)),
                    task_id=task_id,
                    rule_version="T13-v1",
                    stage=task.stage,
                    explanation=code,
                )
            self.state.finish_task(task_id, outcome, evidence_ids=(digest,), explanation=reason)
        return ValidationResult(task_id, outcome, digest)

    def _execution_refs(self, result: ExecutionResult) -> list[str]:
        references = [
            result.evidence_artifact,
            result.stdout_artifact,
            result.stderr_artifact,
            result.storage_observation_artifact,
            result.container_inspect_artifact,
        ]
        if result.workspace_artifact:
            references.append(result.workspace_artifact)
        references.extend(digest for _, digest in result.supplements)
        for digest in references:
            self.state.register_artifact(digest)
        return references

    def validate(
        self,
        run_id: str,
        repository_id: str,
        plan: EnvironmentPlan,
        commands: CommandPlan,
        archive: bytes,
    ) -> ValidationResult:
        request = validation_identity(plan, commands, archive)
        tasks = [
            event.task_id
            for event in self.state.events(run_id)
            if event.kind == "task_started"
            and event.task_id
            and self.state.get_task(event.task_id).entity_id == repository_id
            and self.state.get_task(event.task_id).stage == "repository_validation"
        ]
        if tasks:
            task_id = tasks[0]
            recovered = self.recover(task_id, request, plan, commands)
            if recovered:
                return recovered
            task = self.state.get_task(task_id)
            if task.outcome == "running":
                self._interrupt(task_id, request, "coordinator_recovery")
            task_id = self.state.start_task(run_id, repository_id, "repository_validation")
        else:
            task_id = self.state.start_task(run_id, repository_id, "repository_validation")
        if self.executor is None:
            raise ValueError("an executor is required for a new consecutive sequence")
        plan_ref = self.artifacts.put(json.dumps(asdict(plan), sort_keys=True).encode())
        command_ref = self.artifacts.put(json.dumps(asdict(commands), sort_keys=True).encode())
        archive_ref = self.artifacts.put(archive)
        for ref in (plan_ref, command_ref, archive_ref):
            self.state.register_artifact(ref)
        self._save(
            task_id,
            "plan",
            {
                "kind": "plan",
                "request_hash": request,
                "artifacts": [plan_ref, command_ref, archive_ref],
            },
        )
        prepared: PreparedEnvironment | None = None
        prepare_started = datetime.now(UTC)
        prepare_cursor = len(self.executor.results)
        self._save(
            task_id,
            "prepare_started",
            {
                "kind": "prepare_started",
                "request_hash": request,
                "started_at": prepare_started.isoformat(),
                "artifacts": [],
            },
        )
        try:
            prepared = self.executor.prepare(plan, archive)
            for result in prepared.commands:
                self._record_execution(task_id, request, plan, result, 0, prepare_started)
            refs = [
                prepared.evidence_artifact,
                prepared.plan_artifact,
                prepared.input_archive_artifact,
                prepared.workspace_artifact,
            ]
            for result in prepared.commands:
                refs.extend(self._execution_refs(result))
            for ref in refs:
                self.state.register_artifact(ref)
            self._save(
                task_id,
                "prepared",
                {
                    "kind": "prepared",
                    "request_hash": request,
                    "artifacts": refs,
                    "prepared": asdict(prepared),
                    "started_at": prepare_started.isoformat(),
                    "finished_at": datetime.now(UTC).isoformat(),
                },
            )

            def execute(ordinal: int) -> SuiteResult:
                assert prepared is not None and self.executor is not None
                started = datetime.now(UTC)
                self._save(
                    task_id,
                    "suite_started",
                    {
                        "kind": "suite_started",
                        "request_hash": request,
                        "ordinal": ordinal,
                        "started_at": started.isoformat(),
                        "artifacts": [],
                    },
                )
                cursor = len(self.executor.results)
                try:
                    execution = self.executor.run_fresh(
                        prepared.prepared_id, plan.suite.argv, plan.limits
                    )
                except ProjectExecutionFailure as error:
                    execution = error.result
                except InfrastructureInterruption as error:
                    factual = None
                    produced_results = self.executor.results[cursor:]
                    for produced in produced_results:
                        factual = self._record_execution(
                            task_id, request, plan, produced, ordinal, started
                        )
                    interrupted = self._record_interruption(
                        task_id,
                        request,
                        plan,
                        error,
                        "cleanup" if produced_results else "run",
                        ordinal,
                        started,
                    )
                    return factual if factual and factual.outcome == "failed" else interrupted
                # Persist the factual outcome BEFORE invoking any report adapter.
                result = self._record_execution(task_id, request, plan, execution, ordinal, started)
                if result.outcome == "passed" and self.read_report:
                    report = self.read_report(execution)
                    scope = prove_suite_scope(plan, commands, execution, report, self.artifacts)
                    report_refs = []
                    if report:
                        report_refs = [report.evidence_id, report.normalized_evidence_id]
                        for ref in report_refs:
                            self.state.register_artifact(ref)
                    self._save(
                        task_id,
                        "scope",
                        {
                            "kind": "scope",
                            "request_hash": request,
                            "ordinal": ordinal,
                            "scope_proven": scope,
                            "report": report_refs,
                            "artifacts": [result.evidence_id, *report_refs],
                        },
                    )
                return result

            run_three(execute)
        except ProjectExecutionFailure as error:
            produced_results = self.executor.results[prepare_cursor:]
            for produced in produced_results:
                self._record_execution(task_id, request, plan, produced, 0, prepare_started)
            if error.result not in produced_results:
                self._record_execution(task_id, request, plan, error.result, 0, prepare_started)
        except InfrastructureInterruption as error:
            for produced in self.executor.results[prepare_cursor:]:
                self._record_execution(task_id, request, plan, produced, 0, prepare_started)
            self._record_interruption(task_id, request, plan, error, "prepare", 0, prepare_started)
        finally:
            if prepared is not None:
                try:
                    self.executor.dispose(prepared.prepared_id)
                except InfrastructureInterruption as error:
                    self._record_interruption(
                        task_id, request, plan, error, "cleanup", 0, datetime.now(UTC)
                    )
        recovered = self.recover(task_id, request, plan, commands)
        if recovered is None:
            return self._interrupt(task_id, request, "infrastructure_interrupted")
        return recovered

    def _record_interruption(
        self,
        task: str,
        request: str,
        plan: EnvironmentPlan,
        error: InfrastructureInterruption,
        phase: str,
        ordinal: int,
        started: datetime,
    ) -> SuiteResult:
        references: set[str] = set()

        def register(digest: str) -> None:
            if digest in references:
                return
            references.add(digest)
            self.state.register_artifact(digest)
            try:
                value = json.loads(self.artifacts.get(digest))
            except (ValueError, UnicodeError):
                return
            if isinstance(value, dict):
                for key, reference in value.items():
                    if key.endswith("_artifact") and isinstance(reference, str) and reference:
                        register(reference)

        register(error.evidence_artifact)
        with self.state.transaction():
            suite_id = None
            if phase == "run":
                suite_id = self.state.record_suite_run(
                    task,
                    command=plan.suite.argv,
                    environment_hash=plan.digest,
                    outcome="interrupted",
                    exit_code=None,
                    started_at=started,
                    finished_at=datetime.now(UTC),
                )
            digest = self._save(
                task,
                "execution_result",
                {
                    "kind": "result",
                    "request_hash": request,
                    "phase": phase,
                    "ordinal": ordinal,
                    "outcome": "interrupted",
                    "exit_code": None,
                    "scope_proven": False,
                    "artifacts": sorted(references),
                    "suite_run_id": suite_id,
                    "started_at": started.isoformat(),
                    "finished_at": datetime.now(UTC).isoformat(),
                },
            )
        return SuiteResult(max(1, ordinal), "interrupted", None, digest)

    def _record_execution(
        self,
        task: str,
        request: str,
        plan: EnvironmentPlan,
        execution: ExecutionResult,
        ordinal: int,
        started: datetime,
    ) -> SuiteResult:
        refs = self._execution_refs(execution)
        outcome: Literal["passed", "failed"] = (
            "passed" if execution.reason == "passed" else "failed"
        )
        with self.state.transaction():
            suite_id = None
            if execution.phase == "run":
                suite_id = self.state.record_suite_run(
                    task,
                    command=execution.argv,
                    environment_hash=plan.digest,
                    outcome="completed" if outcome == "passed" else "rejected",
                    exit_code=execution.exit_code,
                    started_at=started,
                    finished_at=datetime.now(UTC),
                    stdout=execution.stdout_artifact,
                    stderr=execution.stderr_artifact,
                )
            digest = self._save(
                task,
                "execution_result",
                {
                    "kind": "result",
                    "request_hash": request,
                    "phase": execution.phase,
                    "ordinal": ordinal,
                    "outcome": outcome,
                    "exit_code": execution.exit_code,
                    "scope_proven": False,
                    "artifacts": refs,
                    "suite_run_id": suite_id,
                    "execution": asdict(execution),
                    "started_at": started.isoformat(),
                    "finished_at": datetime.now(UTC).isoformat(),
                },
            )
        return SuiteResult(max(1, ordinal), outcome, execution.exit_code, digest)

    def _interrupt(self, task: str, request: str, reason: str) -> ValidationResult:
        self._save(
            task,
            "sequence_interrupted",
            {
                "kind": "result",
                "request_hash": request,
                "phase": "run",
                "ordinal": 0,
                "outcome": "interrupted",
                "exit_code": None,
                "scope_proven": False,
                "reason_code": reason,
                "artifacts": [],
            },
        )
        return self._finish(task, request, "interrupted", self._history(task, request))
