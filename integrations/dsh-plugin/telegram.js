/**
 * Telegram control plane for DeepSeek Harness.
 *
 * The transport is deliberately narrow: an allowlisted Telegram identity may
 * create/resume one Harness session for one configured cwd, submit prompts,
 * inspect status, or cancel the active turn.  It never accepts an argv, shell
 * command, provider credential, filesystem path, or Harness permission grant
 * from Telegram.  The Harness remains the authority for tools, sandboxing, and
 * approval policy.
 */

import { randomUUID } from "node:crypto";
import {
  chmodSync,
  mkdirSync,
  readFileSync,
  realpathSync,
  renameSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { homedir } from "node:os";
import { basename, dirname, isAbsolute, join } from "node:path";

const STATE_VERSION = 1;
const DEFAULT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN";
const DEFAULT_MAX_PROMPT_CHARS = 12_000;
const DEFAULT_MAX_REPLY_CHARS = 16_000;
const TELEGRAM_CHUNK_CHARS = 3_900;
const DEFAULT_POLL_TIMEOUT_SECONDS = 25;
const MAX_POLL_TIMEOUT_SECONDS = 50;
const COMMANDS = new Set(["help", "run", "status", "cancel", "new"]);

function asId(value, field) {
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) throw new Error(`${field}_must_be_a_safe_integer`);
    return String(value);
  }
  if (typeof value === "string" && /^-?\d+$/.test(value.trim())) return value.trim();
  throw new Error(`${field}_must_be_an_integer_id`);
}

function idSet(values, field) {
  if (!Array.isArray(values) || values.length === 0) {
    throw new Error(`${field}_must_be_a_non_empty_array`);
  }
  return new Set(values.map((value) => asId(value, field)));
}

function boundedInteger(value, fallback, minimum, maximum, field) {
  const resolved = value ?? fallback;
  if (!Number.isSafeInteger(resolved) || resolved < minimum || resolved > maximum) {
    throw new Error(`${field}_must_be_between_${minimum}_and_${maximum}`);
  }
  return resolved;
}

function optionalString(value, field) {
  if (value === undefined || value === null || value === "") return undefined;
  if (typeof value !== "string" || value.trim() === "") throw new Error(`${field}_must_be_a_string`);
  return value.trim();
}

function defaultStateFile(env) {
  const root = env.DSH_HOME || join(homedir(), ".dsh");
  return join(root, "pao-telegram-controller.json");
}

/** Validate the activation boundary and resolve all runtime-only configuration. */
export function resolveTelegramConfig(config = {}, env = process.env) {
  if (config.enabled !== true) return null;

  const tokenEnv = optionalString(config.tokenEnv, "tokenEnv") ?? DEFAULT_TOKEN_ENV;
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(tokenEnv)) {
    throw new Error("tokenEnv_must_be_an_environment_variable_name");
  }
  const token = env[tokenEnv];
  if (typeof token !== "string" || token.trim() === "") {
    throw new Error(`missing_telegram_bot_token_environment:${tokenEnv}`);
  }

  if (typeof config.cwd !== "string" || !isAbsolute(config.cwd)) {
    throw new Error("cwd_must_be_an_existing_absolute_directory");
  }
  let cwd;
  try {
    cwd = realpathSync(config.cwd);
    if (!statSync(cwd).isDirectory()) throw new Error("not_a_directory");
  } catch {
    throw new Error("cwd_must_be_an_existing_absolute_directory");
  }

  const stateFile = optionalString(config.stateFile, "stateFile") ?? defaultStateFile(env);
  if (!isAbsolute(stateFile)) throw new Error("stateFile_must_be_absolute");

  return Object.freeze({
    token: token.trim(),
    tokenEnv,
    allowedUserIds: idSet(config.allowedUserIds, "allowedUserIds"),
    allowedChatIds: idSet(config.allowedChatIds, "allowedChatIds"),
    cwd,
    workspaceLabel: optionalString(config.workspaceLabel, "workspaceLabel") ?? basename(cwd),
    stateFile,
    provider: optionalString(config.provider, "provider"),
    model: optionalString(config.model, "model"),
    reasoningEffort: optionalString(config.reasoningEffort, "reasoningEffort"),
    agentPreset: optionalString(config.agentPreset, "agentPreset"),
    maxTokens:
      config.maxTokens === undefined
        ? undefined
        : boundedInteger(config.maxTokens, undefined, 1, 1_000_000, "maxTokens"),
    maxPromptChars: boundedInteger(
      config.maxPromptChars,
      DEFAULT_MAX_PROMPT_CHARS,
      1,
      100_000,
      "maxPromptChars",
    ),
    maxReplyChars: boundedInteger(
      config.maxReplyChars,
      DEFAULT_MAX_REPLY_CHARS,
      1,
      200_000,
      "maxReplyChars",
    ),
    pollTimeoutSeconds: boundedInteger(
      config.pollTimeoutSeconds,
      DEFAULT_POLL_TIMEOUT_SECONDS,
      1,
      MAX_POLL_TIMEOUT_SECONDS,
      "pollTimeoutSeconds",
    ),
    ignorePendingOnFirstStart: config.ignorePendingOnFirstStart !== false,
  });
}

