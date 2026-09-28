#!/usr/bin/env python3
"""Spike: Jev (TypeSafe System One) as the judgment layer in the pi dispatch path.

Verifies, against the live API:

1. A task issued in pi reaches the dispatcher and Jev picks the right
   execution target (model) for it.
2. The routing decision reacts to WEEKLY and FIVE_HOUR quota pressure
   carried on each candidate (mirrors ``DispatchCandidateInput.windows``).
3. Deterministic policy stays in code: hard gates run before Jev,
   confidence-gated fallback and defer advice run after it.

Candidate fixtures mirror the host's real plan topology
(docs.z.ai + minimax.io, verified 2026-09):

- ZAI GLM Coding Plan — one shared 5h + weekly pool serving glm-5.3
  (T3 flagship) and glm-5.3-flash (fast multimodal light tier);
  pressure on the pool presses both.
- MiniMax Token Plan — separate 5h rolling + weekly pool serving
  minimax-m3 (T3 frontier coding) and minimax-m2.7 (T2 previous-gen).
- OpenCode Zen free-model relay — UNMETERED, no windows
  (``quota_collectors/unmetered.py``).

Two T3 pools act as mutual hot failover: a pressed ZAI pool routes
T3 work to minimax-m3 with zero quality downgrade.

Layout follows the ``spikes/<topic>/results`` convention: raw answers are
written to ``results/jev_dispatch_results.json``.

Requires ``TYPESAFE_API_KEY`` in the environment; the key is never
persisted.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
NOW = datetime(2026, 9, 23, 14, 0, tzinfo=timezone(timedelta(hours=8)))

ROUTE_CONFIDENCE_FLOOR = 0.45
DEFER_ADVICE_THRESHOLD = 0.6

# ---------------------------------------------------------------------------
# Deterministic quota layer (simplified stand-in for quota_burn.assess)


def pressure_label(used_fraction: float) -> str:
    if used_fraction >= 0.95:
        return "CRITICAL"
    if used_fraction >= 0.85:
        return "HIGH"
    if used_fraction >= 0.60:
        return "MODERATE"
    return "LOW"


@dataclass(frozen=True)
class QuotaWindow:
    kind: str  # "FIVE_HOUR" | "WEEKLY"
    used_fraction: float
    reset_at: datetime

    def view(self) -> dict[str, Any]:
        delta = self.reset_at - NOW
        hours = max(delta.total_seconds(), 0) / 3600
        return {
            "used_fraction": round(self.used_fraction, 3),
            "pressure": pressure_label(self.used_fraction),
            "resets_in_hours": round(hours, 1),
        }


@dataclass(frozen=True)
class Candidate:
    execution_target_id: str
    model_sku_id: str
    tier: int  # 1..3, capability tier (T3 strongest)
    metering: str  # plan id or "unmetered_free_relay"
    windows: tuple[QuotaWindow, ...] = ()

    @property
    def pool(self) -> str:
        return self.metering if self.windows else "unmetered"

    def view(self) -> dict[str, Any]:
        return {
            "execution_target_id": self.execution_target_id,
            "model_sku_id": self.model_sku_id,
            "tier": f"T{self.tier}",
            "quota_pool": self.pool,
            "pool_shared": len(self.windows) > 0
            and sum(1 for other in ALL_TARGETS if other.metering == self.metering and other.windows)
            > 1,
            "quota": (
                {w.kind: w.view() for w in self.windows}
                if self.windows
                else "unmetered free relay, no windows"
            ),
        }

    def hard_gate(self) -> str | None:
        """Deterministic exclusion, runs before Jev (code owns policy)."""
        for w in self.windows:
            if w.used_fraction >= 1.0:
                return f"{w.kind} window exhausted"
        return None


def reset(kind: str, hours: float) -> datetime:
    return NOW + timedelta(hours=hours)


# Base topology: real plan pools. ZAI pool serves both glm SKUs and is
# shared; MiniMax pool is separate; Zen relay is unmetered.
_ZAI_5H = QuotaWindow("FIVE_HOUR", 0.35, reset("5h", 2.0))
_ZAI_WEEKLY = QuotaWindow("WEEKLY", 0.40, reset("weekly", 76.0))
_MM_5H = QuotaWindow("FIVE_HOUR", 0.30, reset("5h", 1.5))
_MM_WEEKLY = QuotaWindow("WEEKLY", 0.38, reset("weekly", 100.0))


def base_candidates() -> list[Candidate]:
    return [
        Candidate(
            execution_target_id="pi@zai-glm-5.3",
            model_sku_id="glm-5.3",
            tier=3,
            metering="zai_glm_coding_plan",
            windows=(_ZAI_5H, _ZAI_WEEKLY),
        ),
        Candidate(
            execution_target_id="pi@zai-glm-5.3-flash",
            model_sku_id="glm-5.3-flash",
            tier=1,
            metering="zai_glm_coding_plan",
            windows=(_ZAI_5H, _ZAI_WEEKLY),
        ),
        Candidate(
            execution_target_id="pi@minimax-m3",
            model_sku_id="minimax-m3",
            tier=3,
            metering="minimax_token_plan",
            windows=(_MM_5H, _MM_WEEKLY),
        ),
        Candidate(
            execution_target_id="pi@minimax-m2.7",
            model_sku_id="minimax-m2.7",
            tier=2,
            metering="minimax_token_plan",
            windows=(_MM_5H, _MM_WEEKLY),
        ),
        Candidate(
            execution_target_id="pi@opencode-zen",
            model_sku_id="zen-free-models",
            tier=1,
            metering="unmetered_free_relay",
        ),
    ]


ALL_TARGETS = base_candidates()


def patched(candidates: list[Candidate], pool: str, **kw: Any) -> list[Candidate]:
    """Patch every target sharing ``pool`` (pool-level quota is shared)."""
    windows = kw.pop("windows", None)
    out = []
    for c in candidates:
        if c.metering == pool and c.windows:
            merged = {**c.__dict__, **kw}
            if windows is not None:
                merged["windows"] = windows
            out.append(Candidate(**merged))
        else:
            out.append(c)
    return out


@dataclass
class Scenario:
    name: str
    task_prompt: str
    candidates: list[Candidate]
    expect: str  # expected execution_target_id(s), "|"-separated; or ANY
    note: str = ""


SCENARIOS: list[Scenario] = [
    Scenario(
        name="S1_trivial_healthy",
        task_prompt=(
            "pi task: 把 utils/datefmt.py 里的函数 `fmt_ts` 重命名为 "
            "`format_timestamp`,并更新同文件内全部调用点。"
        ),
        candidates=base_candidates(),
        expect="pi@zai-glm-5.3-flash|pi@minimax-m2.7",
        note=("低强度任务,配额健康 → 轻快档(flash / m2.7)即可;旗舰档留给高强度工作"),
    ),
    Scenario(
        name="S2_complex_healthy",
        task_prompt=(
            "pi task: 排查并修复登录会话在高并发下随机被登出的 bug,"
            "怀疑 session store 有竞态;需要跨 auth/、storage/ 多文件分析,"
            "设计加锁或乐观并发方案,并补回归测试。"
        ),
        candidates=base_candidates(),
        expect="pi@zai-glm-5.3|pi@minimax-m3",
        note="高强度任务,配额健康 → 两个 T3 旗舰池任一皆可",
    ),
    Scenario(
        name="S3_complex_zai_weekly_critical",
        task_prompt=(
            "pi task: 排查并修复登录会话在高并发下随机被登出的 bug,"
            "怀疑 session store 有竞态;需要跨 auth/、storage/ 多文件分析,"
            "设计加锁或乐观并发方案,并补回归测试。"
        ),
        candidates=patched(
            base_candidates(),
            "zai_glm_coding_plan",
            windows=(
                QuotaWindow("FIVE_HOUR", 0.35, reset("5h", 2.0)),
                QuotaWindow("WEEKLY", 0.93, reset("weekly", 76.0)),
            ),
        ),
        expect="pi@minimax-m3",
        note=(
            "高强度任务,ZAI 周限额 CRITICAL(93%)→ 池内降档无意义"
            "(flash 共享同池)→ 跨池切到同级 M3,零质量损失"
        ),
    ),
    Scenario(
        name="S4_complex_zai_5h_high",
        task_prompt=(
            "pi task: 排查并修复登录会话在高并发下随机被登出的 bug,"
            "怀疑 session store 有竞态;需要跨 auth/、storage/ 多文件分析,"
            "设计加锁或乐观并发方案,并补回归测试。"
        ),
        candidates=patched(
            base_candidates(),
            "zai_glm_coding_plan",
            windows=(
                QuotaWindow("FIVE_HOUR", 0.88, reset("5h", 1.5)),
                QuotaWindow("WEEKLY", 0.45, reset("weekly", 76.0)),
            ),
        ),
        expect="pi@minimax-m3",
        note="高强度任务,ZAI 5 小时窗 HIGH(88%)→ 5h 压力跨池切同级 M3",
    ),
    Scenario(
        name="S5_trivial_all_plans_pressed",
        task_prompt=("pi task: 在 README.md 的安装小节追加一行依赖说明:需要 python>=3.10。"),
        candidates=patched(
            patched(
                base_candidates(),
                "zai_glm_coding_plan",
                windows=(
                    QuotaWindow("FIVE_HOUR", 0.97, reset("5h", 2.0)),
                    QuotaWindow("WEEKLY", 0.96, reset("weekly", 76.0)),
                ),
            ),
            "minimax_token_plan",
            windows=(
                QuotaWindow("FIVE_HOUR", 0.91, reset("5h", 1.5)),
                QuotaWindow("WEEKLY", 0.94, reset("weekly", 100.0)),
            ),
        ),
        expect="pi@opencode-zen",
        note="两个付费池都 CRITICAL → 琐碎任务让给免费 Zen 中继",
    ),
    Scenario(
        name="S6_ambiguous_low_stakes",
        task_prompt="pi task: 帮我看看 build 脚本,感觉哪里不太对。",
        candidates=base_candidates(),
        expect="ANY",
        note="模糊任务 → 观察 task_tier 与置信度,验证兜底策略可用",
    ),
    Scenario(
        name="S7_zai_5h_exhausted",
        task_prompt=(
            "pi task: 重构 payment 模块,把同步调用全部改为带超时与重试的"
            "异步路径,并保持现有接口兼容。"
        ),
        candidates=patched(
            base_candidates(),
            "zai_glm_coding_plan",
            windows=(
                QuotaWindow("FIVE_HOUR", 1.0, reset("5h", 0.5)),
                QuotaWindow("WEEKLY", 0.71, reset("weekly", 76.0)),
            ),
        ),
        expect="pi@minimax-m2.7|pi@minimax-m3",
        note=(
            "ZAI 5h 窗耗尽 → 硬门同时拦截 glm-5.3 和 flash(共享池),"
            "MM 池承接;同池两档烧量相同,T2 任务 m2.7/m3 均可"
        ),
    ),
    Scenario(
        name="S8_t3_pinned_zai_weekly_critical",
        task_prompt=(
            "pi task: 对交易核心 settlement 流程做跨服务重构,涉及资金"
            " correctness,必须给出形式化不变量与完整回归证明。"
            "用户明确要求:仅 T3 级最强模型可执行,禁止任何降档。"
        ),
        candidates=patched(
            base_candidates(),
            "zai_glm_coding_plan",
            windows=(
                QuotaWindow("FIVE_HOUR", 0.55, reset("5h", 3.0)),
                QuotaWindow("WEEKLY", 0.96, reset("weekly", 30.0)),
            ),
        ),
        expect="pi@minimax-m3",
        note=(
            "T3 刚性 + ZAI 周限额 CRITICAL(96%)→ 存在健康同级 M3,"
            "验证 Jev 自身能否守住刚性约束(跨池同级接管,无需降档)"
        ),
    ),
    Scenario(
        name="S9_chore_no_healthy_alternative",
        task_prompt=(
            "pi task: 把今天改动里遗留的 3 个 TODO 注释整理成 "
            "docs/todo-backlog.md 清单,不改动任何代码逻辑。"
        ),
        candidates=[
            Candidate(
                execution_target_id="pi@zai-glm-5.3",
                model_sku_id="glm-5.3",
                tier=3,
                metering="zai_glm_coding_plan",
                windows=(
                    QuotaWindow("FIVE_HOUR", 0.94, reset("5h", 2.5)),
                    QuotaWindow("WEEKLY", 0.97, reset("weekly", 52.0)),
                ),
            ),
            Candidate(
                execution_target_id="pi@minimax-m3",
                model_sku_id="minimax-m3",
                tier=3,
                metering="minimax_token_plan",
                windows=(
                    QuotaWindow("FIVE_HOUR", 0.90, reset("5h", 1.0)),
                    QuotaWindow("WEEKLY", 0.97, reset("weekly", 52.0)),
                ),
            ),
        ],
        expect="ANY",
        note=(
            "低价值杂务 + 两个付费池全部 CRITICAL、无免费中继 → "
            "defer_acceptable 应升高,验证 defer 建议消费路径"
        ),
    ),
]

# ---------------------------------------------------------------------------
# Jev client (stdlib only; retry on 429/529 per API docs)


def jev_ask(state: Any, questions: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = json.dumps({"state": state, "model": MODEL, "questions": questions}).encode()
    last_err: Exception | None = None
    for attempt in range(4):
        req = urllib.request.Request(
            API_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {os.environ['TYPESAFE_API_KEY']}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.load(resp)
            latency_ms = round((time.perf_counter() - started) * 1000)
            return body, {"latency_ms": latency_ms}
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            if e.code in (429, 529) and attempt < 3:
                time.sleep(2**attempt)
                continue
            raise RuntimeError(f"HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            last_err = e
            time.sleep(2**attempt)
    raise RuntimeError(f"connection failed after retries: {last_err}")


# ---------------------------------------------------------------------------
# Question construction


OBJECTIVE = (
    "Pick the execution target that completes the task at required quality "
    "while conserving scarce quota. Rules: (1) meet the task's capability "
    "tier; T3-pinned work needs a T3 flagship — when one flagship pool is "
    "pressed, the other plan's flagship is the zero-downgrade failover. "
    "(2) Quota pools are shared across models on the same plan: when a "
    "pool's FIVE_HOUR or WEEKLY window is HIGH or CRITICAL, avoid every "
    "model on that pool, not just one — downshifting within a pressed "
    "pool saves nothing. (3) Prefer already-paid plan capacity while its "
    "pressure is LOW/MODERATE, matching the model tier to the task: "
    "routine work goes to the fast light tiers (glm-5.3-flash, "
    "minimax-m2.7), flagship burn is reserved for demanding work. "
    "(4) The unmetered free relay is the last resort for low-stakes work "
    "when both paid pools are pressed."
)

TIER_LEVELS = [
    "T0: single-file trivial edit, rename, doc tweak, or simple lookup; "
    "any capable coding model handles it",
    "T1: standard bugfix or small feature; needs solid general coding ability",
    "T2: complex multi-file change, subtle concurrency bug, or meaningful design tradeoffs",
    "T3: deep architectural reasoning, cross-system refactor, or "
    "verification-critical high-risk work",
]


def build_questions(admitted: list[Candidate]) -> dict[str, Any]:
    criteria: dict[str, str] = {}
    for c in admitted:
        parts = [
            f"{c.model_sku_id}, capability tier T{c.tier}, "
            f"pool {c.pool}"
            + (" (shared with other models on the same plan)" if c.view()["pool_shared"] else "")
        ]
        for w in c.windows:
            v = w.view()
            parts.append(
                f"{w.kind}: {v['pressure']} ({int(v['used_fraction'] * 100)}% used, "
                f"resets in {v['resets_in_hours']}h)"
            )
        if not c.windows:
            parts.append("quota: unmetered free relay (no windows)")
        criteria[c.execution_target_id] = "; ".join(parts)

    return {
        "task_tier": {
            "type": "score",
            "instructions": (
                "Rate the capability tier `task.prompt` requires. Judge the "
                "work itself, not the wording. Prefer the lower tier when "
                "the task is routine."
            ),
            "criteria": TIER_LEVELS,
        },
        "route": {
            "type": "choice",
            "instructions": {
                "objective": OBJECTIVE,
                "question": (
                    "Which `candidates[].execution_target_id` should execute "
                    "`task.prompt` right now? Weigh the task tier against each "
                    "candidate's pool pressure."
                ),
            },
            "criteria": criteria,
        },
        "defer_acceptable": {
            "type": "noul",
            "instructions": (
                "Speculative, used only under pressure: if every suitable "
                "target reports HIGH or CRITICAL quota windows, is deferring "
                "`task.prompt` until the nearest window resets acceptable for "
                "the user's workflow?"
            ),
        },
    }


# ---------------------------------------------------------------------------
# Post-Jev deterministic policy


def tier_floor(tier_score: float) -> int:
    """Capability floor implied by Jev's task_tier score (standard rounding)."""
    return min(3, max(1, int(tier_score + 0.5)))


