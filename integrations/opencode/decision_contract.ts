export type RoutingMode = "BYPASS" | "SHADOW" | "ACTIVE"

export type ModelRef = {
  provider_id: string
  model_id: string
  variant?: string | null
}

export type RoutingDecision = {
  decision_id: string
  request_id: string
  mode: RoutingMode
  selected_model?: ModelRef | null
  selected_execution_target_id?: string | null
  switch_requested: boolean
  explanation_ref?: string | null
  catalog_snapshot_id?: string | null
  policy_snapshot_id?: string | null
  quota_snapshot_ids?: string[]
  fallback_reason?: string | null
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
  if (value.variant !== undefined && value.variant !== null && typeof value.variant !== "string") {
    return undefined
  }
  return {
    provider_id: value.provider_id,
    model_id: value.model_id,
    ...(value.variant !== undefined ? { variant: value.variant as string | null } : {}),
  }
}

export function parseRoutingDecision(
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
  if (value.mode !== "ACTIVE" && value.switch_requested) return undefined
  if (value.switch_requested && !selectedModel) return undefined
  if (value.mode === "BYPASS" && selectedModel) return undefined

  const optionalStrings = [
    "selected_execution_target_id",
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
    selected_execution_target_id: value.selected_execution_target_id as string | null | undefined,
    switch_requested: value.switch_requested,
    explanation_ref: value.explanation_ref as string | null | undefined,
    catalog_snapshot_id: value.catalog_snapshot_id as string | null | undefined,
    policy_snapshot_id: value.policy_snapshot_id as string | null | undefined,
    quota_snapshot_ids: quotaSnapshotIDs as string[] | undefined,
    fallback_reason: value.fallback_reason as string | null | undefined,
  }
}
