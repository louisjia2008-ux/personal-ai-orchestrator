/**
 * dsh dispatch core — TypeScript port of spikes/jev-dispatch (run_spike.py
 * + dsh). Self-contained: Node 22 globals (fetch) + node builtins only.
 *
 * Layers (same semantics as the verified Python spike):
 *   1. live probes: ZAI quota windows, MiniMax token-plan status,
 *      DeepSeek prepaid balance; credentials come from pi's own auth store
 *   2. hard gates: exhausted windows / inactive plan / no key /
 *      insufficient paygo balance
 *   3. Jev decision: one request, three parallel questions
 *   4. policy: confidence floor fallback, tier-floor override, defer advice
 */

import { readFileSync } from "node:fs";
import { homedir } from "node:os";

// ---------------------------------------------------------------------------
// Config

const CONFIDENCE_FLOOR = 0.45;
const DEFER_THRESHOLD = 0.6;
const POOL_MIN_BALANCE_CNY = 1.0; // below → whole deepseek pool gated
const PRO_MIN_BALANCE_CNY = 10.0; // below → v4-pro gated, flash stays

const ZAI_QUOTA_URL = "https://api.z.ai/api/monitor/usage/quota/limit";
const MM_QUOTA_URL = "https://api.minimaxi.com/v1/token_plan/remains";
const DS_BALANCE_URL = "https://api.deepseek.com/user/balance";
const JEV_URL = "https://api.typesafe.ai/v1/systemone";
const JEV_MODEL = "jev-latest";

export interface Target {
  tid: string;
  provider: string;
  model: string;
  tier: number;
  pool: string;
}

export const TARGETS: Target[] = [
  { tid: "zai-glm-5.3", provider: "zai", model: "glm-5.3", tier: 3, pool: "zai_glm_coding_plan" },
  { tid: "zai-glm-5.3-flash", provider: "zai", model: "glm-5.3-flash", tier: 1, pool: "zai_glm_coding_plan" },
  { tid: "minimax-m3", provider: "minimax-cn", model: "MiniMax-M3", tier: 3, pool: "minimax_token_plan" },
  { tid: "minimax-m2.7", provider: "minimax-cn", model: "MiniMax-M2.7", tier: 2, pool: "minimax_token_plan" },
  { tid: "deepseek-v4-pro", provider: "deepseek", model: "deepseek-v4-pro", tier: 3, pool: "deepseek_paygo" },
  { tid: "deepseek-v4-flash", provider: "deepseek", model: "deepseek-v4-flash", tier: 1, pool: "deepseek_paygo" },
];

// ---------------------------------------------------------------------------
// Quota model

export type Pressure = "LOW" | "MODERATE" | "HIGH" | "CRITICAL";

export interface QuotaWindow {
  kind: string;
  used: number;
  pressure: Pressure;
  resetInHours: number;
}

function pressure(used: number): Pressure {
  if (used >= 0.95) return "CRITICAL";
  if (used >= 0.85) return "HIGH";
  if (used >= 0.6) return "MODERATE";
  return "LOW";
}

interface Admitted {
  target: Target;
  windows: QuotaWindow[]; // empty = subscription without probe / paygo
  metering: string;
}

// ---------------------------------------------------------------------------
// Live probes (pi auth store: ~/.pi/agent/auth.json)

function loadPiAuth(): Record<string, { type: string; key?: string }> {
  try {
    return JSON.parse(
      readFileSync(`${homedir()}/.pi/agent/auth.json`, "utf-8"),
    );
  } catch {
    return {};
  }
}

async function getJson(
  url: string,
  key: string,
): Promise<{ status: number; body: any }> {
  const res = await fetch(url, {
    headers: { Authorization: `Bearer ${key}`, "x-api-key": key },
    signal: AbortSignal.timeout(15_000),
  });
  let body: any = null;
  try {
    body = await res.json();
  } catch {
    /* non-json */
  }
  return { status: res.status, body };
}

