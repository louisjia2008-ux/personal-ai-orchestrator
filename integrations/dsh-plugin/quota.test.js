import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { dispatchTask } from "./dispatch.js";

const valid = () => ({ base_resp: { status_code: 0 }, model_remains: [{
  model: "general", current_interval_remaining_percent: 80, current_weekly_remaining_percent: 70,
  end_time: Date.now() + 2 * 3600000, weekly_end_time: Date.now() + 100 * 3600000,
}] });

async function route(t, provider, body, status = 200) {
  const home = mkdtempSync(join(tmpdir(), "pao-quota-"));
  const oldHome = process.env.HOME;
  const oldKey = process.env.TYPESAFE_API_KEY;
  process.env.HOME = home;
  process.env.TYPESAFE_API_KEY = "offline-fixture";
  mkdirSync(join(home, ".pi/agent"), { recursive: true });
  writeFileSync(join(home, ".pi/agent/auth.json"), JSON.stringify({ [provider]: { key: "fixture", type: "api_key" } }));
  let decisions = 0;
  const mock = t.mock.method(globalThis, "fetch", async (url, init) => {
    if (String(url).includes("typesafe")) {
      decisions++;
      const state = JSON.parse(init.body).state;
      return Response.json({ answers: {
        route: { choice: state.candidates[0].execution_target_id, confidence: 0.9 }, task_tier: { score: 3 },
      } });
    }
    if (body === "network-error") throw new Error("offline");
    return body === "non-json" ? new Response("unreadable", { status }) : Response.json(body, { status });
  });
  try {
    const result = await dispatchTask("complex verification-critical task", { keys: { [provider]: "fixture" }, providers: new Set([provider]) });
    return { result, decisions };
  } finally {
    mock.mock.restore();
    if (oldHome === undefined) delete process.env.HOME; else process.env.HOME = oldHome;
    if (oldKey === undefined) delete process.env.TYPESAFE_API_KEY; else process.env.TYPESAFE_API_KEY = oldKey;
    rmSync(home, { recursive: true, force: true });
  }
}

test("MiniMax errors and unknown/exhausted coding windows cannot authorize execution", async (t) => {
  const exhausted = valid(); exhausted.model_remains[0].current_weekly_remaining_percent = 0;
  const video = valid(); video.model_remains[0].model = "video";
  const malformed = valid(); malformed.model_remains[0].current_interval_remaining_percent = "80";
  const invalidPlan = { ...valid(), current_interval_total_count: 0, current_interval_usage_count: 1 };
  for (const [body, status] of [[valid(), 401], [valid(), 503], [{}, 200], ["network-error", 200],
      ["non-json", 200], [{ base_resp: { status_code: 2062 } }, 200],
      [exhausted, 200], [video, 200], [malformed, 200], [invalidPlan, 200]]) {
    const { result, decisions } = await route(t, "minimax-cn", body, status);
    assert.equal(result.action, "BLOCK");
    assert.equal(result.target, undefined);
    assert.equal(decisions, 0);
  }
  const { result, decisions } = await route(t, "minimax-cn", valid());
  assert.equal(result.action, "ALLOW");
  assert.equal(result.target.provider, "minimax-cn");
  assert.equal(decisions, 1);
  const plan = { base_resp: { status_code: 0 }, data: {
    current_interval_total_count: 100, current_interval_usage_count: 20,
    current_interval_reset_time: new Date(Date.now() + 3600000).toISOString(),
    current_weekly_total_count: 1000, current_weekly_usage_count: 200,
    current_weekly_reset_time: new Date(Date.now() + 100 * 3600000).toISOString(),
  } };
  assert.equal((await route(t, "minimax-cn", plan)).result.action, "ALLOW");
});

test("ZAI needs nonempty finite future quota windows", async (t) => {
  const good = { success: true, data: { limits: [{ percentage: 20, nextResetTime: Date.now() + 3600000 }] } };
  for (const limits of [[], [{ percentage: "20", nextResetTime: Date.now() + 3600000 }],
      [{ percentage: -1, nextResetTime: Date.now() + 3600000 }],
      [{ percentage: 20, nextResetTime: "invalid" }], [{ percentage: 20, nextResetTime: 1 }]]) {
    assert.equal((await route(t, "zai", { success: true, data: { limits } })).result.action, "BLOCK");
  }
  assert.equal((await route(t, "zai", good)).result.action, "ALLOW");
});

test("DeepSeek rejects malformed balances and cannot downgrade T3 work to flash", async (t) => {
  const body = (balance) => ({ is_available: true, balance_infos: [{ currency: "CNY", total_balance: balance }] });
  for (const value of ["NaN", "Infinity", "", -1, 0, 5]) {
    const { result } = await route(t, "deepseek", body(value));
    assert.equal(result.action, "BLOCK");
    assert.equal(result.target, undefined);
  }
  assert.equal((await route(t, "deepseek", body(50))).result.action, "ALLOW");
});
