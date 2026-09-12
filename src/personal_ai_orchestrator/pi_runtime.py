"""Pi one-shot worker runtime primitives for the PAO execution boundary.

PI-1 deliberately uses Pi's ``--mode json`` one-shot event stream rather
than RPC. PAO's existing execution contract is one task -> one exact child
process -> one durable run row; a one-shot Pi process preserves that contract
without introducing a long-lived shared runtime or a second task authority.

This module contains no task-state, quota, scheduling, or verification logic.
Those remain host-owned.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

PI_PROTOCOL_ERROR_EXIT = 70
PI_MAX_STDOUT_BYTES = 4 * 1024 * 1024
PI_GUARD_RELATIVE_PATH = Path(".pao") / "pi-worktree-guard.ts"
PI_ALLOWED_TOOLS: tuple[str, ...] = (
    "read",
    "edit",
    "write",
    "grep",
    "find",
    "ls",
)

# PAO provider surfaces and Pi provider IDs are not always the same identity.
# Keep this translation narrow and explicit; unknown providers preserve their
# ID so a later runtime-verification gate fails visibly instead of guessing.
PI_PROVIDER_ALIASES: dict[str, str] = {
    "zai-coding-plan": "zai",
    "zai-coding-cn": "zai-coding-cn",
    "minimax-cn-coding-plan": "minimax-cn",
    "minimax-coding-plan": "minimax",
    "minimax-cn": "minimax-cn",
    "minimax": "minimax",
    "opencode": "opencode",
}

# Host-owned Pi extension. Pi itself has no built-in sandbox, so PI-1 does
# not rely on prompt instructions for workspace confinement. The extension
# blocks every enabled path-bearing file tool when its path escapes the task
# worktree lexically OR through an existing symlink. Bash is not enabled at
# all, and project/global extensions are disabled by argv policy.
PI_WORKTREE_GUARD_SOURCE = r'''import type {
  ExtensionAPI,
} from "@earendil-works/pi-coding-agent";
import { existsSync, realpathSync } from "node:fs";
import {
  dirname,
  isAbsolute,
  relative,
  resolve,
  sep,
} from "node:path";

const PATH_TOOLS = new Set([
  "read",
  "edit",
  "write",
  "grep",
  "find",
  "ls",
]);
const MUTATING_TOOLS = new Set(["edit", "write"]);

function isWithin(root: string, candidate: string): boolean {
  const rel = relative(root, candidate);
  return (
    rel === "" ||
    (rel !== ".." && !rel.startsWith(`..${sep}`) && !isAbsolute(rel))
  );
}

function existingAncestor(path: string): string {
  let current = path;
  while (!existsSync(current)) {
    const parent = dirname(current);
    if (parent === current) break;
    current = parent;
  }
  return current;
}

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event, ctx) => {
    if (!PATH_TOOLS.has(event.toolName)) return;

    const input = event.input as Record<string, unknown>;
    const raw = input?.path;
    if (typeof raw !== "string" || raw.length === 0) {
      return {
        block: true,
        reason: "PAO worktree guard: missing path",
        terminate: true,
      };
    }

    try {
      const logicalRoot = resolve(ctx.cwd);
      const realRoot = realpathSync(logicalRoot);
      const normalized = raw.startsWith("@") ? raw.slice(1) : raw;
      const logicalTarget = resolve(logicalRoot, normalized);

      if (!isWithin(logicalRoot, logicalTarget)) {
        return {
          block: true,
          reason: "PAO worktree guard: path escapes assigned worktree",
          terminate: true,
        };
      }

      const ancestor = existingAncestor(logicalTarget);
      const realAncestor = realpathSync(ancestor);
      if (!isWithin(realRoot, realAncestor)) {
        return {
          block: true,
          reason: "PAO worktree guard: symlink escapes assigned worktree",
          terminate: true,
        };
      }

      if (MUTATING_TOOLS.has(event.toolName)) {
        const gitDir = resolve(logicalRoot, ".git");
        const policyDir = resolve(logicalRoot, ".pao");
        if (
          isWithin(gitDir, logicalTarget) ||
          isWithin(policyDir, logicalTarget)
        ) {
          return {
            block: true,
            reason: "PAO worktree guard: protected host path",
            terminate: true,
          };
        }
      }
    } catch {
      return {
        block: true,
        reason: "PAO worktree guard: path could not be proven safe",
        terminate: true,
      };
    }
  });
}
'''


@dataclass(frozen=True)
class PiRuntimeConfig:
    """Host-owned Pi runtime settings; never client supplied."""

    pi_bin: str = "pi"
    extra_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class PiJsonRunSummary:
    protocol_valid: bool
    session_seen: bool
    agent_start_seen: bool
    agent_end_seen: bool
    event_count: int
    tool_start_count: int
    tool_end_count: int
    extension_error_count: int
    parse_error: str | None = None

    @property
    def completed(self) -> bool:
        return (
            self.protocol_valid
            and self.session_seen
            and self.agent_start_seen
            and self.agent_end_seen
            and self.tool_start_count == self.tool_end_count
            and self.extension_error_count == 0
        )


def pi_model_ref(*, provider_id: str, model_sku_id: str) -> str:
    """Translate one PAO model identity into Pi's ``provider/model`` form."""

    provider = PI_PROVIDER_ALIASES.get(provider_id, provider_id)
    model = model_sku_id.split("/", 1)[1] if "/" in model_sku_id else model_sku_id
    return f"{provider}/{model}"


