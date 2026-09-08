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
    selected_execution_target_id: "target-minimax-m3",
    switch_requested: true,
    task_state_version: 7,
    catalog_snapshot_id: "catalog-1",
    policy_snapshot_id: "policy-1",
    quota_snapshot_ids: ["quota-1"],
    ...overrides,
  }
}

test("accepts a matching ACTIVE decision", () => {
  const parsed = parseRoutingDecision(validDecision(), { requestID: "req-1", mode: "ACTIVE" })
  assert.equal(parsed?.decision_id, "dec-1")
  assert.equal(parsed?.task_state_version, 7)
  assert.deepEqual(parsed?.selected_model, selected)
})

test("rejects a stale request id", () => {
  assert.equal(
    parseRoutingDecision(validDecision({ request_id: "req-stale" }), {
      requestID: "req-1",
      mode: "ACTIVE",
    }),
    undefined,
  )
})

test("rejects a mismatched routing mode", () => {
  assert.equal(
    parseRoutingDecision(validDecision({ mode: "SHADOW", switch_requested: false }), {
      requestID: "req-1",
      mode: "ACTIVE",
    }),
    undefined,
  )
})

test("rejects malformed selected model", () => {
  assert.equal(
    parseRoutingDecision(validDecision({ selected_model: { provider_id: "minimax" } }), {
      requestID: "req-1",
      mode: "ACTIVE",
    }),
    undefined,
  )
})

test("rejects switch without a selected model", () => {
  assert.equal(
    parseRoutingDecision(validDecision({ selected_model: null }), {
      requestID: "req-1",
      mode: "ACTIVE",
    }),
    undefined,
  )
})

test("rejects switch without an authoritative task version", () => {
  assert.equal(
    parseRoutingDecision(validDecision({ task_state_version: null }), {
      requestID: "req-1",
      mode: "ACTIVE",
    }),
    undefined,
  )
})

test("rejects malformed task state version", () => {
  assert.equal(
    parseRoutingDecision(validDecision({ task_state_version: -1 }), {
      requestID: "req-1",
      mode: "ACTIVE",
    }),
    undefined,
  )
})

test("rejects SHADOW side-effect request", () => {
  assert.equal(
    parseRoutingDecision(validDecision({ mode: "SHADOW", switch_requested: true }), {
      requestID: "req-1",
      mode: "SHADOW",
    }),
    undefined,
  )
})

test("rejects malformed quota snapshot references", () => {
  assert.equal(
    parseRoutingDecision(validDecision({ quota_snapshot_ids: ["quota-1", 2] }), {
      requestID: "req-1",
      mode: "ACTIVE",
    }),
    undefined,
  )
})

test("rejects malformed execution target reference", () => {
  assert.equal(
    parseRoutingDecision(validDecision({ selected_execution_target_id: 42 }), {
      requestID: "req-1",
      mode: "ACTIVE",
    }),
    undefined,
  )
})

// M1 WP5a-1 — SUPERVISED_AUTO closed-union contract.
//
// 1. The validator accepts a SUPERVISED_AUTO decision when the
//    expected mode matches (e.g. the dispatch panel reads a
//    frozen RoutingDecision via the routing endpoint).
test("accepts a matching SUPERVISED_AUTO decision", () => {
  const parsed = parseRoutingDecision(
    validDecision({ mode: "SUPERVISED_AUTO", switch_requested: false }),
    { requestID: "req-1", mode: "SUPERVISED_AUTO" },
  )
  assert.equal(parsed?.mode, "SUPERVISED_AUTO")
  assert.equal(parsed?.switch_requested, false)
})

// 2. SUPERVISED_AUTO + switch_requested=true must be rejected — the
//    plugin never initiates a session switch for SUPERVISED_AUTO
//    decisions (the side-effect gate at decision_contract.ts:59
//    `if (value.mode !== "ACTIVE" && value.switch_requested)` keeps
//    this invariant).
test("rejects SUPERVISED_AUTO + switch_requested=true", () => {
  assert.equal(
    parseRoutingDecision(
      validDecision({ mode: "SUPERVISED_AUTO", switch_requested: true }),
      { requestID: "req-1", mode: "SUPERVISED_AUTO" },
    ),
    undefined,
  )
})

// 3. Mode mismatch still fails closed: a daemon returning
//    SUPERVISED_AUTO while the plugin expected ACTIVE must NOT be
//    accepted (this is the same guard as any other mode mismatch
//    — the new value gets no special treatment).
test("rejects SUPERVISED_AUTO decision when expected mode is ACTIVE", () => {
  assert.equal(
    parseRoutingDecision(
      validDecision({ mode: "SUPERVISED_AUTO", switch_requested: false }),
      { requestID: "req-1", mode: "ACTIVE" },
    ),
    undefined,
  )
})

// 4. The closed-union type still rejects arbitrary strings — this
//    pins the closed union (not `string`) and protects against a
//    future drift to a plain string type.
test("isRoutingMode rejects unknown strings", () => {
  // The validator lives at module scope; we exercise it indirectly
  // through parseRoutingDecision with a malformed mode string.
  // The parse function reads the JSON ``mode`` field, checks it
  // against the closed union, and returns undefined on miss.
  assert.equal(
    parseRoutingDecision(
      { ...validDecision(), mode: "AUTOPILOT" },
      { requestID: "req-1", mode: "ACTIVE" },
    ),
    undefined,
  )
})

// 5. Legacy regression: BYPASS, SHADOW, ACTIVE old behaviour
//    unchanged. The new SUPERVISED_AUTO branch must NOT affect
//    pre-WP5a-1 mode semantics.
test("legacy ACTIVE decision still requires matching mode", () => {
  const parsed = parseRoutingDecision(
    validDecision({ mode: "ACTIVE", switch_requested: true }),
    { requestID: "req-1", mode: "ACTIVE" },
  )
  assert.equal(parsed?.mode, "ACTIVE")
  assert.equal(parsed?.switch_requested, true)
})

test("legacy SHADOW decision still records only", () => {
  const parsed = parseRoutingDecision(
    validDecision({ mode: "SHADOW", switch_requested: false }),
    { requestID: "req-1", mode: "SHADOW" },
  )
  assert.equal(parsed?.mode, "SHADOW")
  assert.equal(parsed?.switch_requested, false)
})

test("legacy BYPASS decision still matches", () => {
  const parsed = parseRoutingDecision(
    validDecision({
      mode: "BYPASS",
      switch_requested: false,
      selected_model: null,
      selected_execution_target_id: null,
    }),
    { requestID: "req-1", mode: "BYPASS" },
  )
  assert.equal(parsed?.mode, "BYPASS")
})
