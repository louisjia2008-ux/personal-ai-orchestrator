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

# Loaded only by the host's feature-enabled runtime. Socket authority is a
# generated literal outside the worktree, never a model parameter or environment.
PI5_SOCKET_TOOL_SOURCE = r"""import { createConnection } from "node:net";
const socketPath = __SOCKET_PATH__;
const ordinals = new Map<string, number>();
const failure = () => ({ content: [{ type: "text", text: "PAO delegation unavailable." }] });
export default function (pi: any) {
  pi.registerTool({
    name: "pao_delegate",
    label: "Request PAO delegation",
    description: "Request bounded host-selected PAO child execution.",
    parameters: {
      type: "object",
      properties: {
        intent: { type: "string", minLength: 1, maxLength: __MAX_INTENT__ },
        reason: { type: "string", minLength: 1, maxLength: __MAX_REASON__ },
      },
      required: ["intent", "reason"], additionalProperties: false,
    },
    async execute(toolCallId: string, params: any) {
      if (typeof toolCallId !== "string" || !toolCallId || toolCallId.length > 256 ||
          !params || Object.keys(params).some(k => !["intent", "reason"].includes(k)) ||
          typeof params.intent !== "string" || typeof params.reason !== "string" ||
          !params.intent.trim() || !params.reason.trim() ||
          params.intent.length > __MAX_INTENT__ || params.reason.length > __MAX_REASON__) return failure();
      if (!ordinals.has(toolCallId)) {
        if (ordinals.size >= 3) return failure();
        ordinals.set(toolCallId, ordinals.size + 1);
      }
      const line = JSON.stringify({ schema_version: 1, tool_call_id: toolCallId,
        ordinal: ordinals.get(toolCallId), intent: params.intent, reason: params.reason }) + "\n";
      if (Buffer.byteLength(line) > 16384) return failure();
      return await new Promise(resolve => {
        const socket = createConnection({ path: socketPath });
        let data = Buffer.alloc(0), settled = false;
        const finish = (value: any) => {
          if (settled) return;
          settled = true; socket.destroy(); resolve(value);
        };
        socket.setTimeout(310000, () => finish(failure()));
        socket.on("error", () => finish(failure()));
        socket.on("connect", () => socket.write(line));
        socket.on("data", chunk => {
          if (data.length + chunk.length > 16384) return finish(failure());
          data = Buffer.concat([data, chunk]);
        });
        socket.on("end", () => {
          try {
            const wire = data.toString("utf8");
            if (!wire.endsWith("\n") || wire.slice(0, -1).includes("\n")) return finish(failure());
            const response = JSON.parse(wire);
            const states = ["VERIFIED", "BLOCKED", "FAILED", "CANCELLED", "COMPLETED",
              "SUBMITTED", "READY", "RUNNING", "WORKER_FINISHED", "VERIFYING"];
            if (response.schema_version !== 1 || response.tool_call_id !== toolCallId ||
                response.ordinal !== ordinals.get(toolCallId) || response.status !== "COMPLETED" ||
                !states.includes(response.child_state) || typeof response.verified !== "boolean" ||
                (response.verified && response.child_state !== "VERIFIED")) return finish(failure());
            // Only fixed-enum state and boolean reach the model; no raw error or transcript.
            finish({ content: [{ type: "text", text: JSON.stringify({
              child_state: response.child_state, verified: response.verified,
            }) }] });
          } catch { finish(failure()); }
        });
        socket.on("close", () => { if (!settled) finish(failure()); });
      });
    },
  });
}
"""


def pi5_socket_tool_source(socket_path: Path) -> str:
    import json

    return (
        PI5_SOCKET_TOOL_SOURCE.replace("__SOCKET_PATH__", json.dumps(str(socket_path)))
        .replace("__MAX_INTENT__", str(PI5_MAX_INTENT_CHARS))
        .replace("__MAX_REASON__", str(PI5_MAX_REASON_CHARS))
    )


def seed_pi5_socket_tool(policy_root: Path, *, socket_path: Path) -> Path:
    target = policy_root / PI5_TOOL_RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(pi5_socket_tool_source(socket_path), encoding="utf-8")
    return target
