/**
 * pao-dsh-dispatch core — Jev quota-aware model dispatch for DeepSeek
 * Harness. Plain ESM, zero runtime deps (Node 22 fetch + node builtins).
 *
 * Ported from spikes/jev-dispatch (run_spike.py / dsh) with the
 * burn-first economics objective: paid token-plan weekly quota is
 * use-it-or-lose-it, so routing maximizes utilization of paid pools
 * (weighted by remaining weekly fraction and reset imminence) and
 * touches the pay-as-you-go pool only when no paid pool is eligible.
 *
 * Key resolution chain (first hit wins):
 *   1. plugin config apiKeyEnv names → process.env
 *   2. $DSH_HOME/.credentials.yaml refs
 *   3. pi auth store (~/.pi/agent/auth.json)
 *   4. opencode auth store
 */

import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const CONFIDENCE_FLOOR = 0.45;
const DEFER_THRESHOLD = 0.6;
const POOL_MIN_BALANCE_CNY = 1.0;
const PRO_MIN_BALANCE_CNY = 10.0;

const ZAI_QUOTA_URL = "https://api.z.ai/api/monitor/usage/quota/limit";
const MM_QUOTA_URL = "https://api.minimaxi.com/v1/token_plan/remains";
const DS_BALANCE_URL = "https://api.deepseek.com/user/balance";
const JEV_URL = "https://api.typesafe.ai/v1/systemone";
const JEV_MODEL = "jev-latest";

// Default target table. Light-tier ids must exist in the host's installed
// model catalog: dsh's bundled pi-ai catalog (v0.84.x) predates
// glm-5.3-flash, so the zai light tier defaults to glm-5-turbo. Override
// via the plugin config `targets` array (same field names).
export const TARGETS = [
  { tid: "zai-glm-5.3", provider: "zai", model: "glm-5.3", tier: 3, pool: "zai_glm_coding_plan" },
  { tid: "zai-glm-light", provider: "zai", model: "glm-5-turbo", tier: 1, pool: "zai_glm_coding_plan" },
  { tid: "minimax-m3", provider: "minimax-cn", model: "MiniMax-M3", tier: 3, pool: "minimax_token_plan" },
  { tid: "minimax-m2.7", provider: "minimax-cn", model: "MiniMax-M2.7", tier: 2, pool: "minimax_token_plan" },
  { tid: "deepseek-v4-pro", provider: "deepseek", model: "deepseek-v4-pro", tier: 3, pool: "deepseek_paygo" },
  { tid: "deepseek-v4-flash", provider: "deepseek", model: "deepseek-v4-flash", tier: 1, pool: "deepseek_paygo" },
];

function dshHome() {
  return process.env.DSH_HOME ?? join(homedir(), ".dsh");
}

function readJson(file) {
  try {
    return JSON.parse(readFileSync(file, "utf-8"));
  } catch {
    return null;
  }
}

/**
 * Resolve provider keys. `envNames` maps provider id → env var name
 * (plugin config may override). dsh credentials refs are parsed
 * defensively without a YAML dependency.
 */
export function resolveKeys(envNames = {}) {
  const names = {
    zai: envNames.zai ?? "ZAI_API_KEY",
    "minimax-cn": envNames.minimax ?? "MINIMAX_CN_API_KEY",
    deepseek: envNames.deepseek ?? "DEEPSEEK_API_KEY",
  };
  const keys = {};
  for (const [provider, env] of Object.entries(names)) {
    if (process.env[env]) {
      keys[provider] = process.env[env];
      continue;
    }
    // dsh credentials refs: lines like "  NAME: value" under refs:
    try {
      const text = readFileSync(join(dshHome(), ".credentials.yaml"), "utf-8");
      const m = text.match(new RegExp(`^\\s+${env}:\\s*(\\S+)\\s*$`, "m"));
      if (m) {
        keys[provider] = m[1];
        continue;
      }
    } catch {
      /* absent */
    }
  }
  // pi auth store fallback
  const pi = readJson(join(homedir(), ".pi/agent/auth.json"));
  if (pi) {
    for (const p of ["zai", "minimax-cn", "deepseek"]) {
      if (!keys[p] && pi[p]?.key) keys[p] = pi[p].key;
    }
  }
  // opencode auth store fallback
  const oc =
    readJson(join(homedir(), "Library/Application Support/opencode/auth.json"))
    ?? readJson(join(homedir(), ".local/share/opencode/auth.json"));
  const ocMap = { zai: "zai-coding-plan", "minimax-cn": "minimax-cn-coding-plan", deepseek: "deepseek" };
  if (oc) {
    for (const [p, k] of Object.entries(ocMap)) {
      if (!keys[p] && oc[k]?.key) keys[p] = oc[k].key;
    }
  }
  return keys;
}