def deterministic_pick(admitted: list[Candidate], tier_score: float) -> str:
    """Tier-aware fallback: honour the task-tier floor, then prefer
    unpressed paid pools; unmetered relay is the last resort."""

    floor = tier_floor(tier_score)
    pool = [c for c in admitted if c.tier >= floor] or admitted

    def rank(c: Candidate) -> tuple[Any, ...]:
        pressed = any(pressure_label(w.used_fraction) in ("HIGH", "CRITICAL") for w in c.windows)
        return (pressed, 0 if c.windows else 1, -c.tier)

    return sorted(pool, key=rank)[0].execution_target_id


def apply_policy(admitted: list[Candidate], answers: dict[str, Any]) -> dict[str, Any]:
    route = answers["route"]
    tier = answers["task_tier"]
    defer = answers["defer_acceptable"]

    decision = route["choice"]
    mode = "jev"
    reason = f"jev choice (confidence {route['confidence']:.2f})"

    if route["confidence"] < ROUTE_CONFIDENCE_FLOOR:
        decision = deterministic_pick(admitted, tier["score"])
        mode = "fallback_policy"
        reason = (
            f"jev confidence {route['confidence']:.2f} below floor "
            f"{ROUTE_CONFIDENCE_FLOOR} → tier-aware deterministic fallback"
        )

    # Post-Jev guard: Jev's own tier judgment outranks its routing choice —
    # a high-tier task must not land on a below-floor target (Jev weighs
    # pressure against explicit pins and may downshift anyway).
    chosen = next(c for c in admitted if c.execution_target_id == decision)
    floor = tier_floor(tier["score"])
    if chosen.tier < floor:
        decision = deterministic_pick(admitted, tier["score"])
        mode = "policy_override"
        reason = (
            f"jev chose T{chosen.tier} for a T{floor} task "
            f"(score {tier['score']:.1f}) → tier floor enforced in code"
        )

    chosen = next(c for c in admitted if c.execution_target_id == decision)
    defer_advice = None
    if (
        any(pressure_label(w.used_fraction) == "CRITICAL" for w in chosen.windows)
        and defer["noul"] > DEFER_ADVICE_THRESHOLD
    ):
        defer_advice = (
            f"chosen target's pool is CRITICAL-pressed and deferral looks "
            f"acceptable (noul {defer['noul']:.2f}) → consider queueing "
            f"until window reset"
        )

    return {
        "decision": decision,
        "mode": mode,
        "reason": reason,
        "task_tier": {
            "score": tier["score"],
            "legend": tier["legend"],
        },
        "route_probabilities": route["probabilities"],
        "defer_noul": defer["noul"],
        "defer_advice": defer_advice,
    }


