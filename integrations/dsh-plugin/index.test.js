import { test } from "node:test";
import assert from "node:assert/strict";
import { apply, DispatchBlockedError } from "./index.js";

const allowed = (model) => ({ action: "ALLOW", decision: model, target: { provider: "test", model } });
function host(dispatch) {
  const hooks = new Map();
  apply({ on: (name, callback) => hooks.set(name, callback) }, {}, {
    dispatch, keys: {}, providers: new Set(),
  });
  let requests = 0;
  const pre = (turn, text, role = "user") => hooks.get("agent/pre-step")({
    agent: { id: "agent" }, turn,
    messages: [{ role, content: [{ type: "text", text }] }],
  }, () => {});
  const request = (turn) => hooks.get("agent/request")({ agent: { id: "agent" }, turn }, async () => {
    requests++;
    return { provider: "seed", model: "seed" };
  });
  return { pre, request, hooks, requests: () => requests };
}

test("one decision per turn, fresh user prompt and quota admission next turn", async () => {
  const prompts = [];
  const h = host(async (prompt) => { prompts.push(prompt); return allowed(prompt); });
  h.pre(1, "first");
  assert.equal((await h.request(1)).model, "first");
  h.pre(1, "first");
  await h.request(1);
  // Harness supplies only this step's newly claimed inbox batch. A normal
  // tool continuation has no new user messages in the same turn.
  h.hooks.get("agent/pre-step")({ agent: { id: "agent" }, turn: 1, messages: [] }, () => {});
  assert.equal((await h.request(1)).model, "first");
  h.hooks.get("agent/pre-step")({ agent: { id: "agent" }, turn: 2, messages: [
    { role: "user", content: "second" },
    { role: "assistant", content: [{ type: "text", text: "assistant answer" }] },
    { role: "toolResult", content: [{ type: "text", text: "tool output" }] },
  ] }, () => {});
  assert.equal((await h.request(2)).model, "second");
  assert.deepEqual(prompts, ["first", "second"]);
});

test("blocked, malformed and failed dispatch never reach the seed provider", async () => {
  for (const value of [null, { decision: null, action: "BLOCK" }, allowed("unsafe-without-action")]) {
    if (value?.decision === "unsafe-without-action") delete value.action;
    const h = host(async () => value);
    h.pre(1, "task");
    await assert.rejects(h.request(1), DispatchBlockedError);
    assert.equal(h.requests(), 0);
  }
  let attempts = 0;
  const h = host(async () => { if (++attempts === 1) throw new Error("offline"); return allowed("recovered"); });
  h.pre(1, "first");
  await assert.rejects(h.request(1), DispatchBlockedError);
  h.pre(2, "second");
  assert.equal((await h.request(2)).model, "recovered");
  assert.equal(h.requests(), 1);
});

test("concurrent requests share admission and stale completion cannot rewrite another turn", async () => {
  let calls = 0;
  let finish;
  const h = host(() => { calls++; return new Promise((resolve) => { finish = resolve; }); });
  h.pre(1, "first");
  const one = h.request(1);
  const two = h.request(1);
  await Promise.resolve();
  assert.equal(calls, 1);
  finish(allowed("admitted"));
  assert.deepEqual((await Promise.all([one, two])).map((r) => r.model), ["admitted", "admitted"]);
  h.pre(2, "second");
  const stale = h.request(2);
  await Promise.resolve();
  h.pre(3, "third");
  finish(allowed("stale"));
  await assert.rejects(stale, /turn changed/);
  assert.equal(h.requests(), 2);
});

test("missing turn context blocks instead of inheriting a model", async () => {
  const h = host(async () => allowed("unused"));
  h.pre(1, "assistant only", "assistant");
  await assert.rejects(h.request(1), /context/);
  assert.equal(h.requests(), 0);
});

test("an empty new turn and a disposed agent cannot reuse prior admission", async () => {
  const h = host(async () => allowed("first"));
  h.pre(1, "task");
  await h.request(1);
  h.hooks.get("agent/pre-step")({ agent: { id: "agent" }, turn: 2, messages: [] }, () => {});
  await assert.rejects(h.request(2), /context/);
  h.pre(3, "task");
  h.hooks.get("agent/disposed")({ agent: { id: "agent" } });
  await assert.rejects(h.request(3), /context/);
  assert.equal(h.requests(), 1);
});
