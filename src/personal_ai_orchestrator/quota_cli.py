"""Minimal P3 CLI for explaining a normalized quota snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from personal_ai_orchestrator.quota_observability import QuotaSnapshot, render_quota_explanation


def load_snapshot(path: Path) -> QuotaSnapshot:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "snapshot" in payload:
        payload = payload["snapshot"]
    return QuotaSnapshot.model_validate(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="Explain normalized provider quota state")
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--pool", required=True)
    args = parser.parse_args()

    snapshot = load_snapshot(args.snapshot)
    print(render_quota_explanation(args.provider, args.pool, snapshot))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
