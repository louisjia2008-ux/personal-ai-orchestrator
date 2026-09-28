"""Host-owned execution-verification probe.

Runs ONE real, supervised OpenCode worker invocation against a specific
execution target and records durable :mod:`execution_evidence`. A
successful real invocation may establish ``VERIFIED``; a failed call
never can. This is the only bootstrap path by which a dynamically
discovered target becomes launchable for owner dispatch — the boolean
is never flipped manually.

The probe is intentionally host-owned: it is invoked explicitly by the
owner (CLI), uses the exact worker argv profile used by dispatch, and
its evidence is content-addressed in the append-only journal.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from personal_ai_orchestrator.dispatch_executor import build_worker_env
from personal_ai_orchestrator.execution_evidence import (
    ExecutionEvidenceJournal,
    ExecutionVerificationOutcome,
    build_execution_evidence,
)
from personal_ai_orchestrator.process_supervisor import ProcessSupervisor

PROBE_PROMPT = "Reply with exactly: PERSONAL-AI-ORCHESTRATOR-EXECUTION-PROBE-OK"
PROBE_MARKER = "PERSONAL-AI-ORCHESTRATOR-EXECUTION-PROBE-OK"
PROBE_TIMEOUT_SECONDS = 300.0


async def _probe(
    *,
    opencode_bin: str,
    model_sku_id: str,
    cwd: Path,
    timeout_seconds: float,
) -> tuple[int, bytes]:
    argv = (
        opencode_bin,
        "run",
        "--model",
        model_sku_id,
        "--",
        PROBE_PROMPT,
    )
    supervisor = ProcessSupervisor()
    supervised = await supervisor.start(argv, cwd=cwd, env=build_worker_env())
    try:
        stdout, _stderr = await asyncio.wait_for(
            supervised.process.communicate(), timeout=timeout_seconds
        )
    except TimeoutError:
        code = await supervisor.cancel(supervised, grace_seconds=5.0)
        return code, b""
    return supervised.process.returncode or 0, stdout


def run_execution_probe(
    *,
    opencode_bin: str,
    provider_id: str,
    execution_target_id: str,
    model_sku_id: str,
    cwd: Path,
    evidence_root: Path,
    timeout_seconds: float = PROBE_TIMEOUT_SECONDS,
) -> dict[str, str]:
    """Run one real worker invocation and journal sanitized evidence."""

    journal = ExecutionEvidenceJournal(evidence_root)
    try:
        exit_code, stdout = asyncio.run(
            _probe(
                opencode_bin=opencode_bin,
                model_sku_id=model_sku_id,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
            )
        )
    except Exception:
        evidence = build_execution_evidence(
            provider_id=provider_id,
            execution_target_id=execution_target_id,
            model_sku_id=model_sku_id,
            result=ExecutionVerificationOutcome.RUNTIME_UNAVAILABLE,
            reason_code="PROBE_RUNTIME_UNAVAILABLE",
        )
        journal.append(evidence)
        return evidence.model_dump(mode="json")

    if exit_code == 0 and PROBE_MARKER.encode("utf-8") in stdout:
        result = ExecutionVerificationOutcome.VERIFIED
        reason = "REAL_WORKER_PROBE_SUCCEEDED"
    else:
        # A failed probe never fabricates a specific failure cause we
        # did not observe; it records UNKNOWN truthfully.
        result = ExecutionVerificationOutcome.UNKNOWN
        reason = f"PROBE_EXIT_{exit_code}"
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
        description=(
            "Establish durable execution-verification evidence via one real worker invocation"
        )
    )
    parser.add_argument("--opencode", default="opencode")
    parser.add_argument("--provider", required=True)
    parser.add_argument("--execution-target", required=True)
    parser.add_argument("--model-sku", required=True)
    parser.add_argument("--cwd", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=PROBE_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)

    rendered = run_execution_probe(
        opencode_bin=args.opencode,
        provider_id=args.provider,
        execution_target_id=args.execution_target,
        model_sku_id=args.model_sku,
        cwd=args.cwd,
        evidence_root=args.evidence_root,
        timeout_seconds=args.timeout,
    )
    print(json.dumps(rendered, sort_keys=True))
    return 0 if rendered["result"] == "VERIFIED" else 1


if __name__ == "__main__":  # pragma: no cover - operator tool
    sys.exit(main())


__all__ = ["PROBE_PROMPT", "run_execution_probe"]
