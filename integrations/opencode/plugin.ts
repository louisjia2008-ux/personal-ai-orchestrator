import { Plugin } from "@opencode-ai/plugin"

import {
  parseRoutingDecision,
  type RoutingDecision,
  type RoutingMode,
} from "./decision_contract.ts"

function optionString(value: unknown, fallback: string): string {
  return typeof value === "string" && value.length > 0 ? value : fallback
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
  id: "personal-ai-orchestrator.spike",
  async setup(ctx) {
    const endpoint = optionString(ctx.options.endpoint, "http://127.0.0.1:8765")
    const mode = optionMode(ctx.options.mode)

    await ctx.command.transform((draft) => {
      draft.add({
        name: "orchestrator-route",
        description: "Ask Personal AI Orchestrator for a model-routing recommendation",
        async execute({ sessionID }) {
          // Stage A/B intentionally uses an explicit command instead of silently routing
          // every prompt. Once retry/idempotency behavior is proven against a real
          // OpenCode build, the same thin client can be called from an admission hook.
          const requestID = crypto.randomUUID()
          const decision = await requestRoute({
            endpoint,
            requestID,
            sessionID,
            projectID: ctx.location.project?.id,
            location: ctx.location.directory,
            mode,
          })

          if (!decision) {
            // Invalid, stale, mismatched, or unavailable daemon responses all fail closed.
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
