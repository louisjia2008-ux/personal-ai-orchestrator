import { test } from "node:test";
import assert from "node:assert/strict";
import { TARGETS, applyPolicy, fallbackPolicy } from "./dispatch.ts";

const byTid = (tid: string) => TARGETS.find((t) => t.tid === tid)!;
const admitted = [
  { target: byTid("zai-glm-5.3"), windows: [], metering: "subscription" },
  { target: byTid("zai-glm-5.3-flash"), windows: [], metering: "subscription" },
  { target: byTid("deepseek-v4-flash"), windows: [], metering: "api_paygo" },
];
const answers = (route: any, tier = 1, noul = 0.1) => ({
  answers: { route, task_tier: { score: tier }, defer_acceptable: { noul } },
});

test("valid jev choice is kept", () => {
  const r = applyPolicy(admitted, answers({ choice: "pi@zai-glm-5.3-flash", confidence: 0.9 }));
  assert.equal(r.mode, "jev");
  assert.equal(r.decision, "pi@zai-glm-5.3-flash");
});

test("unknown jev choice falls back instead of crashing", () => {
  const r = applyPolicy(admitted, answers({ choice: "pi@made-up", confidence: 0.99 }, 2));
  assert.equal(r.mode, "invalid_choice");
  assert.equal(r.decision, "pi@zai-glm-5.3");
});

test("malformed answers fall back at the T3 floor", () => {
  for (const body of [null, {}, { answers: {} }, answers({ choice: "pi@zai-glm-5.3" })]) {
    const r = applyPolicy(admitted, body);
    assert.equal(r.mode, "jev_unavailable");
    assert.equal(r.decision, "pi@zai-glm-5.3");
  }
});

test("tier floor still overrides a light pick", () => {
  const r = applyPolicy(admitted, answers({ choice: "pi@zai-glm-5.3-flash", confidence: 0.9 }, 3));
  assert.equal(r.mode, "policy_override");
  assert.equal(r.decision, "pi@zai-glm-5.3");
});

test("fallback on jev outage keeps a T3 target", () => {
  const r = fallbackPolicy(admitted, "HTTP 503");
  assert.equal(r.decision, "pi@zai-glm-5.3");
  assert.equal(r.confidence, null);
});