async function probeZai(key: string): Promise<QuotaWindow[] | null> {
  try {
    const { status, body } = await getJson(ZAI_QUOTA_URL, key);
    if (status !== 200 || !body?.success) return null;
    const rows: { nextResetTime: number; percentage: number }[] =
      body.data.limits
        .map((l: any) => ({
          at: new Date(l.nextResetTime),
          used: l.percentage / 100,
        }))
        .sort((a: any, b: any) => a.at - b.at);
    const now = Date.now();
    return rows.map((r: any, i: number) => ({
      kind:
        i === 0 && r.at.getTime() - now <= 6 * 3600_000
          ? "FIVE_HOUR"
          : i === 0
            ? "SHORT_TERM"
            : "WEEKLY",
      used: r.used,
      pressure: pressure(r.used),
      resetInHours: Math.max(0, (r.at.getTime() - now) / 3600_000),
    }));
  } catch {
    return null;
  }
}

async function probeMinimax(
  key: string,
): Promise<{ inactive?: string }> {
  try {
    const { body } = await getJson(MM_QUOTA_URL, key);
    const code = body?.base_resp?.status_code;
    if (code === 2062) return { inactive: body.base_resp.status_msg };
    return {};
  } catch {
    return {}; // unreadable but not proven inactive → include, no windows
  }
}

async function probeDeepseek(
  key: string,
): Promise<{ ok: boolean; balance: number } | null> {
  try {
    const { body } = await getJson(DS_BALANCE_URL, key);
    const cny = (body?.balance_infos ?? []).find(
      (i: any) => i.currency === "CNY",
    );
    if (!cny) return null;
    return {
      ok: Boolean(body?.is_available),
      balance: Number(cny.total_balance ?? 0),
    };
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Jev decision

const TIER_LEVELS = [
  "T0: single-file trivial edit, rename, doc tweak, or simple lookup; any capable coding model handles it",
  "T1: standard bugfix or small feature; needs solid general coding ability",
  "T2: complex multi-file change, subtle concurrency bug, or meaningful design tradeoffs",
  "T3: deep architectural reasoning, cross-system refactor, or verification-critical high-risk work",
];

const OBJECTIVE =
  "Pick the execution target that completes the task at required quality with the best economics. Rules: " +
  "(1) meet the task's capability tier; when a flagship pool is pressed, another plan's flagship is the zero-downgrade failover. " +
  "(2) Subscription pools are shared across their models: HIGH/CRITICAL windows press every model on that pool — downshifting within a pressed pool saves nothing. " +
  "(3) Subscriptions are already paid: prefer them while pressure is LOW/MODERATE, with light tiers for routine work and flagships for demanding work. " +
  "(4) The paygo pool (deepseek) spends real money per token: use it when subscription pools are pressed or unavailable, or when its flagship is the only healthy T3; avoid it for trivial work a healthy subscription covers.";

function candidateView(c: Admitted, dsBalance: number | null): any {
  return {
    execution_target_id: `pi@${c.target.tid}`,
    model_sku_id: c.target.model,
    tier: `T${c.target.tier}`,
    quota_pool: c.target.pool,
    quota:
      c.windows.length > 0
        ? Object.fromEntries(
            c.windows.map((w) => [
              w.kind,
              {
                used_fraction: w.used,
                pressure: w.pressure,
                resets_in_hours: Math.round(w.resetInHours * 10) / 10,
              },
            ]),
          )
        : c.metering === "api_paygo"
          ? `prepaid pay-as-you-go, balance ${dsBalance?.toFixed(2)} CNY`
          : "subscription (windows not probed)",
  };
}

function buildQuestions(
  admitted: Admitted[],
  dsBalance: number | null,
): any {
  const criteria: Record<string, string> = {};
  for (const c of admitted) {
    const parts = [
      `${c.target.model}, capability tier T${c.target.tier}, ${c.windows.length ? "subscription" : c.metering === "api_paygo" ? "pay-as-you-go" : "subscription"}, pool ${c.target.pool}`,
    ];
    for (const w of c.windows) {
      parts.push(
        `${w.kind}: ${w.pressure} (${Math.round(w.used * 100)}% used, resets in ${w.resetInHours.toFixed(1)}h)`,
      );
    }
    if (c.metering === "api_paygo" && dsBalance !== null) {
      parts.push(`prepaid balance: ${dsBalance.toFixed(2)} CNY (billed per token)`);
    }
    criteria[`pi@${c.target.tid}`] = parts.join("; ");
  }
  return {
    task_tier: {
      type: "score",
      instructions:
        "Rate the capability tier `task.prompt` requires. Judge the work itself, not the wording. Prefer the lower tier when the task is routine.",
      criteria: TIER_LEVELS,
    },
    route: {
      type: "choice",
      instructions: {
        objective: OBJECTIVE,
        question:
          "Which `candidates[].execution_target_id` should execute `task.prompt` right now? Weigh task tier, pool pressure, and real-money cost.",
      },
      criteria,
    },
    defer_acceptable: {
      type: "noul",
      instructions:
        "Speculative, used only under pressure: if every suitable target reports HIGH or CRITICAL quota windows, is deferring `task.prompt` until the nearest window resets acceptable for the user's workflow?",
    },
  };
}

async function jevAsk(state: any, questions: any): Promise<any> {
  const key = process.env.TYPESAFE_API_KEY;
  if (!key) throw new Error("TYPESAFE_API_KEY not set");
  let lastErr: unknown = null;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const res = await fetch(JEV_URL, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${key}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ state, model: JEV_MODEL, questions }),
        signal: AbortSignal.timeout(30_000),
      });
      if (res.status === 429 || res.status === 529) {
        await new Promise((r) => setTimeout(r, 2 ** attempt * 1000));
        continue;
      }
      if (!res.ok) throw new Error(`jev HTTP ${res.status}: ${await res.text()}`);
      return await res.json();
    } catch (e) {
      lastErr = e;
      await new Promise((r) => setTimeout(r, 2 ** attempt * 1000));
    }
  }
  throw lastErr instanceof Error ? lastErr : new Error(String(lastErr));
}