# ---------------------------------------------------------------------------
# Runner


def main() -> int:
    api_key = os.environ.get("TYPESAFE_API_KEY")
    if not api_key:
        print("TYPESAFE_API_KEY not set", file=sys.stderr)
        return 2

    results: list[dict[str, Any]] = []
    total_input = total_output = 0
    passes = 0

    for sc in SCENARIOS:
        gated = {c.execution_target_id: c.hard_gate() for c in sc.candidates}
        admitted = [c for c in sc.candidates if not gated[c.execution_target_id]]

        state = {
            "task": {
                "source": "pi",
                "channel": "pi interactive session",
                "prompt": sc.task_prompt,
            },
            "now": NOW.isoformat(),
            "candidates": [c.view() for c in admitted],
            "excluded_by_hard_gates": {k: v for k, v in gated.items() if v},
        }

        body, meta = jev_ask(state, build_questions(admitted))
        usage = body["usage"]
        total_input += usage["input_tokens"]
        total_output += usage["output_tokens"]

        policy = apply_policy(admitted, body["answers"])
        ok = sc.expect == "ANY" or policy["decision"] in sc.expect.split("|")
        passes += ok

        row = {
            "scenario": sc.name,
            "note": sc.note,
            "excluded_by_hard_gates": state["excluded_by_hard_gates"],
            "model": body["model"],
            "expected": sc.expect,
            **policy,
            **meta,
            "usage": usage,
            "pass": ok,
        }
        results.append(row)

        print(
            f"[{'PASS' if ok else 'FAIL'}] {sc.name}: "
            f"{policy['decision']} ({policy['mode']}, "
            f"conf={body['answers']['route']['confidence']:.2f}, "
            f"tier={policy['task_tier']['score']:.1f}, "
            f"defer={policy['defer_noul']:.2f}, "
            f"{meta['latency_ms']}ms)"
        )

    summary = {
        "generated_at": NOW.isoformat(),
        "model": MODEL,
        "scenarios": results,
        "totals": {
            "requests": len(results),
            "passed": passes,
            "failed": len(results) - passes,
            "input_tokens": total_input,
            "output_tokens": total_output,
        },
    }

    out_path = os.path.join(os.path.dirname(__file__), "results")
    os.makedirs(out_path, exist_ok=True)
    out_file = os.path.join(out_path, "jev_dispatch_results.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(
        f"\n{passes}/{len(results)} passed | tokens: "
        f"{total_input} in / {total_output} out | results → {out_file}"
    )
    return 0 if passes == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