/** Provider routes configured in dsh settings (line-parsed, best effort). */
export function configuredProviders() {
  const ids = new Set();
  try {
    const text = readFileSync(join(dshHome(), "settings.yaml"), "utf-8");
    if (/^llm-deepseek:/m.test(text)) ids.add("deepseek");
    const section = text.split(/^llm-pi-ai:/m)[1]?.split(/^\S/m)[0] ?? "";
    const prov = section.split(/^  providers:/m)[1];
    if (prov) {
      for (const m of prov.matchAll(/^    ([a-z0-9-]+):/gm)) ids.add(m[1]);
    }
  } catch {
    /* absent settings — assume nothing configured */
  }
  if (process.env.DSH_DISPATCH_ALLOW_PROVIDERS) {
    for (const p of process.env.DSH_DISPATCH_ALLOW_PROVIDERS.split(",")) ids.add(p.trim());
  }
  return ids;
}

// ---------------------------------------------------------------------------
// Live probes

function pressure(used) {
  if (used >= 0.95) return "CRITICAL";
  if (used >= 0.85) return "HIGH";
  if (used >= 0.6) return "MODERATE";
  return "LOW";
}

async function getJson(url, key) {
  const res = await fetch(url, {
    headers: { Authorization: `Bearer ${key}`, "x-api-key": key },
    signal: AbortSignal.timeout(15_000),
  });
  let body = null;
  try {
    body = await res.json();
  } catch {
    /* non-json */
  }
  return { status: res.status, body };
}

async function probeZai(key) {
  try {
    const { status, body } = await getJson(ZAI_QUOTA_URL, key);
    if (status !== 200 || !body?.success) return null;
    const rows = body.data.limits
      .map((l) => ({ at: new Date(l.nextResetTime), used: l.percentage / 100 }))
      .sort((a, b) => a.at - b.at);
    const now = Date.now();
    return rows.map((r, i) => ({
      kind:
        i === 0 && r.at.getTime() - now <= 6 * 3600_000 ? "FIVE_HOUR" : i === 0 ? "SHORT_TERM" : "WEEKLY",
      used: r.used,
      pressure: pressure(r.used),
      resetInHours: Math.max(0, (r.at.getTime() - now) / 3600_000),
    }));
  } catch {
    return null;
  }
}

async function probeMinimax(key) {
  try {
    const { body } = await getJson(MM_QUOTA_URL, key);
    if (body?.base_resp?.status_code === 2062) return { inactive: body.base_resp.status_msg };
    return {};
  } catch {
    return {};
  }
}

async function probeDeepseek(key) {
  try {
    const { body } = await getJson(DS_BALANCE_URL, key);
    const cny = (body?.balance_infos ?? []).find((i) => i.currency === "CNY");
    if (!cny) return null;
    return { ok: Boolean(body?.is_available), balance: Number(cny.total_balance ?? 0) };
  } catch {
    return null;
  }
}