// ---------------------------------------------------------------------------
// Policy (deterministic, in code — Jev never owns hard rules)

function tierFloor(score: number): number {
  return Math.min(3, Math.max(1, Math.round(score)));
}

function isPressed(c: Admitted): boolean {
  return c.windows.some((w) => w.pressure === "HIGH" || w.pressure === "CRITICAL");
}

function deterministicPick(admitted: Admitted[], score: number): Admitted {
  const floor = tierFloor(score);
  const pool = admitted.filter((c) => c.target.tier >= floor);
  const list = pool.length ? pool : admitted;
  return [...list].sort((a, b) => {
    const pa = isPressed(a) ? 1 : 0;
    const pb = isPressed(b) ? 1 : 0;
    if (pa !== pb) return pa - pb;
    const wa = a.windows.length ? 0 : 1; // paid pools before paygo
    const wb = b.windows.length ? 0 : 1;
    if (wa !== wb) return wa - wb;
    return b.target.tier - a.target.tier;
  })[0];
}

function applyPolicy(admitted: Admitted[], answers: any): any {
  const route = answers.route;
  const tierScore = answers.task_tier.score;
  let decision = route.choice;
  let mode = "jev";
  let reason = `jev choice (confidence ${route.confidence.toFixed(2)})`;

  if (route.confidence < CONFIDENCE_FLOOR) {
    const pick = deterministicPick(admitted, tierScore);
    decision = `pi@${pick.target.tid}`;
    mode = "fallback_policy";
    reason = `jev confidence ${route.confidence.toFixed(2)} below floor → deterministic fallback`;
  }

  const floor = tierFloor(tierScore);
  const chosen = admitted.find((c) => `pi@${c.target.tid}` === decision);
  if (chosen && chosen.target.tier < floor) {
    const pick = deterministicPick(admitted, tierScore);
    decision = `pi@${pick.target.tid}`;
    mode = "policy_override";
    reason = `jev chose T${chosen.target.tier} for a T${floor} task → tier floor enforced in code`;
  }

  const finalPick = admitted.find((c) => `pi@${c.target.tid}` === decision)!;
  const deferAdvice =
    finalPick.windows.some((w) => w.pressure === "CRITICAL") &&
    answers.defer_acceptable.noul > DEFER_THRESHOLD
      ? `chosen pool is CRITICAL-pressed and deferral looks acceptable (noul ${answers.defer_acceptable.noul.toFixed(2)}) → consider queueing until reset`
      : null;

  return {
    decision,
    target: finalPick.target,
    mode,
    reason,
    tierScore,
    confidence: route.confidence,
    deferAdvice,
    probabilities: route.probabilities,
  };
}

