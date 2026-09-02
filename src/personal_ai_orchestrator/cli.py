"""P4.0 thin CLI client for the Personal AI Orchestrator control plane.

The CLI never executes natural-language arguments. Every command renders into one structured
request against the local daemon's typed control API; the daemon stays authoritative for all
safety state.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from pydantic import BaseModel

from personal_ai_orchestrator.control_client import (
    ControlPlaneClient,
    ControlPlaneError,
    ControlPlaneUnavailable,
)
from personal_ai_orchestrator.runtime_config import default_application_support_layout

DEFAULT_SOCKET = default_application_support_layout().socket_path


def _socket_path(args: argparse.Namespace) -> Path:
    return Path(args.socket or os.environ.get("PAO_CONTROL_SOCKET") or DEFAULT_SOCKET)


def _emit(view: BaseModel, *, as_json: bool, render) -> None:
    if as_json:
        print(json.dumps(json.loads(view.model_dump_json()), indent=2, sort_keys=True))
    else:
        render(view)


def _render_task(view) -> None:
    print(f"task:        {view.task_id}")
    print(f"request:     {view.request_id}")
    print(f"state:       {view.state} (v{view.state_version})")
    print(f"created_at:  {view.created_at}")
    print(f"updated_at:  {view.updated_at}")
    print(f"intent:      {view.intent}")


def _render_cancel(view) -> None:
    _render_task(view.task)
    print(f"cancelled_now: {str(view.cancelled_now).lower()}")


def _render_task_list(view) -> None:
    print(f"total: {view.total}")
    for task in view.tasks:
        print(f"{task.task_id}  {task.state:<18} v{task.state_version}  {task.updated_at}")


def _render_run_list(view) -> None:
    if not view.runs:
        print("no runs")
        return
    for run in view.runs:
        finished = run.finished_at or "-"
        print(f"{run.run_id}  {run.status:<10} worker={run.worker_id} pid={run.pid} end={finished}")


def _render_report(view) -> None:
    print(f"task:        {view.task_id}")
    print(f"task_state:  {view.task_state}")
    print(f"status:      {view.status}")
    if view.evidence_id:
        print(f"evidence:    {view.evidence_id}")
    if view.failure_reason:
        print(f"failure:     {view.failure_reason}")
    if view.result is not None:
        print(f"passed:      {str(view.result.passed).lower()}")
        print(f"profile:     {view.result.profile}")
        print(f"changed:     {', '.join(view.result.changed_paths) or '-'}")
        for stage in view.result.stages:
            mark = "PASS" if stage.passed else "FAIL"
            print(f"stage:       [{mark}] {stage.name}")


def _render_routing(view) -> None:
    print(f"decision:    {view.decision_id}")
    print(f"request:     {view.request_id}")
    print(f"created_at:  {view.created_at}")
    decision = view.decision
    print(f"mode:        {decision.get('mode', 'UNKNOWN')}")
    target = decision.get("selected_execution_target_id")
    print(f"selected:    {target or 'none'}")
    if decision.get("fallback_reason"):
        print(f"fallback:    {decision['fallback_reason']}")


def _render_provider_health(view) -> None:
    for provider in view.providers:
        print(f"provider: {provider.provider_id} ({provider.display_name})")
        print(f"  accounts: {provider.account_count}")
        for pool in provider.quota_pools:
            print(
                f"  pool: {pool.quota_pool_id} state={pool.state} "
                f"confidence={pool.confidence} source={pool.measurement_source_type}"
            )
            for window in pool.windows:
                remaining = (
                    "UNKNOWN"
                    if window.remaining_fraction is None
                    else f"{window.remaining_fraction:.1%}"
                )
                print(
                    f"    window: {window.window_id} state={window.state} "
                    f"confidence={window.confidence} remaining={remaining}"
                )
        for target in provider.execution_targets:
            observed = (
                f"observed={target.observed_availability.state}"
                if target.observed_availability is not None
                else "observed=NONE"
            )
            runtime = (
                "UNKNOWN"
                if target.runtime_available is None
                else str(target.runtime_available).lower()
            )
            print(
                f"  target: {target.execution_target_id} enabled={str(target.enabled).lower()} "
                f"runtime_available={runtime} {observed}"
            )


def _render_active_status(view) -> None:
    print(f"production_active: {view.production_active}")
    print(f"authorized:        {str(view.authorized).lower()}")
    for reason in view.blocking_reasons:
        print(f"blocker:           {reason}")


def _render_approval_list(view) -> None:
    if not view.approvals:
        print("no approvals")
        return
    for approval in view.approvals:
        resolved = approval.resolved_at or "-"
        print(
            f"{approval.approval_id}  {approval.kind}  {approval.status}  task={approval.task_id}"
            f"  resolved={resolved}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pao",
        description="Personal AI Orchestrator local control-plane client",
    )
    parser.add_argument(
        "--socket",
        help="control-plane Unix socket path (env PAO_CONTROL_SOCKET)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="render raw JSON responses",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    submit = subparsers.add_parser("submit", help="submit a task (idempotent by request id)")
    submit.add_argument("--task-id", required=True)
    submit.add_argument("--request-id", required=True)
    submit.add_argument(
        "--project-id",
        required=True,
        help="registered project id; terminal cwd is never used as task context",
    )
    submit.add_argument(
        "--intent",
        required=True,
        help="natural-language task intent (never executed)",
    )

    status = subparsers.add_parser("status", help="show one task")
    status.add_argument("task_id")

    subparsers.add_parser("list", help="list tasks").add_argument("--limit", type=int)

    cancel = subparsers.add_parser("cancel", help="cancel a non-running task")
    cancel.add_argument("task_id")
    cancel.add_argument("--request-id")

    report = subparsers.add_parser("report", help="show the verification report for a task")
    report.add_argument("task_id")

    routing = subparsers.add_parser("routing", help="show the latest routing decision for a task")
    routing.add_argument("task_id")

    runs = subparsers.add_parser("runs", help="list runs for a task")
    runs.add_argument("task_id")

    subparsers.add_parser("providers", help="show sanitized provider health")
    subparsers.add_parser("quota", help="show sanitized quota health")
    subparsers.add_parser("active-status", help="show Production ACTIVE gate status")

    approvals = subparsers.add_parser("approvals", help="list approvals for a task")
    approvals.add_argument("task_id")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    client = ControlPlaneClient(_socket_path(args))

    try:
        if args.command == "submit":
            view = client.submit(
                task_id=args.task_id,
                request_id=args.request_id,
                project_id=args.project_id,
                intent=args.intent,
            )
            _emit(view, as_json=args.json, render=_render_task)
        elif args.command == "status":
            _emit(client.get_task(args.task_id), as_json=args.json, render=_render_task)
        elif args.command == "list":
            limit = args.limit
            view = client.list_tasks(limit=limit)
            _emit(view, as_json=args.json, render=_render_task_list)
        elif args.command == "cancel":
            view = client.cancel(args.task_id, request_id=args.request_id)
            _emit(view, as_json=args.json, render=_render_cancel)
        elif args.command == "report":
            view = client.verification_report(args.task_id)
            _emit(view, as_json=args.json, render=_render_report)
        elif args.command == "routing":
            _emit(client.routing_decision(args.task_id), as_json=args.json, render=_render_routing)
        elif args.command == "runs":
            _emit(client.task_runs(args.task_id), as_json=args.json, render=_render_run_list)
        elif args.command in {"providers", "quota"}:
            view = client.providers() if args.command == "providers" else client.quota()
            _emit(view, as_json=args.json, render=_render_provider_health)
        elif args.command == "active-status":
            _emit(client.active_status(), as_json=args.json, render=_render_active_status)
        elif args.command == "approvals":
            view = client.approvals_for_task(args.task_id)
            _emit(view, as_json=args.json, render=_render_approval_list)
        else:  # pragma: no cover - argparse enforces the command set
            parser.error(f"unknown command {args.command}")
    except ControlPlaneUnavailable as error:
        print(f"pao: {error}; is the daemon running?", file=sys.stderr)
        return 1
    except ControlPlaneError as error:
        print(f"pao: {error.code} (HTTP {error.status})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main"]