/** Parse only the explicit /dsh command namespace (including group-chat suffixes). */
export function parseTelegramCommand(text) {
  if (typeof text !== "string") return null;
  const match = text.match(/^\/dsh(?:@[A-Za-z0-9_]+)?(?:\s+([\s\S]*))?$/i);
  if (!match) return null;
  const body = (match[1] ?? "").trim();
  if (!body) return { command: "help", argument: "" };
  const separator = body.search(/\s/);
  const command = (separator === -1 ? body : body.slice(0, separator)).toLowerCase();
  const argument = separator === -1 ? "" : body.slice(separator).trim();
  if (!COMMANDS.has(command)) return { command: "invalid", argument: body };
  return { command, argument };
}

/** Split without breaking surrogate pairs, bounded by UTF-16 code units. */
export function telegramTextChunks(text, limit = TELEGRAM_CHUNK_CHARS) {
  const points = Array.from(String(text));
  if (points.length === 0) return ["(empty)"];
  const chunks = [];
  let current = "";
  for (const point of points) {
    if (current && current.length + point.length > limit) {
      chunks.push(current);
      current = "";
    }
    current += point;
  }
  if (current) chunks.push(current);
  return chunks;
}

function sanitizedDescription(value) {
  if (typeof value !== "string") return "request_failed";
  return value.replace(/[\r\n]+/g, " ").slice(0, 240);
}

export class TelegramApiError extends Error {
  constructor(status, description) {
    super(`telegram_api_error:${status}:${sanitizedDescription(description)}`);
    this.name = "TelegramApiError";
    this.status = status;
  }
}

