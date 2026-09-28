import { test } from "node:test";
import assert from "node:assert/strict";
import { TARGETS, applyPolicy, fallbackPolicy, dispatchTask, jevAsk } from "./dispatch.js";

const byTid = (tid) => TARGETS.find((t) => t.tid === tid);
const admitted = [
  { target: byTid("zai-glm-5.3"), windows: [] },
  { target: byTid("zai-glm-light"), windows: [] },
  { target: byTid("deepseek-v4-flash"), windows: null },
];
const answers = (route, tier = 1, noul = 0.1) => ({
  answers: { route, task_tier: { score: tier }, defer_acceptable: { noul } },
});

test("valid jev choice is kept", () => {
  const r = applyPolicy(admitted, answers({ choice: "dsh:zai-glm-light", confidence: 0.9 }));
  assert.equal(r.mode, "jev");
  assert.equal(r.decision, "dsh:zai-glm-light");
});

test("unknown jev choice falls back instead of crashing", () => {
  const r = applyPolicy(admitted, answers({ choice: "dsh:made-up", confidence: 0.99 }, 2));
  assert.equal(r.mode, "invalid_choice");
  assert.equal(r.decision, "dsh:zai-glm-5.3");
  assert.ok(r.target);
});

test("malformed answers fall back at the T3 floor", () => {
  for (const body of [null, {}, { answers: {} }, answers({ choice: 7, confidence: 0.9 })]) {
    const r = applyPolicy(admitted, body);
    assert.equal(r.mode, "jev_unavailable");
    assert.equal(r.decision, "dsh:zai-glm-5.3");
  }
});

test("tier floor still overrides a light pick", () => {
  const r = applyPolicy(admitted, answers({ choice: "dsh:zai-glm-light", confidence: 0.9 }, 3));
  assert.equal(r.mode, "policy_override");
  assert.equal(r.decision, "dsh:zai-glm-5.3");
});

test("fallback on jev outage keeps a T3 target", () => {
  const r = fallbackPolicy(admitted, "HTTP 503");
  assert.equal(r.decision, "dsh:zai-glm-5.3");
  assert.equal(r.tierScore, null);
  assert.match(r.reason, /HTTP 503/);
});

test("dispatchTask survives a bogus jev reply and sends real candidate ids", async (t) => {
  const prevKey = process.env.TYPESAFE_API_KEY;
  process.env.TYPESAFE_API_KEY = "test";
  t.after(() => {
    if (prevKey === undefined) delete process.env.TYPESAFE_API_KEY;
    else process.env.TYPESAFE_API_KEY = prevKey;
  });
  let sentState = null;
  t.mock.method(globalThis, "fetch", async (url, init) => {
    if (String(url).includes("typesafe")) {
      sentState = JSON.parse(init.body).state;
      return Response.json(answers({ choice: "dsh:nope", confidence: 0.95 }, 1));
    }
    if (String(url).includes("deepseek")) {
      return Response.json({
        is_available: true,
        balance_infos: [{ currency: "CNY", total_balance: "50" }],
      });
    }
    return Response.json({}, { status: 500 });
  });

  const r = await dispatchTask("rename a variable", {
    keys: { deepseek: "k" },
    providers: new Set(["deepseek"]),
  });
  assert.equal(r.mode, "invalid_choice");
  assert.match(r.decision, /^dsh:deepseek-v4-/);
  assert.deepEqual(
    sentState.candidates.map((c) => c.execution_target_id).sort(),
    ["dsh:deepseek-v4-flash", "dsh:deepseek-v4-pro"],
  );
  assert.match(sentState.candidates[0].quota, /pay-as-you-go/);
});

test("jevAsk gives up immediately on 401", async (t) => {
  const f = t.mock.method(globalThis, "fetch", async () => new Response("bad key", { status: 401 }));
  const started = Date.now();
  await assert.rejects(jevAsk({}, {}, "k"), /jev HTTP 401: bad key/);
  assert.equal(f.mock.callCount(), 1);
  assert.ok(Date.now() - started < 500, "no backoff sleep on a non-retryable error");
});

test("jevAsk retries transient 5xx and then succeeds", async (t) => {
  let n = 0;
  const f = t.mock.method(globalThis, "fetch", async () =>
    ++n === 1 ? new Response("overloaded", { status: 529 }) : Response.json({ ok: 1 }),
  );
  assert.deepEqual(await jevAsk({}, {}, "k"), { ok: 1 });
  assert.equal(f.mock.callCount(), 2);
});

test("fallback with no T3 candidate keeps the seed model", () => {
  const lowTiers = [admitted[1], admitted[2]];
  for (const r of [fallbackPolicy(lowTiers, "HTTP 503"), applyPolicy(lowTiers, {})]) {
    assert.equal(r.decision, null);
    assert.equal(r.target, undefined);
    assert.match(r.reason, /no admitted T3 target/);
  }
});
