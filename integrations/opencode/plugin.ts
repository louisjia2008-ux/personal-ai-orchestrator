import { Plugin } from "@opencode-ai/plugin"

import {
  parseRoutingDecision,
  type RoutingDecision,
  type RoutingMode,
} from "./decision_contract"

function optionString(value: unknown, fallback: string): string {
  return typeof value === "string" && value.length > 0 ? value : fallback
}

function optionOptionalString(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined
}

function optionNonnegativeInteger(value: unknown): number | undefined {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : undefined
}

function optionMode(value: unknown): RoutingMode {
  return value === "ACTIVE" || value === "BYPASS" || value === "SHADOW" ? value : "SHADOW"
}

async function postJSON(endpoint: string, path: string, body: object): Promise<unknown | undefined> {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 1500)
  try {
    const response = await fetch(`${endpoint}${path}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    })
    if (!response.ok) return undefined
    return await response.json()
  } catch {
    return undefined
  } finally {
    clearTimeout(timeout)
  }
}

async function requestRoute(input: {
  endpoint: string
  requestID: string
  sessionID: string
  projectID?: string
  location?: string
  agent?: string
  mode: RoutingMode
  taskID?: string
  taskStateVersion?: number
}): Promise<RoutingDecision | undefined> {
  if (input.mode === "BYPASS") return undefined

  const payload = await postJSON(input.endpoint, "/v1/opencode/route", {
    request_id: input.requestID,
    session_id: input.sessionID,
    project_id: input.projectID,
    location: input.location,
    agent: input.agent,
    mode: input.mode,
    task_id: input.taskID,
    task_state_version: input.taskStateVersion,
  })
  return parseRoutingDecision(payload, { requestID: input.requestID, mode: input.mode })
}

type SwitchLease = {
  lease_id: string
  decision_id: string
  task_id: string
  task_state_version: number
  session_id: string
  status: "AUTHORIZED"
  expires_at_epoch: number
}

function parseSwitchLease(value: unknown, expected: {
  decisionID: string
  taskID: string
  taskStateVersion: number
  sessionID: string
}): SwitchLease | undefined {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined
  const item = value as Record<string, unknown>
  if (typeof item.lease_id !== "string" || item.lease_id.length === 0) return undefined
  if (item.decision_id !== expected.decisionID) return undefined
  if (item.task_id !== expected.taskID) return undefined
  if (item.task_state_version !== expected.taskStateVersion) return undefined
  if (item.session_id !== expected.sessionID) return undefined
  if (item.status !== "AUTHORIZED") return undefined
  if (typeof item.expires_at_epoch !== "number" || !Number.isInteger(item.expires_at_epoch)) {
    return undefined
  }
  return item as SwitchLease
}

async function authorizeSwitch(input: {
  endpoint: string
  decision: RoutingDecision
  requestID: string
  taskID: string
  taskStateVersion: number
  sessionID: string
}): Promise<SwitchLease | undefined> {
  const payload = await postJSON(input.endpoint, "/v1/opencode/authorize-switch", {
    decision_id: input.decision.decision_id,
    request_id: input.requestID,
    task_id: input.taskID,
    task_state_version: input.taskStateVersion,
    session_id: input.sessionID,
  })
  return parseSwitchLease(payload, {
    decisionID: input.decision.decision_id,
    taskID: input.taskID,
    taskStateVersion: input.taskStateVersion,
    sessionID: input.sessionID,
  })
}

async function resolveSwitch(input: {
  endpoint: string
  lease: SwitchLease
  completed: boolean
}): Promise<boolean> {
  const payload = await postJSON(input.endpoint, "/v1/opencode/resolve-switch", {
    lease_id: input.lease.lease_id,
    decision_id: input.lease.decision_id,
    session_id: input.lease.session_id,
    completed: input.completed,
  })
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) return false
  const item = payload as Record<string, unknown>
  return (
    item.lease_id === input.lease.lease_id &&
    item.decision_id === input.lease.decision_id &&
    item.status === (input.completed ? "COMPLETED" : "ABORTED")
  )
}

export default Plugin.define({
  id: "personal-ai-orchestrator.routing",
  async setup(ctx) {
    const endpoint = optionString(ctx.options.endpoint, "http://127.0.0.1:8765")
    const mode = optionMode(ctx.options.mode)
    // Task binding is host-owned plugin configuration, not prompt content. Shadow may run with
    // just a task ID; production ACTIVE additionally requires an exact state version server-side.
    const taskID = optionOptionalString(ctx.options.taskID)
    const taskStateVersion = optionNonnegativeInteger(ctx.options.taskStateVersion)

    await ctx.command.transform((draft) => {
      draft.add({
        name: "orchestrator-route",
        description: "Ask Personal AI Orchestrator for a model-routing recommendation",
        async execute({ sessionID }) {
          const requestID = crypto.randomUUID()
          const decision = await requestRoute({
            endpoint,
            requestID,
            sessionID,
            projectID: ctx.location.project?.id,
            location: ctx.location.directory,
            mode,
            taskID,
            taskStateVersion,
          })

          if (!decision) {
            console.warn("[personal-ai-orchestrator] no valid routing decision; keeping current model")
            return
          }

          await ctx.storage.set(`routing:${requestID}`, decision)

          if (mode !== "ACTIVE" || !decision.switch_requested || !decision.selected_model) {
            console.log(
              `[personal-ai-orchestrator] ${mode} decision ${decision.decision_id}: ` +
                `${decision.selected_model?.provider_id ?? "keep"}/${decision.selected_model?.model_id ?? "current"}`,
            )
            return
          }

          if (
            !taskID ||
            taskStateVersion === undefined ||
            decision.task_state_version !== taskStateVersion
          ) {
            console.warn(
              "[personal-ai-orchestrator] ACTIVE decision lacks matching task state authority; keeping current model",
            )
            return
          }

          // Close the distributed TOCTOU window immediately before the side effect. The daemon
          // revalidates the exact durable task version and installs a bounded SQLite-backed lease
          // that freezes task state transitions until this switch attempt resolves or expires.
          const lease = await authorizeSwitch({
            endpoint,
            decision,
            requestID,
            taskID,
            taskStateVersion,
            sessionID,
          })
          if (!lease) {
            console.warn(
              "[personal-ai-orchestrator] switch lease not authorized; keeping current model",
            )
            return
          }

          let completed = false
          try {
            await ctx.session.switchModel({
              sessionID,
              model: {
                providerID: decision.selected_model.provider_id,
                id: decision.selected_model.model_id,
                ...(decision.selected_model.variant
                  ? { variant: decision.selected_model.variant }
                  : {}),
              },
            })
            completed = true
          } finally {
            const resolved = await resolveSwitch({ endpoint, lease, completed })
            if (!resolved) {
              console.error(
                "[personal-ai-orchestrator] switch lease resolution failed; host state remains frozen until lease expiry",
              )
            }
          }
        },
      })
    })
  },
})
