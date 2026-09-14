"""Host-owned owner-dispatch execution pipeline.

This module closes the authoritative execution path:

    dispatch reservation
    → host-owned worktree allocation
    → single-writer lock
    → quota admission (real collectors, only at dispatch time)
    → real OpenCode worker spawn inside the task worktree
    → atomic READY → RUNNING with durable run row
    → supervised worker exit
    → deterministic host verifier
    → VERIFIED / BLOCKED
    → writer lock release + durable evidence

Safety properties (tested in tests/test_dispatch_executor.py):

- ``TaskState.RUNNING`` only ever means a real supervised child exists
  (:meth:`SafetyKernelStore.start_dispatched_worker` atomically pairs the
  run row, the READY→RUNNING transition and dispatch START).
- Worker text (COMPLETE/DONE/SUCCESS) has zero authority; only the
  deterministic verifier may produce VERIFIED.
- The main repository must remain bit-identical (HEAD + clean status)
  across the run or the task fails closed with ``MAIN_REPO_MUTATED``.
- Preparation failure leaves no RUNNING task, no orphan active run, no
  orphan writer lock, an unchanged main repo and sanitized durable
  failure evidence.
- The worker environment is a minimal allowlist; credential values never
  enter the worker env, and ``auth.json`` is never read by this host.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import secrets
import shutil
import signal
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from personal_ai_orchestrator.execution_controller import (
    apply_verification_result,
    begin_verification,
    cancel_worker_run,
    record_worker_exit,
    validate_execution_target_launch,
)
from personal_ai_orchestrator.execution_evidence import (
    ExecutionEvidenceJournal,
    ExecutionVerificationOutcome,
    build_execution_evidence,
)
from personal_ai_orchestrator.model_registry import ModelRegistry
from personal_ai_orchestrator.process_supervisor import (
    ProcessSpawnError,
    ProcessSupervisor,
    SpawnDiagnostics,
    SpawnStage,
    SupervisedProcess,
)
from personal_ai_orchestrator.quota_availability import (
    QuotaAvailabilityEvidence,
    QuotaAvailabilityJournal,
    QuotaAvailabilityState,
    observe_exhaustion,
    observe_success,
    unknown_availability,
)
from personal_ai_orchestrator.quota_collectors.base import (
    QuotaCollectionResult,
    QuotaCollectionStatus,
)
from personal_ai_orchestrator.runtime_quota_routing import quota_pool_id_for_target
from personal_ai_orchestrator.safety_kernel import (
    OwnerDispatchRecord,
    OwnerDispatchStatus,
    SafetyKernelStore,
    TaskState,
    expected_source_state_for_dispatch_authority,
    shadow_identity_payload,
)
from personal_ai_orchestrator.shadow_evidence import ShadowEvidenceJournal
from personal_ai_orchestrator.verification_evidence import VerificationEvidenceJournal
from personal_ai_orchestrator.verifier import (
    DeterministicVerifier,
    VerifierProfile,
)
from personal_ai_orchestrator.worker_outcome_classifier import WorkerFailureClass
from personal_ai_orchestrator.worktree_manager import ManagedWorktree, WorktreeManager

MAX_WORKER_STDOUT_BYTES = 256 * 1024
MAX_WORKER_STDERR_BYTES = 64 * 1024

# Bounded, sanitized transcript tails kept in the run result so the owner
# can see what the worker actually did. The worker's text keeps zero task
# authority — it is evidence for human eyes, never an input to any gate.
WORKER_TRANSCRIPT_TAIL_BYTES = 8 * 1024
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

# Minimum execution environment for the authenticated OpenCode runtime.
# This is deliberately NOT the discovery blocklist reuse: discovery strips
# credentials because it must never see them; the worker runtime needs its
# own HOME-managed auth (OpenCode reads its own credential store itself)
# and nothing else. No credential values are placed in the worker env.
_WORKER_ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LC_MESSAGES",
    "TZ",
    "TMPDIR",
    "TERM",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
    "NO_COLOR",
)


def build_worker_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    source = os.environ if base_env is None else base_env
    env: dict[str, str] = {"TERM": "dumb"}
    for name in _WORKER_ENV_ALLOWLIST:
        value = source.get(name)
        if value is not None:
            env[name] = value
    return env


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return completed.stdout.strip()


def main_repo_fingerprint(repo: Path) -> dict[str, str]:
    """HEAD SHA + porcelain status digest for main-repo immutability proof."""

    head = _git(repo, "rev-parse", "HEAD")
    status = _git(repo, "status", "--porcelain")
    return {
        "head": head,
        "status_sha256": hashlib.sha256(status.encode("utf-8")).hexdigest(),
        "status_lines": str(len([line for line in status.splitlines() if line])),
    }


@dataclass(frozen=True)
class DispatchExecutorConfig:
    """Host-owned repository and worker policy; never client-supplied."""

    repo_path: Path
    worktree_root: Path
    opencode_bin: str = "opencode"
    worker_timeout_seconds: float = 1800.0
    worker_grace_seconds: float = 5.0
    require_quota_certainty: bool = False
    verifier_profile: VerifierProfile | None = None
    extra_worker_args: tuple[str, ...] = ()
    worker_permission_config: Path | None = None


@dataclass
class QuotaAdmission:
    admitted: bool
    failure_code: str | None
    evidence: QuotaAvailabilityEvidence
    collected: bool = False


@dataclass
class ActiveExecution:
    task_id: str
    request_id: str
    run_id: str
    writer_token: str
    supervised: SupervisedProcess
    loop: asyncio.AbstractEventLoop
    cancel_requested: asyncio.Event = field(default_factory=asyncio.Event)
    cancel_completed: asyncio.Event = field(default_factory=asyncio.Event)


class ExecutionSupervisor:
    """Thread-safe registry of live owner-dispatch executions."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[str, ActiveExecution] = {}

    def register(self, execution: ActiveExecution) -> None:
        with self._lock:
            self._active[execution.task_id] = execution

    def unregister(self, task_id: str) -> None:
        with self._lock:
            self._active.pop(task_id, None)

    def get(self, task_id: str) -> ActiveExecution | None:
        with self._lock:
            return self._active.get(task_id)

    def owned_task_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._active))