/** Burn-first value of a paid pool: waste-avoided score (code-owned math). */
function burnValue(windows) {
  const weekly = windows.find((w) => w.kind === "WEEKLY") ?? windows[windows.length - 1];
  if (!weekly) return 0;
  const remaining = Math.max(0, 1 - weekly.used);
  const urgency = 1 - Math.min(Math.max(weekly.resetInHours, 0) / 168, 1);
  return Math.round(remaining * (0.5 + 0.5 * urgency) * 1000) / 1000;
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
  "Pick the execution target that maximizes total value from the host's model subscriptions. Rules: " +
  "(1) Paid token plans are use-it-or-lose-it: unused weekly quota is money wasted at reset, so prefer paid pools while capacity remains, and among them prefer the one with the higher burn_value (remaining weekly fraction weighted by how soon the window resets). " +
  "(2) Inside the chosen paid pool, match the tier to the task — flagship models for demanding work, light fast tiers for routine work — so the same already-paid window serves both. " +
  "(3) Pool windows press every model on that pool: an exhausted or unavailable paid pool leaves only the alternatives. " +
  "(4) The pay-as-you-go pool (deepseek) spends real money per token: use it ONLY when no paid pool is eligible for the task's tier. " +
  "(5) Meet the task's capability tier — never route demanding work to a light tier.";

function candidateView(c, windows, dsBalance) {
  return {
    execution_target_id: `dsh:${c.tid}`,
    model_sku_id: c.model,
    tier: `T${c.tier}`,
    quota_pool: c.pool,
    quota: windows
      ? Object.fromEntries(
          windows.map((w) => [
            w.kind,
            {
              used_fraction: w.used,
              pressure: w.pressure,
              resets_in_hours: Math.round(w.resetInHours * 10) / 10,
            },
          ]),
        )
      : c.pool === "deepseek_paygo"
        ? `prepaid pay-as-you-go, balance ${dsBalance?.toFixed(2)} CNY`
        : "subscription (windows not readable)",
  };
}

function buildQuestions(admitted, dsBalance) {
  const criteria = {};
  for (const { target, windows } of admitted) {
    const parts = [
      `${target.model}, capability tier T${target.tier}, ${target.pool === "deepseek_paygo" ? "pay-as-you-go (real money)" : "paid subscription pool"}, pool ${target.pool}`,
    ];
    for (const w of windows ?? []) {
      parts.push(
        `${w.kind}: ${w.pressure} (${Math.round(w.used * 100)}% used, resets in ${w.resetInHours.toFixed(1)}h)`,
      );
    }
    const bv = burnValue(windows ?? []);
    if (bv > 0) parts.push(`burn_value: ${bv} (weekly quota to harvest before reset)`);
    if (target.pool === "deepseek_paygo" && dsBalance !== null) {
      parts.push(`prepaid balance: ${dsBalance.toFixed(2)} CNY (billed per token)`);
    }
    criteria[`dsh:${target.tid}`] = parts.join("; ");
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
          "Which `candidates[].execution_target_id` should execute `task.prompt` right now? Maximize paid-quota utilization while meeting the task tier.",
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

async function jevAsk(state, questions, apiKey) {
  let lastErr = null;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const res = await fetch(JEV_URL, {
        method: "POST",
        headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
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
// Deterministic policy

function tierFloor(score) {
  return Math.min(3, Math.max(1, Math.round(score)));
}

function isPressed(windows) {
  return (windows ?? []).some((w) => w.pressure === "HIGH" || w.pressure === "CRITICAL");
}

function deterministicPick(admitted, score) {
  const floor = tierFloor(score);
  let pool = admitted.filter((c) => c.target.tier >= floor);
  if (!pool.length) pool = admitted;
  return [...pool].sort((a, b) => {
    const pa = a.target.pool === "deepseek_paygo" ? 1 : 0; // paid pools first
    const pb = b.target.pool === "deepseek_paygo" ? 1 : 0;
    if (pa !== pb) return pa - pb;
    const xa = isPressed(a.windows) ? 1 : 0;
    const xb = isPressed(b.windows) ? 1 : 0;
    if (xa !== xb) return xa - xb;
    const ba = burnValue(a.windows ?? []);
    const bb = burnValue(b.windows ?? []);
    if (ba !== bb) return bb - ba; // burn more valuable quota first
    return b.target.tier - a.target.tier;
  })[0];
}

function applyPolicy(admitted, answers) {
  const route = answers.route;
  const tierScore = answers.task_tier.score;
  let decision = route.choice;
  let mode = "jev";
  let reason = `jev choice (confidence ${route.confidence.toFixed(2)})`;

  if (route.confidence < CONFIDENCE_FLOOR) {
    const pick = deterministicPick(admitted, tierScore);
    decision = `dsh:${pick.target.tid}`;
    mode = "fallback_policy";
    reason = `jev confidence ${route.confidence.toFixed(2)} below floor → burn-first deterministic fallback`;
  }

  const floor = tierFloor(tierScore);
  const chosen = admitted.find((c) => `dsh:${c.target.tid}` === decision);
  if (chosen && chosen.target.tier < floor) {
    const pick = deterministicPick(admitted, tierScore);
    decision = `dsh:${pick.target.tid}`;
    mode = "policy_override";
    reason = `jev chose T${chosen.target.tier} for a T${floor} task → tier floor enforced in code`;
  }

  const finalPick = admitted.find((c) => `dsh:${c.target.tid}` === decision);
  const deferAdvice =
    (finalPick?.windows ?? []).some((w) => w.pressure === "CRITICAL") &&
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
  };
}

// ---------------------------------------------------------------------------
// Entry

export async function dispatchTask(prompt, { keys, providers } = {}) {
  const apiKey = process.env.TYPESAFE_API_KEY;
  if (!apiKey) throw new Error("TYPESAFE_API_KEY not set — set it to enable Jev dispatch");
  const keyMap = keys ?? resolveKeys();
  const routes = providers ?? configuredProviders();
  const gates = {};

  const zaiWindows = keyMap.zai ? await probeZai(keyMap.zai) : null;
  if (keyMap.zai && !zaiWindows) gates["(zai pool)"] = "quota probe failed";
  const mm = keyMap["minimax-cn"] ? await probeMinimax(keyMap["minimax-cn"]) : { inactive: "no key" };
  if (mm.inactive) gates["(minimax pool)"] = mm.inactive;
  const ds = keyMap.deepseek ? await probeDeepseek(keyMap.deepseek) : null;

  const admitted = [];
  for (const t of TARGETS) {
    const tid = `dsh:${t.tid}`;
    if (!routes.has(t.provider)) {
      gates[tid] = `provider "${t.provider}" not configured in dsh`;
      continue;
    }
    if (t.pool === "zai_glm_coding_plan") {
      if (!zaiWindows) continue; // gated at pool level
      if (zaiWindows.some((w) => w.used >= 1.0)) {
        gates[tid] = "quota window exhausted";
        continue;
      }
      admitted.push({ target: t, windows: zaiWindows });
    } else if (t.pool === "minimax_token_plan") {
      if (mm.inactive) continue;
      admitted.push({ target: t, windows: null });
    } else {
      if (ds === null) gates[tid] = "balance unreadable";
      else if (!ds.ok || ds.balance < POOL_MIN_BALANCE_CNY)
        gates[tid] = `insufficient prepaid balance (${ds.balance.toFixed(2)} CNY)`;
      else if (t.model === "deepseek-v4-pro" && ds.balance < PRO_MIN_BALANCE_CNY)
        gates[tid] = `balance ${ds.balance.toFixed(2)} CNY below flagship floor — flash only`;
      else admitted.push({ target: t, windows: null });
    }
  }

  if (!admitted.length) {
    return { decision: null, reason: "no executable target", excluded: gates };
  }

  const state = {
    task: { source: "dsh", prompt },
    now: new Date().toISOString(),
    economics: "burn-first: maximize utilization of paid weekly quota; paygo is last resort",
    candidates: admitted.map((c) => candidateView(c, c.windows, ds?.balance ?? null)),
    excluded_by_hard_gates: gates,
  };

  const body = await jevAsk(state, buildQuestions(admitted, ds?.balance ?? null), apiKey);
  const policy = applyPolicy(admitted, body.answers);

  return {
    ...policy,
    quotaZai: zaiWindows ?? undefined,
    deepseekBalanceCny: ds?.balance ?? null,
    excluded: Object.keys(gates).length ? gates : undefined,
  };
}
