"""Host-owned Pi execution-verification probe.

Runs exactly one real Pi JSON worker invocation for one exact PAO execution
target and records durable execution-verification evidence. Discovery never
sets execution_verified; only this explicit owner-invoked probe can create the
exact-target VERIFIED evidence that the normal routing/dispatch path reads.

This probe uses the same Pi model translation, bounded worker process, worktree
guard, and JSON stream validator as the production Pi executor. It never reads
Pi auth files or prints credential material.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from personal_ai_orchestrator.dispatch_executor import (
    MAX_WORKER_STDERR_BYTES,
    build_worker_env,
)
from personal_ai_orchestrator.execution_evidence import (
    ExecutionEvidenceJournal,
    ExecutionVerificationOutcome,
    build_execution_evidence,
)
from personal_ai_orchestrator.pi_provider_discovery import PI_PROVIDER_SPECS
from personal_ai_orchestrator.pi_runtime import (
    PI_MAX_STDOUT_BYTES,
    PI_PROTOCOL_ERROR_EXIT,
    PiRuntimeConfig,
    build_pi_json_argv,
    pi_model_ref,
    seed_pi_worktree_guard,
    summarize_pi_json_stream,
)
from personal_ai_orchestrator.process_supervisor import ProcessSupervisor

PROBE_PROMPT = "Reply with exactly: PERSONAL-AI-ORCHESTRATOR-PI-EXECUTION-PROBE-OK"
PROBE_MARKER = "PERSONAL-AI-ORCHESTRATOR-PI-EXECUTION-PROBE-OK"
PROBE_TIMEOUT_SECONDS = 300.0


async def _probe(
    *,
    pi_bin: str,
    model_ref: str,
    cwd: Path,
    guard_path: Path,
    timeout_seconds: float,
) -> tuple[int, bytes, bytes, bool]:
    argv = build_pi_json_argv(
        config=PiRuntimeConfig(pi_bin=pi_bin),
        model_ref=model_ref,
        intent=PROBE_PROMPT,
        guard_path=guard_path,
    )
    supervisor = ProcessSupervisor()
    supervised = await supervisor.start(
        argv,
        cwd=cwd,
        env=build_worker_env(),
    )

    try:
        result = await supervisor.collect(
            supervised,
            timeout_seconds=timeout_seconds,
            stdout_cap=PI_MAX_STDOUT_BYTES,
            stderr_cap=MAX_WORKER_STDERR_BYTES,
            grace_seconds=5.0,
            cleanup_budget_seconds=10.0,
            eof_grace_seconds=2.0,
        )
    except TimeoutError:
        result = supervised.collected_result
        if result is None:
            return 124, b"", b"", True
        return 124, result.stdout, result.stderr, result.truncated
    return result.exit_code, result.stdout, result.stderr, result.truncated


def run_pi_execution_probe(
    *,
    pi_bin: str,
    provider_id: str,
    execution_target_id: str,
    model_sku_id: str,
    cwd: Path,
    evidence_root: Path,
    timeout_seconds: float = PROBE_TIMEOUT_SECONDS,
) -> dict[str, str]:
    """Run one real Pi worker and journal scoped verification evidence."""

    journal = ExecutionEvidenceJournal(evidence_root)

    spec = next(
        (item for item in PI_PROVIDER_SPECS if item.pao_provider_id == provider_id),
        None,
    )
    expected_target_id = (
        f"pi-{provider_id}-{model_sku_id.split('/', 1)[-1]}"
        if "/" in model_sku_id
        else f"pi-{provider_id}-{model_sku_id}"
    )
    if spec is None or execution_target_id != expected_target_id:
        evidence = build_execution_evidence(
            provider_id=provider_id,
            execution_target_id=execution_target_id,
            model_sku_id=model_sku_id,
            result=ExecutionVerificationOutcome.UNKNOWN,
            reason_code="PI_PROBE_TARGET_IDENTITY_MISMATCH",
        )
        journal.append(evidence)
        return evidence.model_dump(mode="json")

    guard_path = seed_pi_worktree_guard(evidence_root / "pi-probe-policy")
    model_ref = pi_model_ref(
        provider_id=provider_id,
        model_sku_id=model_sku_id,
    )

    try:
        exit_code, stdout, _stderr, truncated = asyncio.run(
            _probe(
                pi_bin=pi_bin,
                model_ref=model_ref,
                cwd=cwd,
                guard_path=guard_path,
                timeout_seconds=timeout_seconds,
            )
        )
    except Exception:
        evidence = build_execution_evidence(
            provider_id=provider_id,
            execution_target_id=execution_target_id,
            model_sku_id=model_sku_id,
            result=ExecutionVerificationOutcome.RUNTIME_UNAVAILABLE,
            reason_code="PI_PROBE_RUNTIME_UNAVAILABLE",
        )
        journal.append(evidence)
        return evidence.model_dump(mode="json")

    summary = summarize_pi_json_stream(stdout)
    expected_model_match = summary.matches_model_ref(model_ref)
    marker_seen = PROBE_MARKER.encode("utf-8") in stdout

    if (
        exit_code == 0
        and not truncated
        and summary.completed
        and expected_model_match
        and marker_seen
    ):
        result = ExecutionVerificationOutcome.VERIFIED
        reason = "REAL_PI_WORKER_PROBE_SUCCEEDED"
    elif exit_code == PI_PROTOCOL_ERROR_EXIT or not summary.protocol_valid:
        result = ExecutionVerificationOutcome.UNKNOWN
        reason = "PI_PROBE_PROTOCOL_INVALID"
    elif summary.final_stop_reason == "error":
        result = ExecutionVerificationOutcome.UNKNOWN
        reason = "PI_PROBE_PROVIDER_ERROR"
    elif truncated:
        result = ExecutionVerificationOutcome.UNKNOWN
        reason = "PI_PROBE_OUTPUT_TRUNCATED"
    else:
        result = ExecutionVerificationOutcome.UNKNOWN
        reason = f"PI_PROBE_EXIT_{exit_code}"

    evidence = build_execution_evidence(
        provider_id=provider_id,
        execution_target_id=execution_target_id,
        model_sku_id=model_sku_id,
        result=result,
        reason_code=reason,
    )
    journal.append(evidence)
    return evidence.model_dump(mode="json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Establish durable Pi execution-verification evidence",
    )
    parser.add_argument("--pi", default="pi")
    parser.add_argument("--provider", required=True)
    parser.add_argument("--execution-target", required=True)
    parser.add_argument("--model-sku", required=True)
    parser.add_argument("--cwd", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=PROBE_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)

    rendered = run_pi_execution_probe(
        pi_bin=args.pi,
        provider_id=args.provider,
        execution_target_id=args.execution_target,
        model_sku_id=args.model_sku,
        cwd=args.cwd,
        evidence_root=args.evidence_root,
        timeout_seconds=args.timeout,
    )
    print(json.dumps(rendered, sort_keys=True))
    return 0 if rendered["result"] == ExecutionVerificationOutcome.VERIFIED.value else 1


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "PROBE_MARKER",
    "PROBE_PROMPT",
    "PROBE_TIMEOUT_SECONDS",
    "run_pi_execution_probe",
]
