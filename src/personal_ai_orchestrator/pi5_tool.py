"""Trusted PI-5 request tool source.

The generated TypeScript tool only returns a structured request marker. It
contains no process-launch or network behavior; PAO decides later whether any
request may become a real child task.
"""

from __future__ import annotations

from pathlib import Path

from personal_ai_orchestrator.pi5_contract import (
    PI5_MAX_INTENT_CHARS,
    PI5_MAX_REASON_CHARS,
    PI5_MAX_REQUESTS_PER_PARENT_RUN,
    PI5_SCHEMA_VERSION,
)
from personal_ai_orchestrator.pi5_runtime import PI5_TOOL_NAME

PI5_TOOL_RELATIVE_PATH = Path(".pao") / "pi5-request.ts"

PI5_TOOL_SOURCE = rf'''let accepted = 0;
const MAX_REQUESTS = {PI5_MAX_REQUESTS_PER_PARENT_RUN};
const MAX_INTENT = {PI5_MAX_INTENT_CHARS};
const MAX_REASON = {PI5_MAX_REASON_CHARS};

export default function (pi: any) {{
  pi.registerTool({{
    name: "{PI5_TOOL_NAME}",
    label: "Request PAO delegation",
    description: "Record a bounded request for PAO host review; no worker is started by this tool.",
    parameters: {{
      type: "object",
      properties: {{
        intent: {{ type: "string", minLength: 1, maxLength: MAX_INTENT }},
        reason: {{ type: "string", minLength: 1, maxLength: MAX_REASON }},
      }},
      required: ["intent", "reason"],
      additionalProperties: false,
    }},
    async execute(_toolCallId: string, params: any) {{
      const intent = typeof params?.intent === "string" ? params.intent.trim() : "";
      const reason = typeof params?.reason === "string" ? params.reason.trim() : "";
      if (!intent || !reason || intent.length > MAX_INTENT || reason.length > MAX_REASON) {{
        return {{
          content: [{{ type: "text", text: "PI-5 request rejected: invalid bounded request." }}],
          details: {{
            paoDelegation: {{ schemaVersion: {PI5_SCHEMA_VERSION}, accepted: false, reason: "invalid_request" }},
          }},
        }};
      }}
      if (accepted >= MAX_REQUESTS) {{
        return {{
          content: [{{ type: "text", text: "PI-5 request rejected: parent request budget exhausted." }}],
          details: {{
            paoDelegation: {{ schemaVersion: {PI5_SCHEMA_VERSION}, accepted: false, reason: "budget_exhausted" }},
          }},
        }};
      }}
      accepted += 1;
      return {{
        content: [{{ type: "text", text: "PI-5 request recorded for PAO host review." }}],
        details: {{
          paoDelegation: {{
            schemaVersion: {PI5_SCHEMA_VERSION},
            accepted: true,
            ordinal: accepted,
            intent,
            reason,
          }},
        }},
      }};
    }},
  }});
}}
'''


def seed_pi5_tool(policy_root: Path) -> Path:
    target = policy_root / PI5_TOOL_RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(PI5_TOOL_SOURCE, encoding="utf-8")
    return target


__all__ = [
    "PI5_TOOL_RELATIVE_PATH",
    "PI5_TOOL_SOURCE",
    "seed_pi5_tool",
]
