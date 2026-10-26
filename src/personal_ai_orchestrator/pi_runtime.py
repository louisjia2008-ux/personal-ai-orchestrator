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
import sys
from dataclasses import dataclass
from pathlib import Path

from personal_ai_orchestrator.pi5_runtime import PI5_TOOL_NAME


class PiDelegationActivationError(ValueError):
    """Host preflight failed; no worker may start with partial delegation."""

    code = "PI_DELEGATION_ACTIVATION_UNAVAILABLE"

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"{self.code}: {reason_code}")


PI_PROTOCOL_ERROR_EXIT = 70
PI_MAX_STDOUT_BYTES = 4 * 1024 * 1024
PI_MAX_ERROR_MESSAGE_CHARS = 2048
PI_GUARD_RELATIVE_PATH = Path(".pao") / "pi-worktree-guard.ts"
PI_ALLOWED_TOOLS: tuple[str, ...] = (
    "pao_read",
    "pao_edit",
    "pao_write",
    "pao_grep",
    "pao_find",
    "pao_ls",
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

# Every file tool is a host implementation. Distinct names ensure an extension
# load error cannot reactivate a built-in pathname tool with the same name.
PI_WORKTREE_GUARD_SOURCE = r"""import { AsyncLocalStorage } from "node:async_hooks";
import { spawn } from "node:child_process";
import { realpathSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  createReadToolDefinition, createEditToolDefinition, createWriteToolDefinition,
  createFindToolDefinition, createLsToolDefinition, createGrepToolDefinition,
} from "@earendil-works/pi-coding-agent";

const HELPER: string[] = __PAO_FILE_HELPER__;
const ROOT = realpathSync(process.cwd());
const identity = statSync(ROOT, { bigint: true });
const ROOT_ID = `${identity.dev}:${identity.ino}`;
function toolPath(raw: string): string {
  let value = raw.replace(/[\u00A0\u2000-\u200A\u202F\u205F\u3000]/g, " ");
  if (value.startsWith("@")) value = value.slice(1);
  if (value.startsWith("file:")) value = fileURLToPath(value);
  if (value === "~") value = homedir();
  else if (value.startsWith("~/")) value = resolve(homedir(), value.slice(2));
  return resolve(ROOT, value);
}

export default function (pi: any) {
  const signals = new AsyncLocalStorage<AbortSignal | undefined>();
  const call = (operation: string, path: string, extra: any = {}) =>
    new Promise<any>((resolve, reject) => {
    const child = spawn(HELPER[0], [...HELPER.slice(1), ROOT, ROOT_ID], {
      cwd: ROOT, env: { PATH: process.env.PATH ?? "/usr/bin:/bin", LANG: "C.UTF-8" },
      stdio: ["pipe", "pipe", "ignore"], signal: signals.getStore(),
    });
    let output = Buffer.alloc(0);
    let failed = false;
    const timer = setTimeout(() => { failed = true; child.kill("SIGKILL"); }, 10000);
    child.on("error", () => { failed = true; });
    child.stdout.on("data", (chunk) => {
      if (output.length + chunk.length > 24 * 1024 * 1024) {
        failed = true; child.kill("SIGKILL");
      } else output = Buffer.concat([output, chunk]);
    });
    child.stdin.on("error", () => { failed = true; });
    child.on("close", (code) => {
      clearTimeout(timer);
      try {
        const result = JSON.parse(output.toString("utf8"));
        if (failed || code !== 0 || result.ok !== true) throw new Error();
        resolve(result.result);
      } catch { reject(new Error("PAO file operation denied or unavailable")); }
    });
    child.stdin.end(JSON.stringify({ ...extra, operation, path }));
  });
  const readFile = async (path: string) => Buffer.from(await call("read", path), "base64");
  const access = async (path: string) => { await call("stat", path); };
  const writeFile = async (path: string, content: string) => {
    await call("write", path, { content });
  };
  const exists = async (path: string) => {
    try { await access(path); return true; } catch { return false; }
  };
  const read = createReadToolDefinition(ROOT, { operations: {
    readFile, access, detectImageMimeType: async (path: string) => {
      const bytes = await readFile(path);
      if (bytes.subarray(0, 8).equals(Buffer.from([137,80,78,71,13,10,26,10]))) return "image/png";
      if (bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255) return "image/jpeg";
      if (bytes.subarray(0, 3).toString() === "GIF") return "image/gif";
      if (bytes.subarray(0, 4).toString() === "RIFF" &&
          bytes.subarray(8, 12).toString() === "WEBP") return "image/webp";
      return null;
    },
  }});
  const edit = createEditToolDefinition(ROOT, { operations: { readFile, writeFile, access } });
  const write = createWriteToolDefinition(ROOT, { operations: {
    writeFile, mkdir: async (path: string) => { await call("mkdir", path); },
  }});
  const find = createFindToolDefinition(ROOT, { operations: {
    exists, glob: (pattern: string, path: string, options: any) =>
      call("find", path, { pattern, limit: options.limit }),
  }});
  const ls = createLsToolDefinition(ROOT, { operations: {
    exists, stat: async (path: string) => {
      const value = await call("stat", path);
      return { isDirectory: () => value.directory };
    },
    readdir: (path: string) => call("ls", path),
  }});
  const grep = { ...createGrepToolDefinition(ROOT),
    description: "Search confined files with a Python regex or literal string. " +
      "Skips links and Git metadata. Results are bounded.",
    execute: async (_id: string, input: any) => ({
      content: [{ type: "text",
        text: await call("grep", toolPath(input.path ?? "."), input) }], details: {},
    }),
  };
  // No builtin recursive scanner may resolve a worktree pathname independently.
  for (const definition of [read, edit, write, find, ls, grep]) {
    const execute = definition.execute;
    pi.registerTool({ ...definition, name: `pao_${definition.name}`,
      execute: async (...args: any[]) => {
        return await signals.run(args[2], () => execute(...args));
      },
    });
  }
}
"""


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
    if getattr(sys, "frozen", False):
        helper = [sys.executable, "--pao-file-tool"]
    else:
        helper = [sys.executable, "-I", str(Path(__file__).with_name("pi_file_tools.py"))]
    source = PI_WORKTREE_GUARD_SOURCE.replace("__PAO_FILE_HELPER__", json.dumps(helper))
    target.write_text(source, encoding="utf-8")
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
    - only host-seeded explicit extensions are loaded;
    - bash/powershell are absent from the active tool allowlist.
    """

    if config.delegation_enabled and delegation_tool_path is None:
        raise PiDelegationActivationError("TRUSTED_TOOL_MISSING")
    if any(
        arg.split("=", 1)[0]
        in {
            "--tools",
            "-t",
            "--exclude-tools",
            "--no-tools",
            "--no-builtin-tools",
            "-e",
            "--extension",
            "--no-extensions",
            "--",
        }
        for arg in config.extra_args
    ):
        raise PiDelegationActivationError("CONFLICTING_TOOL_CONFIGURATION")
    tools = PI_ALLOWED_TOOLS + ((PI5_TOOL_NAME,) if config.delegation_enabled else ())
    extensions = ("-e", str(delegation_tool_path)) if config.delegation_enabled else ()
    # Bound one-shot jobs independently of Pi's interactive default, while
    # preserving an explicit host override without duplicate option values.
    thinking_args = (
        ()
        if any(arg.split("=", 1)[0] == "--thinking" for arg in config.extra_args)
        else ("--thinking", "off")
    )
    return (
        config.pi_bin,
        "--mode",
        "json",
        "--no-session",
        "--no-builtin-tools",
        "--no-approve",
        "--no-extensions",
        "-e",
        str(guard_path),
        *extensions,
        "--no-skills",
        "--no-prompt-templates",
        "--no-context-files",
        "--tools",
        ",".join(tools),
        "--model",
        model_ref,
        *thinking_args,
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
    agent_active = False
    retry_allowed = False
    retry_ready = False
    retry_attempt = 0
    active_retry_attempt: int | None = None
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

    def _reject_constant(value: str) -> object:  # NaN / Infinity are not JSON
        raise ValueError(f"non-standard JSON constant: {value}")

    def _strict_object(pairs: list[tuple[str, object]]) -> dict:
        obj: dict = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError(f"duplicate object field: {key}")
            obj[key] = value
        return obj

    for raw_line in text.split("\n"):
        line = raw_line[:-1] if raw_line.endswith("\r") else raw_line
        if not line:
            continue
        try:
            event = json.loads(
                line,
                parse_constant=_reject_constant,
                object_pairs_hook=_strict_object,
            )
        except (ValueError, RecursionError):
            # Decoder limits (integer length / nesting) are invalid protocol
            # input too; never let worker bytes escape into lifecycle cleanup.
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
            if agent_active or (agent_start_seen and not retry_ready):
                return invalid("invalid_agent_lifecycle")
            if retry_ready:
                active_retry_attempt = retry_attempt
                retry_ready = False
            agent_start_seen = True
            agent_active = True
            # Pi may continue after an explicit retryable provider error. Only
            # this interval's assistant identity and end can prove completion.
            agent_end_seen = False
            final_provider = final_model = final_stop_reason = final_error_message = None
        elif event_type == "agent_end":
            if not agent_active:
                return invalid("invalid_agent_lifecycle")
            will_retry = event.get("willRetry", False)
            if not isinstance(will_retry, bool):
                return invalid("invalid_agent_lifecycle")
            if will_retry and (final_stop_reason != "error" or active_tool_ids):
                return invalid("invalid_agent_lifecycle")
            if active_retry_attempt is not None and final_stop_reason != "error":
                # A successful retry emits auto_retry_end before agent_end.
                return invalid("invalid_agent_lifecycle")
            retry_allowed = will_retry
            agent_end_seen = not will_retry
            agent_active = False
        elif event_type == "auto_retry_start":
            attempt = event.get("attempt")
            maximum = event.get("maxAttempts")
            if (
                agent_active
                or not agent_start_seen
                or not retry_allowed
                or retry_ready
                or type(attempt) is not int
                or type(maximum) is not int
                or not 0 < attempt <= maximum
                or attempt != retry_attempt + 1
            ):
                return invalid("invalid_agent_lifecycle")
            retry_attempt = attempt
            retry_ready = True
            retry_allowed = False
        elif event_type == "auto_retry_end":
            attempt = event.get("attempt")
            success = event.get("success")
            if (
                type(attempt) is not int
                or attempt <= 0
                or attempt != retry_attempt
                or not isinstance(success, bool)
                or (active_retry_attempt != attempt and not retry_ready)
            ):
                return invalid("invalid_agent_lifecycle")
            if success and (
                not agent_active
                or active_retry_attempt != attempt
                or final_stop_reason in (None, "error")
            ):
                return invalid("invalid_agent_lifecycle")
            if not success and (agent_active or final_stop_reason != "error"):
                return invalid("invalid_agent_lifecycle")
            retry_ready = retry_allowed = False
            retry_attempt = 0
            active_retry_attempt = None
        elif event_type == "message_end":
            message = event.get("message")
            if isinstance(message, dict) and message.get("role") == "assistant":
                if not agent_active:
                    return invalid("invalid_agent_lifecycle")
                provider = message.get("provider")
                model = message.get("model")
                stop_reason = message.get("stopReason")
                final_provider = provider if isinstance(provider, str) else None
                final_model = model if isinstance(model, str) else None
                final_stop_reason = stop_reason if isinstance(stop_reason, str) else None
                final_error_message = _bounded_error_message(message.get("errorMessage"))
        elif event_type == "tool_execution_start":
            if not agent_active:
                return invalid("invalid_agent_lifecycle")
            tool_call_id = event.get("toolCallId")
            if not isinstance(tool_call_id, str) or tool_call_id in active_tool_ids:
                return invalid("invalid_tool_lifecycle")
            active_tool_ids.add(tool_call_id)
            tool_start_count += 1
        elif event_type == "tool_execution_end":
            if not agent_active:
                return invalid("invalid_agent_lifecycle")
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