def seed_pi_worktree_guard(policy_root: Path) -> Path:
    """Write the exact host-owned guard for this run and return its path."""

    target = policy_root / PI_GUARD_RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(PI_WORKTREE_GUARD_SOURCE, encoding="utf-8")
    return target


def build_pi_json_argv(
    *,
    config: PiRuntimeConfig,
    model_ref: str,
    intent: str,
    guard_path: Path,
) -> tuple[str, ...]:
    """Build the deterministic one-shot Pi worker command.

    Security posture:
    - no persistent session;
    - no project trust;
    - no discovered extensions/skills/templates/context files;
    - only PAO's explicit guard extension is loaded;
    - bash/powershell are absent from the active tool allowlist.
    """

    return (
        config.pi_bin,
        "--mode",
        "json",
        "--no-session",
        "--no-approve",
        "--no-extensions",
        "-e",
        str(guard_path),
        "--no-skills",
        "--no-prompt-templates",
        "--no-context-files",
        "--tools",
        ",".join(PI_ALLOWED_TOOLS),
        "--model",
        model_ref,
        *config.extra_args,
        "--",
        intent,
    )


def summarize_pi_json_stream(data: bytes) -> PiJsonRunSummary:
    """Validate the bounded Pi JSONL stream without granting it authority."""

    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return PiJsonRunSummary(
            False,
            False,
            False,
            False,
            0,
            0,
            0,
            0,
            "invalid_utf8",
        )

    session_seen = False
    agent_start_seen = False
    agent_end_seen = False
    tool_start_count = 0
    tool_end_count = 0
    extension_error_count = 0
    event_count = 0

    for raw_line in text.split("\n"):
        line = raw_line[:-1] if raw_line.endswith("\r") else raw_line
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return PiJsonRunSummary(
                False,
                session_seen,
                agent_start_seen,
                agent_end_seen,
                event_count,
                tool_start_count,
                tool_end_count,
                extension_error_count,
                "invalid_jsonl",
            )
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            return PiJsonRunSummary(
                False,
                session_seen,
                agent_start_seen,
                agent_end_seen,
                event_count,
                tool_start_count,
                tool_end_count,
                extension_error_count,
                "invalid_event_shape",
            )
        event_count += 1
        event_type = event["type"]
        if event_count == 1 and event_type != "session":
            return PiJsonRunSummary(
                False,
                False,
                agent_start_seen,
                agent_end_seen,
                event_count,
                tool_start_count,
                tool_end_count,
                extension_error_count,
                "missing_session_header",
            )
        if event_type == "session":
            session_seen = True
        elif event_type == "agent_start":
            agent_start_seen = True
        elif event_type == "agent_end":
            agent_end_seen = True
        elif event_type == "tool_execution_start":
            tool_start_count += 1
        elif event_type == "tool_execution_end":
            tool_end_count += 1
        elif event_type == "extension_error":
            extension_error_count += 1

    return PiJsonRunSummary(
        True,
        session_seen,
        agent_start_seen,
        agent_end_seen,
        event_count,
        tool_start_count,
        tool_end_count,
        extension_error_count,
        None,
    )


__all__ = [
    "PI_ALLOWED_TOOLS",
    "PI_GUARD_RELATIVE_PATH",
    "PI_MAX_STDOUT_BYTES",
    "PI_PROTOCOL_ERROR_EXIT",
    "PI_PROVIDER_ALIASES",
    "PI_WORKTREE_GUARD_SOURCE",
    "PiJsonRunSummary",
    "PiRuntimeConfig",
    "build_pi_json_argv",
    "pi_model_ref",
    "seed_pi_worktree_guard",
    "summarize_pi_json_stream",
]
