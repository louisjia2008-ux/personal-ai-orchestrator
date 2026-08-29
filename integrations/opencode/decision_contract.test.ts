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