/** Minimal Bot API client. The token is runtime-only and is never logged or persisted. */
export class TelegramBotApi {
  constructor({ token, fetchImpl = globalThis.fetch, baseUrl = "https://api.telegram.org" }) {
    if (typeof token !== "string" || token === "") throw new Error("telegram_token_required");
    if (typeof fetchImpl !== "function") throw new Error("fetch_required");
    this.token = token;
    this.fetchImpl = fetchImpl;
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  async call(method, payload, { signal } = {}) {
    const timeoutMs =
      method === "getUpdates"
        ? Math.max(10_000, (Number(payload?.timeout) || 0) * 1_000 + 10_000)
        : 20_000;
    const timeoutSignal = AbortSignal.timeout(timeoutMs);
    const requestSignal = signal ? AbortSignal.any([signal, timeoutSignal]) : timeoutSignal;
    let response;
    try {
      response = await this.fetchImpl(`${this.baseUrl}/bot${this.token}/${method}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
        signal: requestSignal,
      });
    } catch {
      // Native fetch errors may carry a request URL. Never let the URL-shaped
      // Bot API credential cross the transport boundary or reach logs.
      throw new TelegramApiError(0, "transport_failed");
    }
    let body;
    try {
      body = await response.json();
    } catch {
      throw new TelegramApiError(response.status, "non_json_response");
    }
    if (!response.ok || body?.ok !== true) {
      const description = sanitizedDescription(body?.description).replaceAll(this.token, "[redacted]");
      throw new TelegramApiError(body?.error_code ?? response.status, description);
    }
    return body.result;
  }

  getUpdates({ offset, timeoutSeconds, signal }) {
    const payload = {
      timeout: timeoutSeconds,
      limit: 100,
      allowed_updates: ["message"],
    };
    if (offset !== null && offset !== undefined) payload.offset = offset;
    return this.call("getUpdates", payload, { signal });
  }

  async sendText(chatId, text, { signal } = {}) {
    const results = [];
    for (const chunk of telegramTextChunks(text)) {
      results.push(
        await this.call(
          "sendMessage",
          {
            chat_id: chatId,
            text: chunk,
            link_preview_options: { is_disabled: true },
          },
          { signal },
        ),
      );
    }
    return results;
  }
}

function emptyState() {
  return { version: STATE_VERSION, offset: null, chats: {} };
}

function validState(value) {
  if (
    !value ||
    value.version !== STATE_VERSION ||
    !value.chats ||
    typeof value.chats !== "object" ||
    Array.isArray(value.chats)
  ) {
    return false;
  }
  if (value.offset !== null && !Number.isSafeInteger(value.offset)) return false;
  return Object.values(value.chats).every(
    (entry) =>
      entry &&
      typeof entry === "object" &&
      typeof entry.sessionId === "string" &&
      entry.sessionId.length > 0,
  );
}

/** Credential-free durable state: update offset plus chat-to-session mapping. */
export class TelegramStateStore {
  constructor(path) {
    this.path = path;
  }

  load() {
    try {
      const parsed = JSON.parse(readFileSync(this.path, "utf8"));
      return validState(parsed) ? parsed : emptyState();
    } catch {
      return emptyState();
    }
  }

  save(state) {
    if (!validState(state)) throw new Error("invalid_telegram_controller_state");
    mkdirSync(dirname(this.path), { recursive: true, mode: 0o700 });
    const temporary = `${this.path}.tmp-${process.pid}-${randomUUID()}`;
    writeFileSync(temporary, `${JSON.stringify(state)}\n`, { encoding: "utf8", mode: 0o600 });
    chmodSync(temporary, 0o600);
    renameSync(temporary, this.path);
    chmodSync(this.path, 0o600);
  }
}

function errorText(error, secrets = []) {
  const message = error instanceof Error ? error.message : String(error);
  let sanitized = message.replace(/[\r\n]+/g, " ");
  for (const secret of secrets) {
    if (typeof secret === "string" && secret) sanitized = sanitized.replaceAll(secret, "[redacted]");
  }
  return sanitized.slice(0, 300) || "unknown_error";
}

function sleep(ms, signal) {
  return new Promise((resolve) => {
    if (signal?.aborted) return resolve();
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}

function agentOptions(config) {
  return Object.fromEntries(
    Object.entries({
      provider: config.provider,
      model: config.model,
      reasoningEffort: config.reasoningEffort,
      maxTokens: config.maxTokens,
    }).filter(([, value]) => value !== undefined),
  );
}

function sessionMeta(config) {
  const meta = { cwd: config.cwd };
  if (config.agentPreset) meta.agentPreset = config.agentPreset;
  return meta;
}

function helpText() {
  return [
    "DeepSeek Harness Telegram controller",
    "",
    "/dsh run <任务> — 在固定工作目录中提交任务",
    "/dsh status — 查看当前会话状态",
    "/dsh cancel — 停止当前回合并清空待处理输入",
    "/dsh new — 创建一个新的 Harness 会话",
    "/dsh help — 显示本帮助",
    "",
    "Telegram 不能更改工作目录、模型凭据、沙箱或审批策略。",
  ].join("\n");
}

function limitText(text, maximum) {
  const points = Array.from(text);
  if (points.length <= maximum) return text;
  return `${points.slice(0, maximum).join("")}\n\n[输出已在安全上限处截断]`;
}

/**
 * Stateful command router shared by the real long poller and isolated tests.
 * It relies only on the public ctx.agents interface documented by Harness.
 */
export class DeepSeekTelegramController {
  constructor({ ctx, api, config, stateStore, logger = console }) {
    if (!ctx?.agents) throw new Error("deepseek_harness_agents_service_required");
    if (typeof ctx.on !== "function") throw new Error("deepseek_harness_event_context_required");
    this.ctx = ctx;
    this.api = api;
    this.config = config;
    this.stateStore = stateStore;
    this.logger = logger;
    this.state = stateStore.load();
    this.handles = new Map();
    this.active = new Map();
    this.cancelled = new Set();
    this.abortController = null;
    this.pollPromise = null;
  }

  start() {
    if (this.pollPromise) return;
    this.abortController = new AbortController();
    this.pollPromise = this.#poll(this.abortController.signal).catch((error) => {
      if (!this.abortController?.signal.aborted) {
        this.logger.error?.(
          `pao-telegram-controller stopped: ${errorText(error, [this.config.token])}`,
        );
      }
    });
  }

  async stop() {
    this.abortController?.abort();
    await this.pollPromise;
    this.pollPromise = null;
    const handles = [...this.handles.values()].filter((entry) => entry.owned).map((entry) => entry.handle);
    this.handles.clear();
    await Promise.allSettled(handles.map((handle) => handle.dispose()));
  }

  async #poll(signal) {
    if (this.state.offset === null && this.config.ignorePendingOnFirstStart) {
      const pending = await this.api.getUpdates({ offset: -1, timeoutSeconds: 0, signal });
      const latest = Array.isArray(pending)
        ? pending.reduce(
            (value, update) =>
              Number.isSafeInteger(update?.update_id) ? Math.max(value, update.update_id) : value,
            -1,
          )
        : -1;
      if (latest >= 0) {
        this.state.offset = latest + 1;
        this.stateStore.save(this.state);
        this.logger.info?.("pao-telegram-controller ignored queued pre-activation updates");
      }
    }

    while (!signal.aborted) {
      try {
        const updates = await this.api.getUpdates({
          offset: this.state.offset,
          timeoutSeconds: this.config.pollTimeoutSeconds,
          signal,
        });
        if (!Array.isArray(updates)) throw new Error("telegram_updates_must_be_an_array");
        for (const update of updates.sort((a, b) => (a?.update_id ?? 0) - (b?.update_id ?? 0))) {
          if (signal.aborted) return;
          if (!Number.isSafeInteger(update?.update_id)) continue;
          try {
            await this.handleUpdate(update);
          } catch (error) {
            this.logger.warn?.(
              `pao-telegram-controller update rejected: ${errorText(error, [this.config.token])}`,
            );
          } finally {
            this.state.offset = update.update_id + 1;
            this.stateStore.save(this.state);
          }
        }
      } catch (error) {
        if (signal.aborted) return;
        this.logger.warn?.(
          `pao-telegram-controller poll failed: ${errorText(error, [this.config.token])}`,
        );
        await sleep(1_000, signal);
      }
    }
  }

  #authorized(message) {
    let userId;
    let chatId;
    try {
      userId = asId(message?.from?.id, "telegram_user_id");
      chatId = asId(message?.chat?.id, "telegram_chat_id");
    } catch {
      return null;
    }
    if (!this.config.allowedUserIds.has(userId) || !this.config.allowedChatIds.has(chatId)) {
      this.logger.warn?.("pao-telegram-controller rejected a non-allowlisted command");
      return null;
    }
    return { userId, chatId };
  }

  async handleUpdate(update) {
    const message = update?.message;
    const parsed = parseTelegramCommand(message?.text);
    if (!parsed) return { accepted: false, reason: "not_a_dsh_command" };
    const identity = this.#authorized(message);
    if (!identity) return { accepted: false, reason: "not_allowlisted" };

    const chatId = identity.chatId;
    switch (parsed.command) {
      case "help":
        await this.api.sendText(chatId, helpText());
        return { accepted: true, command: "help" };
      case "status":
        await this.api.sendText(chatId, this.#statusText(chatId));
        return { accepted: true, command: "status" };
      case "cancel":
        return this.#cancel(chatId);
      case "new":
        return this.#newSession(chatId, parsed.argument);
      case "run":
        return this.#run(chatId, parsed.argument);
      case "invalid":
      default:
        await this.api.sendText(chatId, "不支持的 /dsh 子命令。使用 /dsh help 查看允许的操作。");
        return { accepted: false, reason: "unsupported_command" };
    }
  }

  #statusText(chatId) {
    const entry = this.handles.get(chatId);
    const persisted = this.state.chats[chatId];
    if (!persisted) return "DeepSeek Harness：尚未创建 Telegram 会话。使用 /dsh new 或 /dsh run <任务>。";
    const running = this.active.has(chatId) || entry?.handle.agent.status === "running";
    const attachment = entry ? "已连接" : "可在下次任务时恢复";
    return [
      `DeepSeek Harness：${running ? "运行中" : "空闲"}`,
      `会话：${persisted.sessionId}`,
      `状态：${attachment}`,
      `工作区：${this.config.workspaceLabel}`,
    ].join("\n");
  }

  async #cancel(chatId) {
    const entry = this.handles.get(chatId);
    if (!this.active.has(chatId)) {
      await this.api.sendText(chatId, "当前没有由 Telegram 启动的运行中任务。");
      return { accepted: false, reason: "not_running" };
    }
    this.cancelled.add(chatId);
    // Agent creation/resume is asynchronous. A cancel arriving in that small
    // window marks the run cancelled; #execute checks the marker before it can
    // enqueue the prompt. Once attached, use Harness's typed cancel boundary.
    entry?.handle.agent.cancel({ kind: "user" });
    await this.api.sendText(chatId, "已请求 DeepSeek Harness 停止当前回合。待处理输入也已清空。");
    return { accepted: true, command: "cancel" };
  }

  async #newSession(chatId, argument) {
    if (argument) {
      await this.api.sendText(chatId, "/dsh new 不接受路径或其他参数；工作目录由本机插件配置固定。");
      return { accepted: false, reason: "new_rejects_arguments" };
    }
    if (this.active.has(chatId)) {
      await this.api.sendText(chatId, "当前任务仍在运行。先使用 /dsh cancel，等待停止后再新建会话。");
      return { accepted: false, reason: "running" };
    }
    const previous = this.handles.get(chatId);
    if (previous?.owned) await previous.handle.dispose();
    this.handles.delete(chatId);
    const entry = await this.#create(chatId);
    await this.api.sendText(chatId, `已创建新的 DeepSeek Harness 会话：${entry.handle.agent.id}`);
    return { accepted: true, command: "new", sessionId: entry.handle.agent.id };
  }

  async #run(chatId, argument) {
    const prompt = argument.trim();
    if (!prompt) {
      await this.api.sendText(chatId, "用法：/dsh run <任务描述>");
      return { accepted: false, reason: "prompt_required" };
    }
    if (prompt.includes("\0")) {
      await this.api.sendText(chatId, "任务描述包含不允许的 NUL 字符。");
      return { accepted: false, reason: "invalid_prompt" };
    }
    if (Array.from(prompt).length > this.config.maxPromptChars) {
      await this.api.sendText(chatId, `任务描述过长；上限为 ${this.config.maxPromptChars} 个字符。`);
      return { accepted: false, reason: "prompt_too_long" };
    }
    if (this.active.has(chatId)) {
      await this.api.sendText(chatId, "此聊天已有一个 DeepSeek Harness 任务运行中。可使用 /dsh status 或 /dsh cancel。");
      return { accepted: false, reason: "running" };
    }

    const completion = this.#execute(chatId, prompt);
    this.active.set(chatId, completion);
    const release = () => {
      if (this.active.get(chatId) === completion) this.active.delete(chatId);
      this.cancelled.delete(chatId);
    };
    // #execute normally contains its own failures, but transport shutdown can
    // still reject the final Telegram send. A two-sided handler releases the
    // slot without creating an ignored rejecting Promise from finally().
    void completion.then(release, release);
    await this.api.sendText(chatId, "任务已提交给本机 DeepSeek Harness。使用 /dsh status 查看状态，/dsh cancel 停止。");
    return { accepted: true, command: "run", completion };
  }

  async #create(chatId) {
    const sessionId = `telegram-${randomUUID()}`;
    const handle = await this.ctx.agents.create({
      sessionId,
      meta: sessionMeta(this.config),
      agentOptions: agentOptions(this.config),
    });
    const entry = { handle, owned: true };
    this.handles.set(chatId, entry);
    this.state.chats[chatId] = { sessionId, createdAt: new Date().toISOString() };
    this.stateStore.save(this.state);
    return entry;
  }

  async #ensureHandle(chatId) {
    const attached = this.handles.get(chatId);
    if (attached) return attached;
    const saved = this.state.chats[chatId];
    if (!saved) return this.#create(chatId);

    const live = this.ctx.agents.get?.(saved.sessionId);
    if (live) {
      const entry = { handle: { agent: live, dispose: async () => {} }, owned: false };
      this.handles.set(chatId, entry);
      return entry;
    }

    const handle = await this.ctx.agents.resume({
      resumeSessionId: saved.sessionId,
      agentOptions: agentOptions(this.config),
    });
    const entry = { handle, owned: true };
    this.handles.set(chatId, entry);
    return entry;
  }

  async #execute(chatId, prompt) {
    let disposeStream = () => {};
    let disposeSession = () => {};
    let disposeError = () => {};
    try {
      const entry = await this.#ensureHandle(chatId);
      const agent = entry.handle.agent;
      if (this.cancelled.has(chatId)) return;
      const attempts = new Map();
      const streamedVisible = [];
      const durableVisible = [];
      const errors = [];

      disposeStream = this.ctx.on("agent/assistant-stream", ({ agent: subject, frame }) => {
        if (subject !== agent) return;
        if (frame.type === "start") {
          attempts.set(String(frame.attemptId), []);
          return;
        }
        if (frame.type === "chunk") {
          if (frame.chunk?.type === "text-delta" && frame.chunk.text) {
            const key = String(frame.attemptId);
            const parts = attempts.get(key) ?? [];
            parts.push(frame.chunk.text);
            attempts.set(key, parts);
          }
          return;
        }
        if (frame.type === "end") {
          const key = String(frame.attemptId);
          const text = (attempts.get(key) ?? []).join("").trim();
          attempts.delete(key);
          if (frame.outcome?.kind === "committed" && frame.outcome.eventType === "assistant/message" && text) {
            streamedVisible.push(text);
          }
        }
      });
      // dsh 0.1.2-alpha.1 publishes the authoritative assembled answer only
      // through session/event. Newer Harness builds additionally publish the
      // transient agent/assistant-stream event above. Prefer this durable path
      // when both exist so one answer is never relayed twice.
      disposeSession = this.ctx.on("session/event", (session, event) => {
        const sameSession =
          session === agent.session ||
          (typeof session?.id === "string" && session.id === agent.session?.id);
        if (!sameSession || event?.type !== "assistant/message") return;
        const content = event.data?.message?.content;
        if (!Array.isArray(content)) return;
        const text = content
          .filter((block) => block?.type === "text" && typeof block.text === "string")
          .map((block) => block.text)
          .join("")
          .trim();
        if (text) durableVisible.push(text);
      });
      disposeError = this.ctx.on("agent/error", ({ agent: subject, error }) => {
        if (subject === agent) errors.push(error);
      });

      agent.followup({
        content: [{ type: "text", text: prompt }],
        source: { kind: "user" },
      });
      await agent.whenIdle();

      if (this.cancelled.has(chatId)) return;
      if (errors.length) throw errors.at(-1);
      const visible = durableVisible.length ? durableVisible : streamedVisible;
      const result = visible.join("\n\n").trim() || "任务已经结束，但 Harness 没有产生可见文本输出。";
      await this.api.sendText(
        chatId,
        `DeepSeek Harness 已完成：\n\n${limitText(result, this.config.maxReplyChars)}`,
      );
    } catch (error) {
      if (!this.cancelled.has(chatId)) {
        this.logger.warn?.(
          `pao-telegram-controller Harness task failed: ${errorText(error, [this.config.token])}`,
        );
        await this.api.sendText(chatId, "DeepSeek Harness 任务失败；详细原因仅保留在本机日志中。");
      }
    } finally {
      disposeError();
      disposeSession();
      disposeStream();
    }
  }
}