// ---------------------------------------------------------------------------
// Entry

export interface DispatchResult {
  decision: string | null;
  target?: Target;
  mode?: string;
  reason?: string;
  tierScore?: number;
  confidence?: number;
  deferAdvice?: string | null;
  quotaZai?: QuotaWindow[];
  deepseekBalanceCny?: number | null;
  excluded?: Record<string, string>;
}

export async function dispatchTask(prompt: string): Promise<DispatchResult> {
  const auth = loadPiAuth();
  const gates: Record<string, string> = {};

  const zaiWindows = auth["zai"]?.key ? await probeZai(auth["zai"].key!) : null;
  if (auth["zai"]?.key && !zaiWindows) gates["(zai pool)"] = "quota probe failed";
  const mm = auth["minimax-cn"]?.key ? await probeMinimax(auth["minimax-cn"].key!) : { inactive: "no api key in pi" };
  if (mm.inactive) gates["(minimax pool)"] = mm.inactive;
  const ds = auth["deepseek"]?.key ? await probeDeepseek(auth["deepseek"].key!) : null;

  const admitted: Admitted[] = [];
  for (const t of TARGETS) {
    const tid = `pi@${t.tid}`;
    if (!auth[t.provider]?.key) {
      gates[tid] = "no api key configured in pi";
      continue;
    }
    if (t.pool === "zai_glm_coding_plan") {
      if (!zaiWindows) continue; // reason already gated at pool level
      if (zaiWindows.some((w) => w.used >= 1.0)) {
        gates[tid] = "quota window exhausted";
        continue;
      }
      admitted.push({ target: t, windows: zaiWindows, metering: "subscription" });
    } else if (t.pool === "minimax_token_plan") {
      if (mm.inactive) continue; // reason already gated at pool level
      admitted.push({ target: t, windows: [], metering: "subscription" });
    } else {
      if (ds === null) gates[tid] = "balance unreadable";
      else if (!ds.ok || ds.balance < POOL_MIN_BALANCE_CNY)
        gates[tid] = `insufficient prepaid balance (${ds.balance.toFixed(2)} CNY)`;
      else if (t.model === "deepseek-v4-pro" && ds.balance < PRO_MIN_BALANCE_CNY)
        gates[tid] = `balance ${ds.balance.toFixed(2)} CNY below flagship floor — flash only`;
      else admitted.push({ target: t, windows: [], metering: "api_paygo" });
    }
  }

  if (!admitted.length) {
    return { decision: null, reason: "no executable target", excluded: gates };
  }

  const state = {
    task: { source: "pi-dsh", prompt },
    now: new Date().toISOString(),
    candidates: admitted.map((c) => candidateView(c, ds?.balance ?? null)),
    excluded_by_hard_gates: gates,
  };

  const body = await jevAsk(state, buildQuestions(admitted, ds?.balance ?? null));
  const policy = applyPolicy(admitted, body.answers);

  return {
    ...policy,
    quotaZai: zaiWindows ?? undefined,
    deepseekBalanceCny: ds?.balance ?? null,
    excluded: Object.keys(gates).length ? gates : undefined,
  };
}
