#!/usr/bin/env python3
"""Run the frozen T012 corpus against the historical external regex gate."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests/fixtures/pi5b3g_dynamic_scope_corpus.json"
OUTPUT = ROOT / ".ralph/artifacts/t012-baseline.json"

FORBIDDEN = re.compile(
    r"(?i)(?:\bprovider\b|\bmodel\b|\bruntime\b|execution[ -]?target|"
    r"\bretr(?:y|ies)\b|\bfallback\b|\bdelegate\b|\bdelegation\b|"
    r"\bgrandchild(?:ren)?\b|\bshell\b|\bbash\b|\bpowershell\b|"
    r"\bnetwork\b|\bweb\b|\bcredential(?:s)?\b|\bsecret(?:s)?\b|"
    r"\bcurl\b|\bwget\b|\bssh\b|https?://|file://|\.\.|(?:^|\s)/)",
)


def historical_decision(case: dict[str, object]) -> str:
    observation = case["observation"]
    intent = str(case["intent"])
    reason = str(case["reason"])
    expected_file = (
        f"pi5b3g_obs{observation}_child.json"
        if observation in {1, 2, 3}
        else None
    )
    paths = sorted(set(re.findall(r"[A-Za-z0-9_.-]+\.json", intent)))
    checks = (
        observation in {1, 2, 3},
        1 <= len(intent) <= 2000,
        1 <= len(reason) <= 512,
        expected_file is not None and paths == [expected_file],
        intent.count("PI5B3G_CHILD_VERIFIED") == 1,
        FORBIDDEN.search(intent) is None,
        FORBIDDEN.search(reason) is None,
    )
    return "ALLOW" if all(checks) else "REJECT"


def main() -> int:
    payload = json.loads(CORPUS.read_text(encoding="utf-8"))
    rows = []
    for case in payload["cases"]:
        actual = historical_decision(case)
        rows.append(
            {
                "id": case["id"],
                "expected": case["decision"],
                "historical": actual,
                "matches_expected": actual == case["decision"],
            }
        )
    safe = [row for row in rows if row["expected"] == "ALLOW"]
    unsafe = [row for row in rows if row["expected"] == "REJECT"]
    report = {
        "schema_version": 1,
        "corpus_sha256": hashlib.sha256(CORPUS.read_bytes()).hexdigest(),
        "total": len(rows),
        "safe_total": len(safe),
        "unsafe_or_ambiguous_total": len(unsafe),
        "historical_false_rejections": [
            row["id"] for row in safe if not row["matches_expected"]
        ],
        "historical_false_acceptances": [
            row["id"] for row in unsafe if not row["matches_expected"]
        ],
        "matched": sum(row["matches_expected"] for row in rows),
        "rows": rows,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in report if key != "rows"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
