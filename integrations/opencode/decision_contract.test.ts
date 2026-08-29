import assert from "node:assert/strict"
import test from "node:test"

import { parseRoutingDecision } from "./decision_contract.ts"

const selected = { provider_id: "minimax", model_id: "m3" }

function validDecision(overrides: Record<string, unknown> = {}) {
  return {
    decision_id: "dec-1",
    request_id: "req-1",
    mode: "ACTIVE",
    selected_model: selected,
    switch_requested: true,
    quota_snapshot_ids: ["quota-1"],
    ...overrides,
  }
}

test("accepts a matching ACTIVE decision", () => {
  const parsed = parseRoutingDecision(validDecision(), {
    requestID: "req-1",
    mode: "ACTIVE",
  })
  assert.equal(parsed?.decision_id, "dec-1")
  assert.deepEqual(parsed?.selected_model, selected)
})

test("rejects a stale request id", () => {
  const parsed = parseRoutingDecision(validDecision({ request_id: "req-stale" }), {
    requestID: "req-1",
    mode: "ACTIVE",
  })
  assert.equal(parsed, undefined)
})

test("rejects a mismatched routing mode", () => {
  const parsed = parseRoutingDecision(validDecision({ mode: "SHADOW", switch_requested: false }), {
    requestID: "req-1",
    mode: "ACTIVE",
  })
  assert.equal(parsed, undefined)
})

test("rejects malformed selected model", () => {
  const parsed = parseRoutingDecision(
    validDecision({ selected_model: { provider_id: "minimax" } }),
    { requestID: "req-1", mode: "ACTIVE" },
  )
  assert.equal(parsed, undefined)
})

test("rejects switch without a selected model", () => {
  const parsed = parseRoutingDecision(validDecision({ selected_model: null }), {
    requestID: "req-1",
    mode: "ACTIVE",
  })
  assert.equal(parsed, undefined)
})

test("rejects SHADOW side-effect request", () => {
  const parsed = parseRoutingDecision(
    validDecision({ mode: "SHADOW", switch_requested: true }),
    { requestID: "req-1", mode: "SHADOW" },
  )
  assert.equal(parsed, undefined)
})

test("rejects malformed quota snapshot references", () => {
  const parsed = parseRoutingDecision(validDecision({ quota_snapshot_ids: ["quota-1", 2] }), {
    requestID: "req-1",
    mode: "ACTIVE",
  })
  assert.equal(parsed, undefined)
})