class OwnerDispatchExecutor:
    """Executes one RESERVED owner dispatch through the full product path."""

    def __init__(
        self,
        *,
        state_db: str | Path,
        config: DispatchExecutorConfig,
        registry_provider: Callable[[], ModelRegistry],
        verification_journal: VerificationEvidenceJournal,
        execution_evidence_journal: ExecutionEvidenceJournal,
        quota_availability_journal: QuotaAvailabilityJournal,
        quota_collectors: dict[str, Any] | None = None,
        supervisor: ProcessSupervisor | None = None,
        execution_supervisor: ExecutionSupervisor | None = None,
        store_factory: Callable[[], SafetyKernelStore] | None = None,
        shadow_journal: ShadowEvidenceJournal | None = None,
    ) -> None:
        self._state_db = state_db
        self.config = config
        self._registry_provider = registry_provider
        self._verification_journal = verification_journal
        self._execution_evidence_journal = execution_evidence_journal
        self._quota_availability_journal = quota_availability_journal
        self._quota_collectors = quota_collectors or {}
        self._supervisor = supervisor or ProcessSupervisor()
        self.execution_supervisor = execution_supervisor or ExecutionSupervisor()
        self._store_factory = store_factory or (lambda: SafetyKernelStore(state_db))
        # M1 WP5a-2: journal used to finalize the SUPERVISED_AUTO pending
        # shadow once the run reaches a truthful outcome. ``None`` keeps the
        # owner-dispatch-only behavior (no pending shadow exists to finalize).
        self._shadow_journal = shadow_journal

    # ------------------------------------------------------------------
    # public entrypoints
    # ------------------------------------------------------------------

    def execute(self, request_id: str) -> None:
        """Run the full dispatch lifecycle to a terminal outcome.

        Never raises for expected execution failures: every failure is
        recorded as durable sanitized evidence (BLOCKED dispatch + audit).
        """

        try:
            asyncio.run(self.execute_async(request_id))
        except Exception as error:  # pragma: no cover - final safety net
            store = self._open_store()
            try:
                dispatch = store.get_owner_dispatch_by_request_id(request_id)
                if dispatch.status not in {
                    OwnerDispatchStatus.FINISHED,
                    OwnerDispatchStatus.BLOCKED,
                    OwnerDispatchStatus.CANCELLED,
                }:
                    store.mark_owner_dispatch_blocked(
                        request_id,
                        failure_code="EXECUTOR_INTERNAL_ERROR",
                        failure_reason=type(error).__name__,
                    )
            except Exception:
                pass
            finally:
                store.close()

    async def execute_async(self, request_id: str) -> None:
        try:
            await self._execute_async(request_id)
        finally:
            self._disable_worker_session(request_id)

    def _disable_worker_session(self, request_id: str) -> None:
        """Runtime hook: synchronously revoke optional worker capabilities."""

    def _activate_worker_session(self, request_id: str) -> None:
        """Runtime hook called only after the durable RUNNING commit."""

    async def _execute_async(self, request_id: str) -> None:
        store = self._open_store()
        try:
            dispatch = store.get_owner_dispatch_by_request_id(request_id)
        except KeyError:
            store.close()
            return
        if dispatch.status is not OwnerDispatchStatus.RESERVED:
            # Re-entry after a completed/crashed run is not a ghost path.
            store.close()
            return

        task = store.get_task(dispatch.task_id)
        # Round 6 §4-§7: the legal source state is a property of the
        # DURABLE dispatch authority, never of the current task row. A
        # stale SUPERVISED_AUTO reservation whose task was vetoed /
        # mode-aborted back to READY must fail closed here — it may NOT
        # be reinterpreted as an owner dispatch merely because the task
        # happens to be READY. Unknown authority fails closed with a
        # sanitized dispatch BLOCKED marker and no worker spawn (no
        # quota / worktree / writer side effect can have happened yet:
        # this is the first gate after the durable row load).
        try:
            expected_state = expected_source_state_for_dispatch_authority(
                dispatch.authority
            )
        except ValueError:
            store.mark_owner_dispatch_blocked(
                request_id,
                failure_code="UNKNOWN_DISPATCH_AUTHORITY",
                failure_reason=(
                    "dispatch authority has no legal worker source state"
                ),
            )
            store.close()
            return
        if task.state is not expected_state:
            store.mark_owner_dispatch_blocked(
                request_id,
                failure_code="TASK_NOT_READY",
                failure_reason=(
                    f"task state {task.state.value} is not the legal source"
                    f" state {expected_state.value} for authority"
                    f" {dispatch.authority}"
                ),
            )
            store.close()
            return
        ready_version = task.state_version

        project_root = self._project_root_or_blocked(store, dispatch, request_id)
        if project_root is None:
            store.close()
            return
        main_before = self._fingerprint_or_blocked(store, request_id, project_root)
        if main_before is None:
            store.close()
            return

        # Defense in depth: re-validate launchability against the live
        # registry and durable execution evidence even though the control
        # plane already gated the dispatch reservation. Runtime
        # availability is derived from the actual worker binary surface.
        try:
            validate_execution_target_launch(
                self._registry_provider(),
                execution_target_id=dispatch.execution_target_id,
                runtime_available=shutil.which(self.config.opencode_bin) is not None,
                execution_evidence_journal=self._execution_evidence_journal,
            )
        except RuntimeError as error:
            self._fail_pre_worker(
                store,
                request_id,
                failure_code="EXECUTION_TARGET_NOT_LAUNCHABLE",
                failure_reason=str(error),
            )
            store.close()
            return

        # -- worktree ----------------------------------------------------
        worktree, failure = self._prepare_worktree(store, dispatch)
        if worktree is None:
            self._fail_pre_worker(
                store,
                request_id,
                failure_code=failure or "WORKTREE_ALLOCATION_FAILED",
                failure_reason="host worktree allocation failed",
            )
            store.close()
            return

        # -- writer lock -------------------------------------------------
        writer_token = f"writer-{dispatch.dispatch_id}-{secrets.token_hex(8)}"
        try:
            store.acquire_writer(dispatch.task_id, writer_token)
        except Exception:
            self._fail_pre_worker(
                store,
                request_id,
                failure_code="WRITER_LOCK_UNAVAILABLE",
                failure_reason="single-writer lock could not be acquired",
            )
            store.close()
            return

        # -- quota admission --------------------------------------------
        try:
            admission = self._admit_quota(store, dispatch)
        except Exception:
            admission = None
        if admission is None or not admission.admitted:
            self._fail_pre_worker(
                store,
                request_id,
                writer_token=writer_token,
                failure_code=(
                    admission.failure_code
                    if admission is not None and admission.failure_code
                    else "QUOTA_ADMISSION_FAILED"
                ),
                failure_reason="quota admission blocked the billable launch",
            )
            store.close()
            return

        # -- worker spawn + atomic RUNNING -------------------------------
        spawn_observation = self._supervisor.begin_spawn_observation()
        try:
            supervised = await self._spawn_worker(store, dispatch, worktree)
        except Exception as error:
            observed = self._supervisor.observed_process()
            diagnostics = self._spawn_failure_diagnostics(error, observed)
            if observed is not None:
                exit_code = await self._supervisor.abort_unowned(observed)
                diagnostics = diagnostics.evolved(
                    child_exited_before_ownership=True,
                    safe_exit_code=exit_code,
                )
            self._audit_spawn(
                store,
                dispatch.task_id,
                "WORKER_SPAWN_FAILED_DETAIL",
                diagnostics,
            )
            self._fail_pre_worker(
                store,
                request_id,
                writer_token=writer_token,
                failure_code="WORKER_SPAWN_FAILED",
                failure_reason=(
                    "worker process failed before durable ownership: "
                    f"stage={diagnostics.spawn_stage.value} "
                    f"exception={diagnostics.exception_class or type(error).__name__} "
                    f"child_created={'yes' if diagnostics.child_created else 'no'}"
                ),
            )
            store.close()
            return
        finally:
            self._supervisor.end_spawn_observation(spawn_observation)

        self._audit_spawn(
            store,
            dispatch.task_id,
            "PROCESS_CREATED",
            self._supervised_spawn_diagnostics(supervised),
        )

        run_id = f"run-{dispatch.dispatch_id}"
        self._audit_spawn(
            store,
            dispatch.task_id,
            "DURABLE_RUN_REGISTRATION_STARTED",
            self._supervised_spawn_diagnostics(supervised).evolved(
                spawn_stage=SpawnStage.DURABLE_RUN_REGISTRATION_STARTED,
            ),
        )
        try:
            store.start_dispatched_worker(
                dispatch_id=dispatch.dispatch_id,
                task_id=dispatch.task_id,
                expected_task_version=ready_version,
                run_id=run_id,
                worker_id=dispatch.execution_target_id,
                writer_token=writer_token,
                pid=supervised.pid,
                expected_state=expected_state,
            )
        except Exception as error:
            # The child exists but is not durably owned: kill the exact
            # child before any state repair so no orphan process remains.
            exit_code = await self._supervisor.cancel(
                supervised, grace_seconds=self.config.worker_grace_seconds
            )
            self._audit_spawn(
                store,
                dispatch.task_id,
                "RUN_START_FAILED_DETAIL",
                self._supervised_spawn_diagnostics(supervised).evolved(
                    spawn_stage=SpawnStage.DURABLE_RUN_REGISTRATION_STARTED,
                    exception_class=type(error).__name__,
                    safe_errno=error.errno if isinstance(error, OSError) else None,
                    child_exited_before_ownership=True,
                    safe_exit_code=exit_code,
                ),
            )
            self._fail_pre_worker(
                store,
                request_id,
                writer_token=writer_token,
                failure_code="RUN_START_FAILED",
                failure_reason=f"atomic RUNNING transition failed: {type(error).__name__}",
            )
            store.close()
            return

        self._audit_spawn(
            store,
            dispatch.task_id,
            "DURABLE_RUN_REGISTERED",
            self._supervised_spawn_diagnostics(supervised).evolved(
                spawn_stage=SpawnStage.DURABLE_RUN_REGISTERED,
                durable_run_created=True,
            ),
        )
        execution = ActiveExecution(
            task_id=dispatch.task_id,
            request_id=request_id,
            run_id=run_id,
            writer_token=writer_token,
            supervised=supervised,
            loop=asyncio.get_running_loop(),
        )
        try:
            self._activate_worker_session(request_id)
            self.execution_supervisor.register(execution)
        except Exception as error:
            self._audit_spawn(
                store,
                dispatch.task_id,
                "WORKER_OWNERSHIP_REGISTRATION_FAILED_DETAIL",
                self._supervised_spawn_diagnostics(supervised).evolved(
                    spawn_stage=SpawnStage.DURABLE_RUN_REGISTERED,
                    exception_class=type(error).__name__,
                    safe_errno=error.errno if isinstance(error, OSError) else None,
                    durable_run_created=True,
                ),
            )
            self._emergency_repair(
                store,
                request_id,
                dispatch,
                supervised,
                run_id,
                writer_token,
                error=error,
            )
            store.close()
            return
        self._audit_spawn(
            store,
            dispatch.task_id,
            "WORKER_RUNNING",
            self._supervised_spawn_diagnostics(supervised).evolved(
                spawn_stage=SpawnStage.WORKER_RUNNING,
                durable_run_created=True,
            ),
        )

        # -- supervised worker lifecycle ---------------------------------
        real_invocation_succeeded = False
        worker_exit_code: int | None = None
        try:
            self._audit_spawn(
                store,
                dispatch.task_id,
                "PROTOCOL_BOOTSTRAP_STARTED",
                self._supervised_spawn_diagnostics(supervised).evolved(
                    spawn_stage=SpawnStage.PROTOCOL_BOOTSTRAP_STARTED,
                    durable_run_created=True,
                    protocol_bootstrap_started=True,
                ),
            )
            exit_code, stdout, stderr, truncated = await self._wait_for_worker(supervised)
            self._audit_spawn(
                store,
                dispatch.task_id,
                "PROTOCOL_BOOTSTRAP_COMPLETED",
                self._supervised_spawn_diagnostics(supervised).evolved(
                    spawn_stage=SpawnStage.PROTOCOL_BOOTSTRAP_COMPLETED,
                    durable_run_created=True,
                    protocol_bootstrap_started=True,
                    protocol_bootstrap_completed=True,
                    safe_exit_code=exit_code,
                ),
            )
            self._disable_worker_session(request_id)
            worker_exit_code = exit_code
            if (
                exit_code != 0
                and execution.cancel_requested.is_set()
                and not execution.cancel_completed.is_set()
            ):
                # The owner asked for this kill; the cancel transaction
                # owns the final state. Give it the chance to finish.
                try:
                    await asyncio.wait_for(
                        execution.cancel_completed.wait(), timeout=15.0
                    )
                except TimeoutError:
                    pass
            if execution.cancel_requested.is_set() and exit_code != 0:
                task_now = store.get_task(dispatch.task_id)
                if task_now.state is not TaskState.RUNNING:
                    self.execution_supervisor.unregister(dispatch.task_id)
                    try:
                        store.release_writer(dispatch.task_id, writer_token)
                    except Exception:
                        pass
                    if task_now.state is TaskState.CANCELLED:
                        store.mark_owner_dispatch_cancelled(request_id)
                    store.close()
                    return
            result = self._host_result_envelope(
                exit_code, stdout, stderr, truncated=truncated
            )
            next_state = record_worker_exit(
                store,
                task_id=dispatch.task_id,
                run_id=run_id,
                exit_code=exit_code,
                worker_result=result,
            )
            real_invocation_succeeded = (
                exit_code == 0 and next_state is TaskState.WORKER_FINISHED
            )
            # M1 WP4: classify the worker outcome and write the
            # ``QUOTA_BLOCKED`` evidence + ``observe_*`` journal
            # row when the classifier flags a rate-limit / quota
            # hit. The classification runs **before** the dispatch
            # writes its own evidence so the journal row points
            # at the same instant as the worker exit. ``A2`` is
            # explicitly off-limits: the helper never bumps
            # ``consecutive_failures`` (the journal write below
            # uses ``observe_exhaustion`` for windowed targets
            # and ``observe_rate_limited`` for unmetered ones).
            self._classify_and_record_worker_outcome(
                store,
                dispatch=dispatch,
                execution_target_id=dispatch.execution_target_id,
                exit_code=exit_code,
                worker_result=result,
            )
        except asyncio.CancelledError:
            # Loop shutdown/cancellation must never strand a live child,
            # a RUNNING task or the writer lock. Repair synchronously
            # (no awaits that could re-cancel), then propagate.
            self._emergency_repair(
                store,
                request_id,
                dispatch,
                supervised,
                run_id,
                writer_token,
                cancelled=True,
            )
            raise
        except TimeoutError:
            self._disable_worker_session(request_id)
            exit_code = await self._supervisor.cancel(
                supervised, grace_seconds=self.config.worker_grace_seconds
            )
            worker_exit_code = exit_code
            result = self._host_result_envelope(
                exit_code, b"", b"", truncated=False, timeout=True
            )
            next_state = record_worker_exit(
                store,
                task_id=dispatch.task_id,
                run_id=run_id,
                exit_code=exit_code,
                worker_result=result,
            )
        except ValueError:
            # The task was finalized concurrently (for example by an owner
            # cancellation closing the run first). Never resurrect state.
            task_now = store.get_task(dispatch.task_id)
            self.execution_supervisor.unregister(dispatch.task_id)
            try:
                store.release_writer(dispatch.task_id, writer_token)
            except Exception:
                pass
            if task_now.state is TaskState.CANCELLED:
                store.mark_owner_dispatch_cancelled(request_id)
            else:
                store.finish_owner_dispatch(
                    request_id,
                    failure_code=f"TASK_{task_now.state.value}",
                    failure_reason=(
                        "task finalized concurrently during worker execution"
                    ),
                )
            store.close()
            return
        except Exception as error:
            # Final executor safety net after RUNNING was granted: kill
            # the exact child and fail the run/task/dispatch/lock closed.
            self._emergency_repair(
                store,
                request_id,
                dispatch,
                supervised,
                run_id,
                writer_token,
                error=error,
            )
            store.close()
            return
        finally:
            self.execution_supervisor.unregister(dispatch.task_id)

        # -- deterministic verification ----------------------------------
        # M1 WP5a-2: when this is a SUPERVISED_AUTO dispatch, the
        # verification path owns the pending-shadow finalization — only a
        # deterministic verifier verdict may close the shadow observation
        # as verified. ``auto_pending_finalized`` tracks whether that
        # happened so the close-out below never double-finalizes with a
        # different payload (observation ids are deterministic; content
        # divergence would fail-closed in the journal instead).
        #
        # Round 4 (final outcome ordering): the verifier runs FIRST but
        # commits NOTHING terminal — the main-repo immutability result is
        # computed next, and only the COMPOSED final host outcome
        # (verifier authoritative pass AND main repo unchanged) enters
        # the single terminal transaction + immutable finalize intent.
        # The normal path can therefore never leave a verified=True
        # shadow behind a later VERIFIED → BLOCKED downgrade.
        auto_pending_finalized = False
        verdict = None
        shadow_pending_id: str | None = None
        if next_state is TaskState.WORKER_FINISHED:
            try:
                begin_verification(store, task_id=dispatch.task_id)
                profile = self.config.verifier_profile
                if profile is None:
                    from personal_ai_orchestrator.verifier import VerificationResult

                    verdict = VerificationResult(
                        profile="none",
                        passed=False,
                        changed_paths=(),
                        unexpected_paths=(),
                        stages=(),
                        evidence_id=None,
                        failure_reason="no host verifier profile configured",
                    )
                else:
                    verifier = DeterministicVerifier()
                    raw = verifier.verify(
                        worktree.worktree_path,
                        base_sha=worktree.base_sha,
                        profile=profile,
                    )
                    self._verification_journal.append(raw)
                    verdict = raw
                shadow_pending_id = self._supervised_auto_pending_id(store, dispatch)
                # ``begin_verification`` moved the durable task row to
                # VERIFYING; mirror it locally so the composed final
                # commit below runs exactly on this success path.
                next_state = TaskState.VERIFYING
            except Exception as error:
                store.transition_task(
                    dispatch.task_id,
                    TaskState.BLOCKED,
                    expected_version=store.get_task(dispatch.task_id).state_version,
                    reason=f"verification stage failed closed: {type(error).__name__}",
                )
                next_state = TaskState.BLOCKED
                verdict = None
                shadow_pending_id = None

        # -- main repo immutability --------------------------------------
        # Round 4: computed BEFORE the terminal commit so the immutable
        # finalize intent freezes the FINAL host outcome (§2). A
        # fingerprint failure is fail-closed as a mutation.
        try:
            main_after = main_repo_fingerprint(project_root)
            main_unchanged = main_before == main_after
        except Exception:
            main_after = dict(main_before)
            main_unchanged = False

        # -- ONE final terminal commit -----------------------------------
        # Composes verifier authority + main-repo immutability into a
        # single VERIFYING → VERIFIED/BLOCKED transaction with the
        # immutable finalize intent; no later downgrade is needed or
        # possible on this path.
        if next_state is TaskState.VERIFYING and verdict is not None:
            try:
                next_state = apply_verification_result(
                    store,
                    task_id=dispatch.task_id,
                    result=verdict,
                    evidence_journal=self._verification_journal,
                    shadow_journal=self._shadow_journal if shadow_pending_id else None,
                    shadow_pending_id=shadow_pending_id,
                    shadow_dispatch_id=dispatch.dispatch_id,
                    shadow_execution_success=worker_exit_code == 0,
                    shadow_main_repo_unchanged=main_unchanged,
                )
                # Round 3: the terminal truth + durable finalization
                # intent committed in ONE transaction, and the inline
                # drain already replayed the observation + discarded the
                # pending on the happy path. A failed drain only leaves
                # the intent open — the tick / restart replay finishes it.
                auto_pending_finalized = shadow_pending_id is not None
            except Exception as error:
                store.transition_task(
                    dispatch.task_id,
                    TaskState.BLOCKED,
                    expected_version=store.get_task(dispatch.task_id).state_version,
                    reason=f"verification stage failed closed: {type(error).__name__}",
                )
                next_state = TaskState.BLOCKED

        # -- evidence + lock release -------------------------------------
        verified = next_state is TaskState.VERIFIED

        evidence = build_execution_evidence(
            provider_id=self._provider_id(dispatch),
            execution_target_id=dispatch.execution_target_id,
            model_sku_id=self._model_sku_id(dispatch),
            result=(
                ExecutionVerificationOutcome.VERIFIED
                if real_invocation_succeeded
                else ExecutionVerificationOutcome.UNKNOWN
            ),
            reason_code=(
                "REAL_WORKER_DISPATCH_SUCCEEDED"
                if real_invocation_succeeded
                else f"WORKER_OUTCOME_{next_state.value}"
            ),
        )
        try:
            self._execution_evidence_journal.append(evidence)
        except Exception:
            pass

        try:
            store.release_writer(dispatch.task_id, writer_token)
        except Exception:
            pass

        if next_state is TaskState.CANCELLED:
            store.mark_owner_dispatch_cancelled(request_id)
        else:
            failure_code = None if verified else f"TASK_{next_state.value}"
            store.finish_owner_dispatch(
                request_id,
                failure_code=failure_code,
                failure_reason=None if verified else f"task ended in {next_state.value}",
            )
        # M1 WP5a-2: close the SUPERVISED_AUTO shadow lifecycle for
        # outcomes the verification path did not already finalize
        # (worker failed before WORKER_FINISHED, concurrent cancel, ...)
        # and clear the now-inert auto metadata off the active task row.
        # Round 3: the failed-execution finalize is now a DURABLE intent
        # + best-effort drain — a real worker ran, so its observation
        # must survive an executor crash (the tick's terminal sweep and
        # the finalize outbox replay it); the blind filesystem discard
        # fallback is gone.
        if not auto_pending_finalized:
            self._finalize_supervised_auto_pending(
                store,
                dispatch,
                verified=False,
                execution_success=worker_exit_code == 0,
            )
        self._clear_supervised_auto_metadata(store, dispatch)
        try:
            store._audit(
                dispatch.task_id,
                "OWNER_DISPATCH_COMPLETED",
                {
                    "dispatch_id": dispatch.dispatch_id,
                    "request_id": request_id,
                    "run_id": run_id,
                    "final_state": next_state.value,
                    "execution_evidence_id": evidence.evidence_id,
                    "quota_state": admission.evidence.state.value,
                    "quota_confidence": admission.evidence.confidence.value,
                    "main_head_before": main_before["head"],
                    "main_head_after": main_after["head"],
                    "main_repo_unchanged": main_unchanged,
                    "worktree_path": str(worktree.worktree_path),
                },
            )
        except Exception:
            pass
        store.close()

    def cancel_active(self, task_id: str, *, timeout: float = 60.0) -> bool:
        """Cancel the live supervised worker for ``task_id`` if present.

        Runs on the executor's own event loop: asyncio subprocess
        transports are bound to the loop that created them, so the exact
        child can only be awaited from there.
        """

        execution = self.execution_supervisor.get(task_id)
        if execution is None:
            return False
        future = asyncio.run_coroutine_threadsafe(
            self._cancel_on_loop(execution), execution.loop
        )
        return bool(future.result(timeout=timeout))

    async def _cancel_on_loop(self, execution: ActiveExecution) -> bool:
        self._disable_worker_session(execution.request_id)
        execution.cancel_requested.set()
        store = self._open_store()
        try:
            try:
                await cancel_worker_run(
                    store,
                    self._supervisor,
                    execution.supervised,
                    task_id=execution.task_id,
                    run_id=execution.run_id,
                    writer_token=execution.writer_token,
                    grace_seconds=self.config.worker_grace_seconds,
                )
            except (ValueError, RuntimeError):
                # The exact child died (or its exit was recorded) while
                # cancellation was in flight. Never resurrect state; the
                # worker-exit path owns the final transition.
                task_now = store.get_task(execution.task_id)
                if task_now.state is TaskState.CANCELLED:
                    return True
                return False
            try:
                store.mark_owner_dispatch_cancelled(execution.request_id)
            except Exception:
                pass
        finally:
            self.execution_supervisor.unregister(execution.task_id)
            store.close()
            execution.cancel_completed.set()
        return True

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    @staticmethod
    def _audit_spawn(
        store: SafetyKernelStore,
        task_id: str,
        event_type: str,
        diagnostics: SpawnDiagnostics,
    ) -> None:
        """Best-effort diagnostics must never change lifecycle correctness."""

        try:
            store._audit(task_id, event_type, diagnostics.as_dict())
        except Exception:
            pass

    @staticmethod
    def _supervised_spawn_diagnostics(supervised: Any) -> SpawnDiagnostics:
        diagnostics = getattr(supervised, "spawn_diagnostics", None)
        if isinstance(diagnostics, SpawnDiagnostics):
            return diagnostics
        empty_hash = hashlib.sha256(b"").hexdigest()
        return SpawnDiagnostics(
            spawn_stage=SpawnStage.PROCESS_CREATED,
            executable_path_hash=empty_hash,
            executable_exists=False,
            executable_executable=False,
            cwd_hash=empty_hash,
            cwd_exists=False,
            argv_count=0,
            argv_shape=(),
            env_key_names=(),
            process_group_setup_stage="PROCESS_OBSERVED_BY_TEST_DOUBLE",
            stdout_pipe_created=False,
            stderr_pipe_created=False,
            child_created=True,
            child_pid_observed=getattr(supervised, "pid", None) is not None,
        )

    @staticmethod
    def _spawn_failure_diagnostics(
        error: Exception, observed: SupervisedProcess | None
    ) -> SpawnDiagnostics:
        if observed is not None:
            return observed.spawn_diagnostics.evolved(
                spawn_stage=SpawnStage.SPAWN_ADAPTER_POST_CREATE,
                exception_class=type(error).__name__,
                safe_errno=error.errno if isinstance(error, OSError) else None,
            )
        if isinstance(error, ProcessSpawnError):
            return error.diagnostics
        # Runtime-specific setup can fail before ProcessSupervisor sees argv.
        # Persist no guessed path, argv, or environment data in that case.
        empty_hash = hashlib.sha256(b"").hexdigest()
        return SpawnDiagnostics(
            spawn_stage=SpawnStage.CONTRACT_VALIDATED,
            exception_class=type(error).__name__,
            safe_errno=error.errno if isinstance(error, OSError) else None,
            child_created=False,
            child_pid_observed=False,
            child_exited_before_ownership=False,
            safe_exit_code=None,
            executable_path_hash=empty_hash,
            executable_exists=False,
            executable_executable=False,
            cwd_hash=empty_hash,
            cwd_exists=False,
            argv_count=0,
            argv_shape=(),
            env_key_names=(),
            process_group_setup_stage="NOT_STARTED",
            stdout_pipe_created=False,
            stderr_pipe_created=False,
        )

    def _emergency_repair(
        self,
        store: SafetyKernelStore,
        request_id: str,
        dispatch: OwnerDispatchRecord,
        supervised: SupervisedProcess,
        run_id: str,
        writer_token: str,
        *,
        cancelled: bool = False,
        error: BaseException | None = None,
    ) -> None:
        """Last-resort fail-closed repair after RUNNING was granted.

        Kills the exact supervised child, closes the run row with a
        human-readable reason, blocks the task, releases the writer lock
        and blocks the dispatch. Every step is best-effort so one broken
        step cannot skip the rest. Synchronous by design so it also works
        during loop teardown.
        """

        from personal_ai_orchestrator.execution_controller import (
            _failure_result_payload,
            _human_reason_for_failure,
        )

        self._disable_worker_session(request_id)
        self.execution_supervisor.unregister(dispatch.task_id)
        self._supervisor.emergency_kill(supervised)
        # emergency_kill always sends SIGKILL to the process group. Tails
        # are not drained (the supervisor does not buffer them in this
        # path), so worker_result is None and only the signal is reported.
        failure_payload = _failure_result_payload(
            exit_code=None, signal=signal.SIGKILL, worker_result=None
        )
        failure_payload["emergency_repair"] = True
        reason = _human_reason_for_failure(exit_code=None, signal=signal.SIGKILL)
        try:
            task = store.get_task(dispatch.task_id)
            if task.state is TaskState.RUNNING:
                run = store.connection.execute(
                    "SELECT status FROM runs WHERE run_id=?", (run_id,)
                ).fetchone()
                if run is not None and run["status"] == "RUNNING":
                    store.finish_run(
                        run_id, status="FAILED", result=failure_payload
                    )
                store.transition_task(
                    dispatch.task_id,
                    TaskState.BLOCKED,
                    expected_version=task.state_version,
                    reason=f"executor emergency repair: {reason}",
                )
        except Exception:
            pass
        try:
            store.release_writer(dispatch.task_id, writer_token)
        except Exception:
            pass
        try:
            store.mark_owner_dispatch_blocked(
                request_id,
                failure_code="EXECUTOR_INTERNAL_ERROR",
                failure_reason=(
                    f"{type(error).__name__ if error is not None else 'CANCELLED'}; {reason}"
                ),
            )
        except Exception:
            pass

    def _open_store(self) -> SafetyKernelStore:
        return self._store_factory()

    def _supervised_auto_pending_id(
        self, store: SafetyKernelStore, dispatch: OwnerDispatchRecord
    ) -> str | None:
        """The pending-shadow id for a SUPERVISED_AUTO dispatch, if any.

        裁决 15 keeps ``pending_id`` and the task row's ``auto_decision_id``
        at the same value. Owner-initiated dispatches carry no auto metadata
        and return ``None`` — the existing owner path never touches the
        pending-shadow journal.
        """

        if dispatch.authority != "SUPERVISED_AUTO":
            return None
        try:
            task = store.get_task(dispatch.task_id)
        except Exception:
            return None
        return task.auto_decision_id

    def _finalize_supervised_auto_pending(
        self,
        store: SafetyKernelStore,
        dispatch: OwnerDispatchRecord,
        *,
        verified: bool,
        execution_success: bool,
    ) -> None:
        """Finalize the pending shadow with a truthful non-verified outcome.

        Round 3: a real worker launched (run row exists), so the
        observation is DURABLE truth — record the finalization intent
        first (immutable payload, ``observed_at`` pinned once), then
        best-effort drain. A crash before the drain leaves the intent
        open; the tick / restart replay finalizes + discards exactly
        once. When an intent already owns this pending (e.g. a tick
        reconstruction raced us), the first durable intent wins and we
        only drain it.
        """

        from personal_ai_orchestrator.safety_kernel import ShadowFinalizationIntent
        from personal_ai_orchestrator.supervised_auto_step import (
            drain_auto_shadow_finalize_outbox,
        )

        pending_id = self._supervised_auto_pending_id(store, dispatch)
        if pending_id is None or self._shadow_journal is None:
            return
        try:
            pending = self._shadow_journal.load_pending(pending_id)
        except (FileNotFoundError, ValueError):
            return
        if not store.shadow_finalize_intent_exists(pending_id):
            intent = ShadowFinalizationIntent(
                pending_id=pending_id,
                task_id=dispatch.task_id,
                dispatch_id=dispatch.dispatch_id,
                request_id=pending.request_id,
                decision_id=pending.decision_id,
                verified=verified,
                execution_success=execution_success,
                verification_success=False,
                observed_at=datetime.now(UTC).isoformat(),
                identity_json=shadow_identity_payload(pending),
            )
            try:
                store.enqueue_shadow_finalization(intent)
            except RuntimeError:
                # A different payload already owns this finalization id
                # — the first durable intent is authoritative.
                pass
        drain_auto_shadow_finalize_outbox(store, self._shadow_journal)

    def _clear_supervised_auto_metadata(
        self, store: SafetyKernelStore, dispatch: OwnerDispatchRecord
    ) -> None:
        """Clear the task row's auto metadata once the auto lifecycle ends.

        §32: after the run reached a post-RUNNING state the four ``auto_*``
        columns are inert control state and must not linger on the active
        row. Historical truth lives in the audit trail, the routing
        decision row and the shadow journal.

        Round 4 §8/§9 (hard recovery guard): when THIS lifecycle truly
        launched a worker (exact current-cycle dispatch + exact
        ``run-{dispatch_id}`` row), the metadata — the last recovery
        correlation to the pending's routing identity — may clear ONLY
        when a durable finalize intent exists or the finalized
        observation is already proven. Without that proof the clear is
        refused, a sanitized system event records why, and the task row
        stays fail-closed recoverable.
        """

        if dispatch.authority != "SUPERVISED_AUTO":
            return
        try:
            task = store.get_task(dispatch.task_id)
            if task.auto_decision_id is None:
                return
            from personal_ai_orchestrator.supervised_auto_step import (
                real_execution_recovery_proof,
            )

            if not real_execution_recovery_proof(
                store,
                self._shadow_journal,
                task_id=dispatch.task_id,
                pending_id=task.auto_decision_id,
            ):
                try:
                    store.record_system_event(
                        "AUTO_SHADOW_RECOVERY_GUARD_HELD",
                        {
                            "task_id": dispatch.task_id,
                            "pending_id": task.auto_decision_id,
                            "reason": (
                                "real execution without durable finalize "
                                "intent or proven observation"
                            ),
                        },
                    )
                except Exception:  # noqa: BLE001 — event best-effort
                    pass
                return
            store.clear_auto_state_metadata(
                dispatch.task_id,
                expected_version=task.state_version,
                reason="supervised auto lifecycle closed after dispatch",
            )
        except Exception:
            pass

    def _project_root_or_blocked(
        self,
        store: SafetyKernelStore,
        dispatch: OwnerDispatchRecord,
        request_id: str,
    ) -> Path | None:
        try:
            task = store.get_task(dispatch.task_id)
        except Exception:
            store.mark_owner_dispatch_blocked(
                request_id,
                failure_code="TASK_NOT_FOUND",
                failure_reason="dispatch task could not be loaded",
            )
            return None
        if task.project_id is None:
            self._fail_pre_worker(
                store,
                request_id,
                failure_code="MISSING_PROJECT_ID",
                failure_reason="coding tasks require an explicit registered project",
            )
            return None
        if task.base_sha is None:
            self._fail_pre_worker(
                store,
                request_id,
                failure_code="PROJECT_BASE_SHA_MISSING",
                failure_reason="coding tasks require a durable base_sha",
            )
            return None
        try:
            project = store.get_project(task.project_id)
        except KeyError:
            self._fail_pre_worker(
                store,
                request_id,
                failure_code="PROJECT_NOT_REGISTERED",
                failure_reason="task project_id is not registered",
            )
            return None
        if project.storage_availability.value != "ONLINE":
            self._fail_pre_worker(
                store,
                request_id,
                failure_code=f"PROJECT_{project.storage_availability.value}",
                failure_reason="registered project is not currently available",
            )
            return None
        root = Path(project.git_root)
        if not root.exists():
            try:
                from personal_ai_orchestrator.safety_kernel import ProjectAvailability

                store.update_project_availability(
                    project.project_id,
                    storage_availability=ProjectAvailability.MISSING,
                )
            except Exception:
                pass
            self._fail_pre_worker(
                store,
                request_id,
                failure_code="PROJECT_MISSING",
                failure_reason="registered project path is missing",
            )
            return None
        try:
            top = Path(_git(root, "rev-parse", "--show-toplevel")).resolve()
            if top != root.resolve():
                raise ValueError("registered path is no longer the git root")
            _git(root, "cat-file", "-e", f"{task.base_sha}^{{commit}}")
        except Exception:
            try:
                from personal_ai_orchestrator.safety_kernel import ProjectAvailability

                store.update_project_availability(
                    project.project_id,
                    storage_availability=ProjectAvailability.INVALID_REPOSITORY,
                )
            except Exception:
                pass
            self._fail_pre_worker(
                store,
                request_id,
                failure_code="PROJECT_INVALID_REPOSITORY",
                failure_reason="registered project is not a usable git repository",
            )
            return None
        return root

    def _fingerprint_or_blocked(
        self, store: SafetyKernelStore, request_id: str, repo_path: Path
    ) -> dict[str, str] | None:
        try:
            return main_repo_fingerprint(repo_path)
        except Exception:
            store.mark_owner_dispatch_blocked(
                request_id,
                failure_code="REPOSITORY_POLICY_FAILED",
                failure_reason="main repository fingerprint could not be established",
            )
            return None

    @staticmethod
    def _check_delegated_workspace(store, dispatch, managed) -> None:
        from personal_ai_orchestrator.safety_kernel import AUTHORITY_DELEGATED_CHILD

        if dispatch.authority != AUTHORITY_DELEGATED_CHILD:
            return
        child_path = managed.worktree_path.resolve()
        rows = store.connection.execute(
            "SELECT worktree_path FROM workspaces WHERE task_id != ?", (dispatch.task_id,)
        ).fetchall()
        if any(Path(row["worktree_path"]).resolve() == child_path for row in rows):
            raise ValueError("delegated child workspace aliases another task workspace")

    def _prepare_worktree(
        self, store: SafetyKernelStore, dispatch: OwnerDispatchRecord
    ) -> tuple[ManagedWorktree | None, str | None]:
        try:
            task = store.get_task(dispatch.task_id)
            if task.project_id is None:
                return None, "MISSING_PROJECT_ID"
            if task.base_sha is None:
                return None, "PROJECT_BASE_SHA_MISSING"
            project = store.get_project(task.project_id)
            if project.storage_availability.value != "ONLINE":
                return None, f"PROJECT_{project.storage_availability.value}"
            repo_path = Path(project.git_root)
            base_sha = task.base_sha
            manager = WorktreeManager(self.config.worktree_root)
            try:
                existing = store.get_workspace(dispatch.task_id)
                managed = manager.adopt(
                    repo_path=Path(existing.repo_path),
                    task_id=dispatch.task_id,
                    worktree_path=Path(existing.worktree_path),
                    branch=existing.branch,
                    base_sha=existing.base_sha,
                )
                self._check_delegated_workspace(store, dispatch, managed)
                self._seed_worker_policy(managed)
                return managed, None
            except KeyError:
                pass
            managed = manager.create(
                repo_path=repo_path,
                task_id=dispatch.task_id,
                base_sha=base_sha,
            )
            self._check_delegated_workspace(store, dispatch, managed)
            self._seed_worker_policy(managed)
            store.register_workspace(
                task_id=dispatch.task_id,
                repo_path=str(managed.repo_path),
                worktree_path=str(managed.worktree_path),
                branch=managed.branch,
                base_sha=managed.base_sha,
                project_id=task.project_id,
                working_subpath=task.working_subpath,
            )
            return managed, None
        except FileExistsError:
            return None, "WORKTREE_ALREADY_EXISTS"
        except Exception:
            return None, "WORKTREE_ALLOCATION_FAILED"

    def _seed_worker_policy(self, managed: ManagedWorktree) -> None:
        """Seed the host-owned OpenCode worker sandbox into the worktree.

        Non-interactive ``opencode run`` auto-rejects permission prompts,
        which would make every edit fail. The host therefore provisions a
        project config that allows edits inside the assigned worktree and
        denies bash / webfetch outright. This is host policy, written
        before the worker exists; the worker can neither create nor
        change it afterwards through the denied surfaces.
        """

        seed = self.config.worker_permission_config
        if seed is None:
            return
        target = managed.worktree_path / "opencode.json"
        if target.exists():
            return
        target.write_bytes(seed.read_bytes())

    # -- M1 WP4: worker-outcome classification + journal write ------
    def _classify_and_record_worker_outcome(
        self,
        store: SafetyKernelStore,
        *,
        dispatch: OwnerDispatchRecord,
        execution_target_id: str,
        exit_code: int,
        worker_result: dict[str, object],
    ) -> None:
        """Inspect the worker's stderr and persist a quota-or-rate-limit cooldown.

        M1 WP4 closes the gap where ``owner-dispatch`` had no
        worker-side quota signal: every dispatch exited either
        ``VERIFIED`` (zero exit) or ``UNKNOWN`` (any non-zero exit),
        with no classifier to tell apart a rate-limit cooldown from
        an unrelated failure. The classifier replaces the legacy
        shadow text-match (which used the bare ``"quota"`` token
        and matched this project's own source code) with the
        conservative marker list in ``worker_outcome_classifier``.

        Windowed targets: ``observe_exhaustion`` flips the journal
        to COOLDOWN with a 1-hour window (the existing windowed
        behaviour). The execution-evidence journal also records a
        ``QUOTA_BLOCKED`` row so ``latest_verified_for_target``
        falls back to the most recent VERIFIED row (the
        post-WP3 §3.4 contract).
        Unmetered targets: ``observe_rate_limited`` with the
        15-minute default; the cooldown expires back to
        ``AVAILABLE_UNMETERED`` directly (the unmetered baseline
        tells ``state_at`` to skip the probe round-trip).

        V4 refactor: the body is split into three helpers
        (``_classify_worker_failure``,
        ``_apply_pool_kind_cooldown``,
        ``_record_quota_blocked_evidence``); this method is now a
        thin orchestrator.
        """

        verdict = self._classify_worker_failure(
            exit_code=exit_code,
            worker_result=worker_result,
        )
        if verdict is WorkerFailureClass.NONE:
            return
        if verdict is not WorkerFailureClass.QUOTA_OR_RATE_LIMIT:
            # Auth / unclassified failures do not advance the
            # quota journal. A future commit will add an
            # auth-evidence row; for now we keep the contract
            # tight so a regression cannot silently disable it.
            return

        provider_id = self._provider_id(dispatch)
        observed_at = datetime.now(UTC)
        try:
            reason_code = self._apply_pool_kind_cooldown(
                dispatch=dispatch,
                execution_target_id=execution_target_id,
                provider_id=provider_id,
                observed_at=observed_at,
            )
        except Exception:
            return
        if reason_code is None:
            return
        self._record_quota_blocked_evidence(
            dispatch=dispatch,
            execution_target_id=execution_target_id,
            provider_id=provider_id,
            observed_at=observed_at,
            reason_code=reason_code,
        )

    def _classify_worker_failure(
        self,
        *,
        exit_code: int,
        worker_result: dict[str, object],
    ) -> WorkerFailureClass:
        """Run the worker's stderr through ``classify_worker_failure``.

        Pulled out of ``_classify_and_record_worker_outcome`` (V4
        refactor — zero behaviour change). Returning a frozen enum
        keeps the orchestrator free of imports.
        """

        from personal_ai_orchestrator.worker_outcome_classifier import (
            classify_worker_failure as _classify,
        )

        stderr_tail_obj = worker_result.get("stderr_tail")
        stderr_tail = stderr_tail_obj if isinstance(stderr_tail_obj, str) else ""
        return _classify(exit_code=exit_code, stderr_tail=stderr_tail)

    def _apply_pool_kind_cooldown(
        self,
        *,
        dispatch: OwnerDispatchRecord,
        execution_target_id: str,
        provider_id: str,
        observed_at: datetime,
    ) -> str | None:
        """Apply the windowed-vs-unmetered cooldown to the quota journal.

        Windowed targets hit ``observe_exhaustion`` (1-hour
        cooldown, ``USAGE_LIMIT`` reason). Unmetered targets hit
        ``observe_rate_limited`` (15-minute default, the unmetered
        baseline tells ``state_at`` to skip the probe round-trip).
        Returns the reason code for the execution-evidence row, or
        ``None`` when the provider is not in the family registry
        (a defensive no-op — pre-WP4 dispatch has no signal).
        """

        from personal_ai_orchestrator.provider_discovery import (
            PROVIDER_FAMILIES as _PF,
        )
        from personal_ai_orchestrator.quota_availability import (
            observe_exhaustion as _observe_exhaustion,
        )
        from personal_ai_orchestrator.quota_availability import (
            observe_rate_limited as _observe_rate_limited,
        )

        family = next(
            (spec for spec in _PF if spec.provider_id == provider_id),
            None,
        )
        is_unmetered = bool(family and family.pool_kind == "unmetered")
        previous = self._quota_availability_journal.load(execution_target_id)
        quota_pool_id = quota_pool_id_for_target(
            self._registry_provider(), execution_target_id=execution_target_id, now=observed_at,
        )
        if quota_pool_id is None:
            return None
        if is_unmetered:
            self._quota_availability_journal.save(
                _observe_rate_limited(
                    previous,
                    execution_target_id=execution_target_id,
                    provider_id=provider_id,
                    quota_pool_id=quota_pool_id,
                    observed_at=observed_at,
                )
            )
            return "WORKER_RATE_LIMIT"
        self._quota_availability_journal.save(
            _observe_exhaustion(
                previous,
                execution_target_id=execution_target_id,
                provider_id=provider_id,
                quota_pool_id=quota_pool_id,
                observed_at=observed_at,
                sanitized_reason_code="USAGE_LIMIT",
            )
        )
        return "WORKER_QUOTA_BLOCKED"

    def _record_quota_blocked_evidence(
        self,
        *,
        dispatch: OwnerDispatchRecord,
        execution_target_id: str,
        provider_id: str,
        observed_at: datetime,
        reason_code: str,
    ) -> None:
        """Append a ``QUOTA_BLOCKED`` row to the execution-evidence journal.

        The ``latest_verified_for_target`` helper already demote-
        falls back from any non-VERIFIED outcome to the most recent
        VERIFIED row, so this single row keeps the dispatcher honest
        even when the cooldown is the only signal we have.
        """

        from personal_ai_orchestrator.execution_evidence import (
            ExecutionVerificationOutcome as _EVO,
        )
        from personal_ai_orchestrator.execution_evidence import (
            build_execution_evidence as _build_evidence,
        )

        try:
            evidence = _build_evidence(
                provider_id=provider_id,
                execution_target_id=execution_target_id,
                model_sku_id=self._model_sku_id(dispatch),
                observed_at=observed_at,
                result=_EVO.QUOTA_BLOCKED,
                reason_code=reason_code,
            )
            self._execution_evidence_journal.append(evidence)
        except Exception:
            pass

    def _admit_quota(
        self, store: SafetyKernelStore, dispatch: OwnerDispatchRecord
    ) -> QuotaAdmission:
        now = datetime.now(UTC)
        provider_id = self._provider_id(dispatch)
        execution_target_id = dispatch.execution_target_id
        quota_pool_id = quota_pool_id_for_target(
            self._registry_provider(), execution_target_id=execution_target_id, now=now,
        )
        if quota_pool_id is None:
            return QuotaAdmission(
                admitted=False, failure_code="QUOTA_BINDING_UNKNOWN", collected=False,
                evidence=unknown_availability(
                    execution_target_id=execution_target_id, provider_id=provider_id,
                    quota_pool_id="unbound", observed_at=now,
                ),
            )

        previous = self._quota_availability_journal.load(execution_target_id)
        # Definitive exhaustions short-circuit before any observation: they
        # are not expected to clear on their own within the dispatch window.
        # UNCERTAIN_LOCKED is checked AFTER the new observation instead so a
        # single successful collection can release the lock atomically.
        if previous is not None and previous.blocks_quota_billable_launch(now=now):
            return QuotaAdmission(
                admitted=False,
                failure_code="QUOTA_EXHAUSTED",
                evidence=previous,
                collected=False,
            )

        collector = self._quota_collectors.get(provider_id)
        evidence: QuotaAvailabilityEvidence
        if collector is None:
            evidence = unknown_availability(
                execution_target_id=execution_target_id,
                provider_id=provider_id,
                quota_pool_id=quota_pool_id,
                observed_at=now,
                previous=previous,
            )
            # UNKNOWN must remain UNKNOWN — and must remain durable.
            self._quota_availability_journal.save(evidence)
            # Re-projection: the save may have pushed us over the failure
            # threshold. Reject before falling through to the admit path.
            if evidence.state_at(now=now) is QuotaAvailabilityState.UNCERTAIN_LOCKED:
                return QuotaAdmission(
                    admitted=False,
                    failure_code="QUOTA_UNKNOWN",
                    evidence=evidence,
                    collected=False,
                )
            if self.config.require_quota_certainty:
                return QuotaAdmission(
                    admitted=False,
                    failure_code="QUOTA_UNKNOWN",
                    evidence=evidence,
                    collected=False,
                )
        else:
            result: QuotaCollectionResult = collector.collect()
            # A collector cannot silently redirect an explicitly bound target
            # to an unrelated balance. Unnamed legacy results cannot prove identity.
            if (
                result.snapshot is not None
                and result.snapshot.quota_pool_id != quota_pool_id
            ):
                result = QuotaCollectionResult(status=QuotaCollectionStatus.UNKNOWN)
            evidence = self._snapshot_to_availability(
                result,
                previous=previous,
                execution_target_id=execution_target_id,
                provider_id=provider_id,
                quota_pool_id=quota_pool_id,
                now=now,
            )
            self._quota_availability_journal.save(evidence)
            if evidence.state in {
                QuotaAvailabilityState.EXHAUSTED_OBSERVED,
                QuotaAvailabilityState.COOLDOWN,
            }:
                return QuotaAdmission(
                    admitted=False,
                    failure_code="QUOTA_EXHAUSTED",
                    evidence=evidence,
                    collected=True,
                )
            if evidence.state_at(now=now) is QuotaAvailabilityState.UNCERTAIN_LOCKED:
                return QuotaAdmission(
                    admitted=False,
                    failure_code="QUOTA_UNKNOWN",
                    evidence=evidence,
                    collected=True,
                )
            if (
                self.config.require_quota_certainty
                and evidence.state is QuotaAvailabilityState.UNKNOWN
            ):
                return QuotaAdmission(
                    admitted=False,
                    failure_code="QUOTA_UNKNOWN",
                    evidence=evidence,
                    collected=True,
                )

        store._audit(
            dispatch.task_id,
            "QUOTA_ADMITTED",
            {
                "dispatch_id": dispatch.dispatch_id,
                "execution_target_id": execution_target_id,
                "quota_state": evidence.state.value,
                "quota_confidence": evidence.confidence.value,
                "collected": collector is not None,
                "consecutive_failures": evidence.consecutive_failures,
            },
        )
        return QuotaAdmission(
            admitted=True, failure_code=None, evidence=evidence, collected=collector is not None
        )

    def _snapshot_to_availability(
        self,
        result: QuotaCollectionResult,
        *,
        previous: QuotaAvailabilityEvidence | None,
        execution_target_id: str,
        provider_id: str,
        quota_pool_id: str,
        now: datetime,
    ) -> QuotaAvailabilityEvidence:
        snapshot = result.snapshot
        if snapshot is not None and snapshot.state.value == "EXHAUSTED":
            return observe_exhaustion(
                previous,
                execution_target_id=execution_target_id,
                provider_id=provider_id,
                quota_pool_id=quota_pool_id,
                observed_at=now,
                sanitized_reason_code="PROVIDER_REPORTED_EXHAUSTED",
            )
        if snapshot is not None and result.status is QuotaCollectionStatus.SUCCESS:
            return observe_success(
                previous,
                execution_target_id=execution_target_id,
                provider_id=provider_id,
                quota_pool_id=quota_pool_id,
                observed_at=now,
            )
        if (
            snapshot is None
            and result.last_known_good is not None
            and result.last_known_good.state.value == "EXHAUSTED"
        ):
            return observe_exhaustion(
                previous,
                execution_target_id=execution_target_id,
                provider_id=provider_id,
                quota_pool_id=quota_pool_id,
                observed_at=now,
                sanitized_reason_code="LAST_KNOWN_GOOD_EXHAUSTED",
            )
        # UNKNOWN outcome: bump the streak so the lock can fire after the
        # configured number of consecutive failures.
        return unknown_availability(
            execution_target_id=execution_target_id,
            provider_id=provider_id,
            quota_pool_id=quota_pool_id,
            observed_at=now,
            previous=previous,
        )

    def _provider_id(self, dispatch: OwnerDispatchRecord) -> str:
        registry = self._registry_provider()
        target = registry.execution_targets.get(dispatch.execution_target_id)
        if target is None:
            return dispatch.execution_target_id.split("/")[0].split("-")[0]
        model = registry.models.get(target.model_sku_id)
        return model.provider_id if model is not None else "unknown"

    def _model_sku_id(self, dispatch: OwnerDispatchRecord) -> str:
        registry = self._registry_provider()
        target = registry.execution_targets.get(dispatch.execution_target_id)
        return target.model_sku_id if target is not None else dispatch.execution_target_id

    def _worker_argv(self, dispatch: OwnerDispatchRecord, intent: str) -> tuple[str, ...]:
        argv: list[str] = [
            self.config.opencode_bin,
            "run",
            "--model",
            self._model_sku_id(dispatch),
            *self.config.extra_worker_args,
            "--",
            intent,
        ]
        return tuple(argv)

    async def _spawn_worker(
        self,
        store: SafetyKernelStore,
        dispatch: OwnerDispatchRecord,
        worktree: ManagedWorktree,
    ) -> SupervisedProcess:
        task = store.get_task(dispatch.task_id)
        argv = self._worker_argv(dispatch, task.intent)
        store._audit(
            dispatch.task_id,
            "WORKER_ARGV_BUILT",
            {
                "dispatch_id": dispatch.dispatch_id,
                "argv": list(argv),
                "cwd": str(worktree.worktree_path),
            },
        )
        env = build_worker_env()
        self._audit_spawn(
            store,
            dispatch.task_id,
            "PROCESS_CREATE_STARTED",
            self._supervisor.inspect_contract(
                argv, cwd=worktree.worktree_path, env=env
            ).evolved(spawn_stage=SpawnStage.PROCESS_CREATE_STARTED),
        )
        return await self._supervisor.start(
            argv,
            cwd=worktree.worktree_path,
            env=env,
        )

    async def _wait_for_worker(
        self, supervised: SupervisedProcess
    ) -> tuple[int, bytes, bytes, bool]:
        """Bounded-drain wait: only the exact supervised child, capped output."""

        chunks: dict[str, list[bytes]] = {"out": [], "err": []}
        truncated = False

        async def _drain(stream: Any, cap: int, key: str) -> None:
            nonlocal truncated
            total = 0
            while True:
                chunk = await stream.read(min(8192, cap + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total <= cap:
                    chunks[key].append(chunk)
                else:
                    truncated = True

        stdout_task = asyncio.create_task(
            _drain(supervised.process.stdout, MAX_WORKER_STDOUT_BYTES, "out")
        )
        stderr_task = asyncio.create_task(
            _drain(supervised.process.stderr, MAX_WORKER_STDERR_BYTES, "err")
        )
        try:
            await asyncio.wait_for(
                supervised.process.wait(), timeout=self.config.worker_timeout_seconds
            )
        except TimeoutError:
            stdout_task.cancel()
            stderr_task.cancel()
            raise
        await asyncio.gather(stdout_task, stderr_task)
        stdout = b"".join(chunks["out"])
        stderr = b"".join(chunks["err"])
        returncode = supervised.process.returncode or 0
        self._supervisor._children.pop(supervised.pid, None)
        return returncode, stdout, stderr, truncated

    @staticmethod
    def _sanitize_transcript(data: bytes) -> str:
        """Bounded, printable, ANSI-free worker text for human eyes only."""

        tail = data[-WORKER_TRANSCRIPT_TAIL_BYTES:]
        text = tail.decode("utf-8", errors="replace")
        text = _ANSI_ESCAPE_RE.sub("", text)
        text = _CONTROL_CHARS_RE.sub("", text)
        return text

    @classmethod
    def _host_result_envelope(
        cls,
        exit_code: int,
        stdout: bytes,
        stderr: bytes,
        *,
        truncated: bool,
        timeout: bool = False,
    ) -> dict[str, Any]:
        """Host-derived metadata only. Worker text has zero task authority.

        The transcript tails are sanitized display evidence: the owner can
        finally see what the worker did (opencode narrates progress on
        stderr) without the raw bytes ever feeding a gate or a decision.
        """

        return {
            "exit_code": exit_code,
            "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
            "stdout_bytes": len(stdout),
            "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
            "stderr_bytes": len(stderr),
            "output_truncated": truncated,
            "timed_out": timeout,
            "stdout_tail": cls._sanitize_transcript(stdout),
            "stderr_tail": cls._sanitize_transcript(stderr),
        }

    def _fail_pre_worker(
        self,
        store: SafetyKernelStore,
        request_id: str,
        *,
        failure_code: str,
        failure_reason: str,
        writer_token: str | None = None,
    ) -> None:
        """Fail closed before any worker exists: no RUNNING, no orphan lock.

        Only a writer token acquired by THIS execution is released; a
        pre-existing foreign lock is never touched.

        Round 6 §11 — the task mutation is authority-aware: only the
        dispatch authority's OWN legal source state may transition to
        BLOCKED (OWNER → READY, SUPERVISED_AUTO → AUTO_GRACE). A stale
        SUPERVISED_AUTO reservation failing after a veto moved the task
        to READY must NOT mutate the owner-controlled READY task — the
        veto already won; the dispatch row alone carries the failure.
        """

        dispatch = store.get_owner_dispatch_by_request_id(request_id)
        if writer_token is not None:
            try:
                store.release_writer(dispatch.task_id, writer_token)
            except Exception:
                pass
        task = store.get_task(dispatch.task_id)
        try:
            authority_source_state = (
                expected_source_state_for_dispatch_authority(dispatch.authority)
            )
        except ValueError:
            authority_source_state = None  # unknown authority: never mutate
        if (
            authority_source_state is not None
            and task.state is authority_source_state
        ):
            store.transition_task(
                dispatch.task_id,
                TaskState.BLOCKED,
                expected_version=task.state_version,
                reason=f"owner dispatch failed before worker start: {failure_code}",
            )
        store.mark_owner_dispatch_blocked(
            request_id,
            failure_code=failure_code,
            failure_reason=failure_reason,
        )


__all__ = [
    "ActiveExecution",
    "DispatchExecutorConfig",
    "ExecutionSupervisor",
    "OwnerDispatchExecutor",
    "QuotaAdmission",
    "build_worker_env",
    "main_repo_fingerprint",
]
