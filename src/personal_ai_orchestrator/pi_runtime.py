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
PI_MAX_ERROR_MESSAGE_CHARS = 2048
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
#
# The source intentionally has no dependency on pi's package types. CLI
# ``-e`` loads this trusted TypeScript file directly, while all imports are
# Node built-ins. This avoids relying on global npm module resolution merely
# to enforce the guard.
PI_WORKTREE_GUARD_SOURCE = r'''import { existsSync, realpathSync } from "node:fs";
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
const OPTIONAL_PATH_TOOLS = new Set(["grep", "find", "ls"]);
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

export default function (pi: any) {
  pi.on("tool_call", async (event: any, ctx: any) => {
    if (!PATH_TOOLS.has(event.toolName)) return;

    const input = event.input as Record<string, unknown>;
    let raw = input?.path;
    if (
      (raw === undefined || raw === "") &&
      OPTIONAL_PATH_TOOLS.has(event.toolName)
    ) {
      raw = ".";
    }
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
    delegation_enabled: bool = False


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
    final_provider: str | None = None
    final_model: str | None = None
    final_stop_reason: str | None = None
    final_error_message: str | None = None
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
            and self.final_provider is not None
            and self.final_model is not None
            and self.final_stop_reason == "stop"
        )

    def matches_model_ref(self, model_ref: str) -> bool:
        expected_provider, separator, expected_model = model_ref.partition("/")
        return (
            bool(separator)
            and self.completed
            and self.final_provider == expected_provider
            and self.final_model == expected_model
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
    delegation_tool_path: Path | None = None,
) -> tuple[str, ...]:
    """Build the deterministic one-shot Pi worker command.

    Security posture:
    - no persistent session;
    - no project trust;
    - no discovered extensions/skills/templates/context files;
    - only PAO's explicit guard extension is loaded;
    - bash/powershell are absent from the active tool allowlist.
    """

    if config.delegation_enabled and delegation_tool_path is None:
        raise ValueError("enabled delegation requires a host-seeded trusted tool")
    extensions = (
        ("-e", str(delegation_tool_path)) if config.delegation_enabled else ()
    )
    return (
        config.pi_bin,
        "--mode",
        "json",
        "--no-session",
        "--no-approve",
        "--no-extensions",
        "-e",
        str(guard_path),
        *extensions,
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


def _bounded_error_message(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value[:PI_MAX_ERROR_MESSAGE_CHARS]


def _invalid_summary(
    *,
    session_seen: bool,
    agent_start_seen: bool,
    agent_end_seen: bool,
    event_count: int,
    tool_start_count: int,
    tool_end_count: int,
    extension_error_count: int,
    final_provider: str | None,
    final_model: str | None,
    final_stop_reason: str | None,
    final_error_message: str | None,
    parse_error: str,
) -> PiJsonRunSummary:
    return PiJsonRunSummary(
        protocol_valid=False,
        session_seen=session_seen,
        agent_start_seen=agent_start_seen,
        agent_end_seen=agent_end_seen,
        event_count=event_count,
        tool_start_count=tool_start_count,
        tool_end_count=tool_end_count,
        extension_error_count=extension_error_count,
        final_provider=final_provider,
        final_model=final_model,
        final_stop_reason=final_stop_reason,
        final_error_message=final_error_message,
        parse_error=parse_error,
    )


def summarize_pi_json_stream(data: bytes) -> PiJsonRunSummary:
    """Validate the bounded Pi JSONL stream without granting it authority."""

    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return _invalid_summary(
            session_seen=False,
            agent_start_seen=False,
            agent_end_seen=False,
            event_count=0,
            tool_start_count=0,
            tool_end_count=0,
            extension_error_count=0,
            final_provider=None,
            final_model=None,
            final_stop_reason=None,
            final_error_message=None,
            parse_error="invalid_utf8",
        )

    session_seen = False
    agent_start_seen = False
    agent_end_seen = False
    tool_start_count = 0
    tool_end_count = 0
    extension_error_count = 0
    event_count = 0
    active_tool_ids: set[str] = set()
    final_provider: str | None = None
    final_model: str | None = None
    final_stop_reason: str | None = None
    final_error_message: str | None = None

    def invalid(parse_error: str) -> PiJsonRunSummary:
        return _invalid_summary(
            session_seen=session_seen,
            agent_start_seen=agent_start_seen,
            agent_end_seen=agent_end_seen,
            event_count=event_count,
            tool_start_count=tool_start_count,
            tool_end_count=tool_end_count,
            extension_error_count=extension_error_count,
            final_provider=final_provider,
            final_model=final_model,
            final_stop_reason=final_stop_reason,
            final_error_message=final_error_message,
            parse_error=parse_error,
        )

    for raw_line in text.split("\n"):
        line = raw_line[:-1] if raw_line.endswith("\r") else raw_line
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return invalid("invalid_jsonl")
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            return invalid("invalid_event_shape")

        event_count += 1
        event_type = event["type"]
        if event_count == 1 and event_type != "session":
            return invalid("missing_session_header")

        if event_type == "session":
            session_seen = True
        elif event_type == "agent_start":
            agent_start_seen = True
        elif event_type == "agent_end":
            agent_end_seen = True
        elif event_type == "message_end":
            message = event.get("message")
            if isinstance(message, dict) and message.get("role") == "assistant":
                provider = message.get("provider")
                model = message.get("model")
                stop_reason = message.get("stopReason")
                final_provider = provider if isinstance(provider, str) else None
                final_model = model if isinstance(model, str) else None
                final_stop_reason = stop_reason if isinstance(stop_reason, str) else None
                final_error_message = _bounded_error_message(message.get("errorMessage"))
        elif event_type == "tool_execution_start":
            tool_call_id = event.get("toolCallId")
            if not isinstance(tool_call_id, str) or tool_call_id in active_tool_ids:
                return invalid("invalid_tool_lifecycle")
            active_tool_ids.add(tool_call_id)
            tool_start_count += 1
        elif event_type == "tool_execution_end":
            tool_call_id = event.get("toolCallId")
            if not isinstance(tool_call_id, str) or tool_call_id not in active_tool_ids:
                return invalid("invalid_tool_lifecycle")
            active_tool_ids.remove(tool_call_id)
            tool_end_count += 1
        elif event_type == "extension_error":
            extension_error_count += 1

    if active_tool_ids:
        return invalid("incomplete_tool_lifecycle")

    return PiJsonRunSummary(
        protocol_valid=True,
        session_seen=session_seen,
        agent_start_seen=agent_start_seen,
        agent_end_seen=agent_end_seen,
        event_count=event_count,
        tool_start_count=tool_start_count,
        tool_end_count=tool_end_count,
        extension_error_count=extension_error_count,
        final_provider=final_provider,
        final_model=final_model,
        final_stop_reason=final_stop_reason,
        final_error_message=final_error_message,
        parse_error=None,
    )


__all__ = [
    "PI_ALLOWED_TOOLS",
    "PI_GUARD_RELATIVE_PATH",
    "PI_MAX_ERROR_MESSAGE_CHARS",
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
