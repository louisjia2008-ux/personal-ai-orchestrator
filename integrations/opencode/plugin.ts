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

  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 1500)
  try {
    const response = await fetch(`${input.endpoint}/v1/opencode/route`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        request_id: input.requestID,
        session_id: input.sessionID,
        project_id: input.projectID,
        location: input.location,
        agent: input.agent,
        mode: input.mode,
        task_id: input.taskID,
        task_state_version: input.taskStateVersion,
      }),
      signal: controller.signal,
    })
    if (!response.ok) return undefined
    const payload: unknown = await response.json()
    return parseRoutingDecision(payload, { requestID: input.requestID, mode: input.mode })
  } catch {
    return undefined
  } finally {
    clearTimeout(timeout)
  }
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
        },
      })
    })
  },
})
