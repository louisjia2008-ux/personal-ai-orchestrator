import { Plugin } from "@opencode-ai/plugin"

type RoutingMode = "BYPASS" | "SHADOW" | "ACTIVE"

type ModelRef = {
  provider_id: string
  model_id: string
  variant?: string | null
}

type RoutingDecision = {
  decision_id: string
  request_id: string
  mode: RoutingMode
  selected_model?: ModelRef | null
  switch_requested: boolean
  explanation_ref?: string | null
  catalog_snapshot_id?: string | null
  policy_snapshot_id?: string | null
  quota_snapshot_ids?: string[]
  fallback_reason?: string | null
}

function optionString(value: unknown, fallback: string): string {
  return typeof value === "string" && value.length > 0 ? value : fallback
}

function optionMode(value: unknown): RoutingMode {
  return value === "ACTIVE" || value === "BYPASS" || value === "SHADOW" ? value : "SHADOW"
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function isRoutingMode(value: unknown): value is RoutingMode {
  return value === "ACTIVE" || value === "BYPASS" || value === "SHADOW"
}

function parseModelRef(value: unknown): ModelRef | null | undefined {
  if (value === null || value === undefined) return value
  if (!isRecord(value)) return undefined
  if (typeof value.provider_id !== "string" || value.provider_id.length === 0) return undefined
  if (typeof value.model_id !== "string" || value.model_id.length === 0) return undefined
  if (
    value.variant !== undefined &&
    value.variant !== null &&
    typeof value.variant !== "string"
  ) {
    return undefined
  }
  return {
    provider_id: value.provider_id,
    model_id: value.model_id,
    ...(value.variant !== undefined ? { variant: value.variant as string | null } : {}),
  }
}

function parseRoutingDecision(
  value: unknown,
  expected: { requestID: string; mode: RoutingMode },
): RoutingDecision | undefined {
  if (!isRecord(value)) return undefined
  if (typeof value.decision_id !== "string" || value.decision_id.length === 0) return undefined
  if (value.request_id !== expected.requestID) return undefined
  if (!isRoutingMode(value.mode) || value.mode !== expected.mode) return undefined
  if (typeof value.switch_requested !== "boolean") return undefined

  const selectedModel = parseModelRef(value.selected_model)
  if (value.selected_model !== undefined && selectedModel === undefined) return undefined

  // Contract parity with the Python boundary: only ACTIVE may request a switch,
  // and a switch request must carry a concrete model. BYPASS may not select one.
  if (value.mode !== "ACTIVE" && value.switch_requested) return undefined
  if (value.switch_requested && !selectedModel) return undefined
  if (value.mode === "BYPASS" && selectedModel) return undefined

  const optionalStrings = [
    "explanation_ref",
    "catalog_snapshot_id",
    "policy_snapshot_id",
    "fallback_reason",
  ] as const
  for (const key of optionalStrings) {
    const item = value[key]
    if (item !== undefined && item !== null && typeof item !== "string") return undefined
  }

  const quotaSnapshotIDs = value.quota_snapshot_ids
  if (
    quotaSnapshotIDs !== undefined &&
    (!Array.isArray(quotaSnapshotIDs) ||
      quotaSnapshotIDs.some((item) => typeof item !== "string" || item.length === 0))
  ) {
    return undefined
  }

  return {
    decision_id: value.decision_id,
    request_id: expected.requestID,
    mode: expected.mode,
    selected_model: selectedModel,
    switch_requested: value.switch_requested,
    explanation_ref: value.explanation_ref as string | null | undefined,
    catalog_snapshot_id: value.catalog_snapshot_id as string | null | undefined,
    policy_snapshot_id: value.policy_snapshot_id as string | null | undefined,
    quota_snapshot_ids: quotaSnapshotIDs as string[] | undefined,
    fallback_reason: value.fallback_reason as string | null | undefined,
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
