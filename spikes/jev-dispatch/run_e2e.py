#!/usr/bin/env python3
"""E2E: a task issued in pi -> live quota probe -> Jev decision -> real
model execution on the chosen target.

Closes the loop the decision-layer spike
(``run_spike.py``) left open: the model Jev picks actually executes the
task and returns output.

Live inputs:

- Credentials: read at runtime from the OpenCode auth store
  (``~/.local/share/opencode/auth.json``); never copied anywhere.
- ZAI quota: ``GET https://api.z.ai/api/monitor/usage/quota/limit``
  (5h + weekly windows; sooner reset = FIVE_HOUR).
- MiniMax quota: ``GET https://api.minimaxi.com/v1/token_plan/remains``;
  ``status_code 2062`` (no active subscription) hard-gates the pool with
  the real reason.
- Execution: ZAI Anthropic-compatible endpoint
  ``https://api.z.ai/api/anthropic/v1/messages`` with the coding-plan key.

Scenario C injects synthetic CRITICAL pressure on ZAI to demonstrate
reactivity against a live otherwise-healthy pool; its execution step is
marked accordingly when no executable target remains.

Requires ``TYPESAFE_API_KEY`` in the environment.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from run_spike import (  # noqa: E402
    Candidate,
    QuotaWindow,
    apply_policy,
    build_questions,
    jev_ask,
    patched,
)

AUTH_PATH = os.path.expanduser(
    "~/Library/Application Support/opencode/auth.json"
)
AUTH_PATH_FALLBACK = os.path.expanduser("~/.local/share/opencode/auth.json")
ZAI_QUOTA_URL = "https://api.z.ai/api/monitor/usage/quota/limit"
MM_QUOTA_URL = "https://api.minimaxi.com/v1/token_plan/remains"
ZAI_MESSAGES_URL = "https://api.z.ai/api/anthropic/v1/messages"


def load_keys() -> dict[str, str]:
    for path in (AUTH_PATH, AUTH_PATH_FALLBACK):
        if os.path.exists(path):
            auth = json.load(open(path))
            return {
                "zai": auth["zai-coding-plan"]["key"],
                "minimax": auth["minimax-cn-coding-plan"]["key"],
            }
    raise SystemExit("opencode auth store not found")


def http_get(url: str, key: str) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {key}", "x-api-key": key}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode(errors="replace"))


def probe_zai(key: str) -> tuple[QuotaWindow, ...] | None:
    status, body = http_get(ZAI_QUOTA_URL, key)
    if status != 200 or not body.get("success"):
        return None
    windows = []
    for lim in body["data"]["limits"]:
        reset_at = datetime.fromtimestamp(
            lim["nextResetTime"] / 1000, tz=timezone.utc
        )
        windows.append((reset_at, lim["percentage"] / 100))
    windows.sort()  # sooner reset first
    # Label by plausibility: a 5h rolling window always resets within ~5h;
    # ZAI lite's shorter credit window resets on a multi-hour/day boundary.
    kinds = [
        "FIVE_HOUR" if (windows[0][0] - datetime.now(timezone.utc)).total_seconds() <= 6 * 3600
        else "SHORT_TERM",
        "WEEKLY",
    ]
    return tuple(
        QuotaWindow(kind, used, reset)
        for kind, (reset, used) in zip(kinds, windows)
    )


def probe_minimax(key: str) -> tuple[QuotaWindow, ...] | None:
    """Return windows, or None with the real reason recorded."""

    status, body = http_get(MM_QUOTA_URL, key)
    status_code = body.get("base_resp", {}).get("status_code")
    if status_code == 2062:
        MM_INACTIVE["reason"] = body["base_resp"]["status_msg"]
        return None
    if status != 200:
        return None
    return ()  # active but shape unknown — treat as no windows


MM_INACTIVE: dict[str, str] = {}


def execute_on_zai(
    key: str, model: str, task: str
) -> tuple[str, int]:
    payload = json.dumps(
        {
            "model": model,
            # glm-5.3 thinks at max intensity by default; flagships need
            # a larger budget so thinking doesn't consume the whole cap.
            "max_tokens": 8192 if not model.endswith("flash") else 1600,
            "messages": [{"role": "user", "content": task}],
        }
    ).encode()
    req = urllib.request.Request(
        ZAI_MESSAGES_URL,
        data=payload,
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=300) as r:
        body = json.load(r)
    text = "".join(
        b.get("text", "") for b in body.get("content", [])
    ).strip()
    return text, round((time.perf_counter() - started) * 1000)


TASKS: list[dict[str, str]] = [
    {
        "name": "A_complex_live_quota",
        "task": (
            "用 Python 写一个线程安全的滑动窗口限流器类 "
            "SlidingWindowLimiter(构造参数 rate/period),"
            "给出 allow() 方法与一行使用示例。只输出代码。"
        ),
        "note": "真实配额状态下的中高强度任务;限流器属标准组件,Jev 分级若为 T1–T2 选 flash 也属正确判断",
    },
    {
        "name": "B_trivial_live_quota",
        "task": (
            "给下面这行 Shell 写一句注释说明作用:"
            "find . -name '*.pyc' -delete。只输出注释。"
        ),
        "note": "真实配额状态下的低强度任务 → 应选 glm-5.3-flash 执行",
    },
    {
        "name": "C_t3_synthetic_zai_critical",
        "task": (
            "设计一个跨服务的幂等支付入账方案,要求给出核心不变量与"
            "失败恢复路径。用要点提纲,总共不超过 300 字。"
            "仅 T3 最强模型可执行,禁止降档。"
        ),
        "note": (
            "注入 ZAI 周 CRITICAL + MM 无订阅(真实)→ 无可执行目标,"
            "应触发 defer/兜底路径"
        ),
    },
]


def main() -> int:
    if not os.environ.get("TYPESAFE_API_KEY"):
        print("TYPESAFE_API_KEY not set", file=sys.stderr)
        return 2
    keys = load_keys()

    zai_windows = probe_zai(keys["zai"])
    if zai_windows is None:
        print("ZAI quota probe failed", file=sys.stderr)
        return 2
    probe_minimax(keys["minimax"])

    def live_candidates() -> list[Candidate]:
        cands = [
            Candidate(
                execution_target_id="pi@zai-glm-5.3",
                model_sku_id="glm-5.3",
                tier=3,
                metering="zai_glm_coding_plan",
                windows=zai_windows,
            ),
            Candidate(
                execution_target_id="pi@zai-glm-5.3-flash",
                model_sku_id="glm-5.3-flash",
                tier=1,
                metering="zai_glm_coding_plan",
                windows=zai_windows,
            ),
            Candidate(
                execution_target_id="pi@opencode-zen",
                model_sku_id="zen-free-models",
                tier=1,
                metering="unmetered_free_relay",
            ),
        ]
        if not MM_INACTIVE:
            cands += [
                Candidate(
                    execution_target_id="pi@minimax-m3",
                    model_sku_id="minimax-m3",
                    tier=3,
                    metering="minimax_token_plan",
                    windows=(),
                ),
            ]
        return cands

    results = []
    for t in TASKS:
        cands = live_candidates()
        if t["name"].startswith("C_"):
            cands = patched(
                cands,
                "zai_glm_coding_plan",
                windows=(
                    zai_windows[0],
                    QuotaWindow("WEEKLY", 0.96, zai_windows[1].reset_at),
                ),
            )

        gated = {c.execution_target_id: c.hard_gate() for c in cands}
        if MM_INACTIVE:
            gated["pi@minimax-m3 (pool)"] = MM_INACTIVE["reason"]
        admitted = [
            c for c in cands if not gated[c.execution_target_id]
        ]

        state = {
            "task": {"source": "pi", "prompt": t["task"]},
            "now": datetime.now(timezone.utc).isoformat(),
            "candidates": [c.view() for c in admitted],
            "excluded_by_hard_gates": {k: v for k, v in gated.items() if v},
        }
        body, meta = jev_ask(state, build_questions(admitted))
        policy = apply_policy(admitted, body["answers"])

        exec_note = ""
        output = ""
        chosen = policy["decision"]
        if chosen.startswith("pi@zai-"):
            model = next(
                c.model_sku_id
                for c in admitted
                if c.execution_target_id == chosen
            )
            output, exec_ms = execute_on_zai(keys["zai"], model, t["task"])
            exec_note = f"executed on {model} in {exec_ms}ms"
        elif chosen == "pi@opencode-zen":
            exec_note = "would relay via opencode zen (not executed here)"
        else:
            exec_note = "no executable target (defer path)"

        row = {
            "scenario": t["name"],
            "note": t["note"],
            "decision": chosen,
            "mode": policy["mode"],
            "confidence": round(
                body["answers"]["route"]["confidence"], 2
            ),
            "task_tier": policy["task_tier"]["score"],
            "defer_noul": policy["defer_noul"],
            "defer_advice": policy["defer_advice"],
            "excluded": state["excluded_by_hard_gates"],
            "execution": exec_note,
            "output_excerpt": output[:200],
            **meta,
        }
        results.append(row)
        print(
            f"{t['name']}: {chosen} ({policy['mode']}, "
            f"conf={row['confidence']}, tier={row['task_tier']:.1f}) "
            f"-> {exec_note}"
        )
        if output:
            print(f"  output: {output[:120]!r}")

    out = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "results",
        "jev_dispatch_e2e_results.json",
    )
    snapshot = {
        "zai_windows": [w.view() for w in zai_windows],
        "minimax": MM_INACTIVE or "active",
        "runs": results,
    }
    json.dump(snapshot, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\nresults -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
