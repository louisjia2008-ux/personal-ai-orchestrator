import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, statSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setImmediate } from "node:timers/promises";

import {
  DeepSeekTelegramController,
  TelegramBotApi,
  TelegramStateStore,
  parseTelegramCommand,
  resolveTelegramConfig,
  telegramTextChunks,
} from "./telegram.js";

class MemoryStateStore {
  constructor(initial = { version: 1, offset: null, chats: {} }) {
    this.value = structuredClone(initial);
    this.saves = 0;
  }

  load() {
    return structuredClone(this.value);
  }

  save(value) {
    this.value = structuredClone(value);
    this.saves += 1;
  }
}

class FakeApi {
  constructor() {
    this.sent = [];
  }

  async sendText(chatId, text) {
    this.sent.push({ chatId: String(chatId), text });
    return [];
  }
}

class FakeAgentContext {
  constructor() {
    this.listeners = new Map();
  }

  on(event, listener) {
    const listeners = this.listeners.get(event) ?? new Set();
    listeners.add(listener);
    this.listeners.set(event, listeners);
    return () => listeners.delete(listener);
  }

  emit(event, ...args) {
    for (const listener of this.listeners.get(event) ?? []) listener(...args);
  }
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

class FakeAgent {
  constructor(id, { held = false, streaming = true, cloneSessionEvents = false } = {}) {
    this.id = id;
    this.session = { id };
    this.status = "idle";
    this.ctx = new FakeAgentContext();
    this.followups = [];
    this.cancelCauses = [];
    this.held = held;
    this.streaming = streaming;
    this.cloneSessionEvents = cloneSessionEvents;
    this.gate = held ? deferred() : null;
    this.idle = Promise.resolve();
  }

  followup(message) {
    this.followups.push(message);
    this.status = "running";
    this.idle = (async () => {
      if (this.gate) await this.gate.promise;
      const attemptId = `attempt-${this.followups.length}`;
      if (this.streaming) {
        this.ctx.emit("agent/assistant-stream", {
          agent: this,
          frame: { type: "start", attemptId, revision: 1, turn: 1, step: 1 },
        });
        this.ctx.emit("agent/assistant-stream", {
          agent: this,
          frame: {
            type: "chunk",
            attemptId,
            revision: 1,
            index: 0,
            time: Date.now(),
            chunk: { type: "reasoning-delta", index: 0, text: "private reasoning" },
          },
        });
        this.ctx.emit("agent/assistant-stream", {
          agent: this,
          frame: {
            type: "chunk",
            attemptId,
            revision: 1,
            index: 1,
            time: Date.now(),
            chunk: { type: "text-delta", index: 1, text: "visible result" },
          },
        });
        this.ctx.emit("agent/assistant-stream", {
          agent: this,
          frame: {
            type: "end",
            attemptId,
            revision: 1,
            index: 2,
            outcome: { kind: "committed", eventType: "assistant/message", seq: 3 },
          },
        });
      }
      this.ctx.emit(
        "session/event",
        this.cloneSessionEvents ? { ...this.session } : this.session,
        {
          type: "assistant/message",
          seq: 3,
          time: Date.now(),
          data: {
            turn: 1,
            step: 1,
            message: {
              role: "assistant",
              content: [
                { type: "reasoning", text: "private reasoning" },
                { type: "text", text: "visible result" },
              ],
            },
          },
        },
      );
      this.status = "idle";
    })();
  }

  whenIdle() {
    return this.idle;
  }

  cancel(cause) {
    this.cancelCauses.push(cause);
    this.gate?.resolve();
    this.status = "idle";
  }
}

class FakeAgents {
  constructor({
    held = false,
    creationHeld = false,
    streaming = true,
    cloneSessionEvents = false,
    events,
  } = {}) {
    this.held = held;
    this.streaming = streaming;
    this.cloneSessionEvents = cloneSessionEvents;
    this.events = events;
    this.creationGate = creationHeld ? deferred() : null;
    this.created = [];
    this.resumed = [];
    this.live = new Map();
    this.disposed = [];
  }

  async create(options) {
    this.created.push(options);
    if (this.creationGate) await this.creationGate.promise;
    const agent = new FakeAgent(options.sessionId, {
      held: this.held,
      streaming: this.streaming,
      cloneSessionEvents: this.cloneSessionEvents,
    });
    agent.ctx = this.events;
    this.live.set(agent.id, agent);
    return {
      agent,
      dispose: async () => {
        this.disposed.push(agent.id);
        this.live.delete(agent.id);
      },
    };
  }

  async resume(options) {
    this.resumed.push(options);
    const agent = new FakeAgent(options.resumeSessionId, {
      held: this.held,
      streaming: this.streaming,
      cloneSessionEvents: this.cloneSessionEvents,
    });
    agent.ctx = this.events;
    this.live.set(agent.id, agent);
    return {
      agent,
      dispose: async () => {
        this.disposed.push(agent.id);
        this.live.delete(agent.id);
      },
    };
  }

  get(id) {
    return this.live.get(id);
  }
}

function config(overrides = {}) {
  return {
    allowedUserIds: new Set(["42"]),
    allowedChatIds: new Set(["84"]),
    cwd: "/fixed/workspace",
    workspaceLabel: "workspace",
    provider: undefined,
    model: undefined,
    reasoningEffort: undefined,
    agentPreset: undefined,
    maxTokens: undefined,
    maxPromptChars: 12_000,
    maxReplyChars: 16_000,
    pollTimeoutSeconds: 25,
    ignorePendingOnFirstStart: true,
    ...overrides,
  };
}

function update(text, { updateId = 1, userId = 42, chatId = 84 } = {}) {
  return {
    update_id: updateId,
    message: { message_id: updateId, text, from: { id: userId }, chat: { id: chatId } },
  };
}

function harness({ initialState, controllerConfig, ...options } = {}) {
  const events = new FakeAgentContext();
  const agents = new FakeAgents({ ...options, events });
  const api = new FakeApi();
  const stateStore = new MemoryStateStore(initialState);
  const controller = new DeepSeekTelegramController({
    ctx: { agents, on: events.on.bind(events) },
    api,
    config: config(controllerConfig),
    stateStore,
    logger: { info() {}, warn() {}, error() {} },
  });
  return { agents, api, stateStore, controller, events };
}

test("explicit /dsh namespace parses commands and group suffixes", () => {
  assert.deepEqual(parseTelegramCommand("/dsh@my_bot run fix the tests"), {
    command: "run",
    argument: "fix the tests",
  });
  assert.deepEqual(parseTelegramCommand("/dsh"), { command: "help", argument: "" });
  assert.equal(parseTelegramCommand("run fix the tests"), null);
  assert.deepEqual(parseTelegramCommand("/dsh shell rm -rf x"), {
    command: "invalid",
    argument: "shell rm -rf x",
  });
});

test("activation requires token, dual allowlists, and an existing absolute cwd", () => {
  assert.equal(resolveTelegramConfig({ enabled: false }, {}), null);
  assert.throws(
    () =>
      resolveTelegramConfig(
        { enabled: true, cwd: tmpdir(), allowedUserIds: [1], allowedChatIds: [2] },
        {},
      ),
    /missing_telegram_bot_token_environment/,
  );
  assert.throws(
    () =>
      resolveTelegramConfig(
        { enabled: true, cwd: tmpdir(), allowedUserIds: [], allowedChatIds: [2] },
        { TELEGRAM_BOT_TOKEN: "secret" },
      ),
    /allowedUserIds_must_be_a_non_empty_array/,
  );
  const resolved = resolveTelegramConfig(
    { enabled: true, cwd: tmpdir(), allowedUserIds: [1], allowedChatIds: [-2] },
    { TELEGRAM_BOT_TOKEN: "secret" },
  );
  assert.equal(resolved.token, "secret");
  assert.deepEqual([...resolved.allowedUserIds], ["1"]);
  assert.deepEqual([...resolved.allowedChatIds], ["-2"]);
});

test("non-allowlisted commands are silent and cannot create an agent", async () => {
  const { agents, api, controller } = harness();
  const result = await controller.handleUpdate(update("/dsh run do work", { userId: 99 }));
  assert.deepEqual(result, { accepted: false, reason: "not_allowlisted" });
  assert.equal(agents.created.length, 0);
  assert.equal(api.sent.length, 0);
});

test("run pins cwd, forwards only a user prompt, and returns visible text without reasoning", async () => {
  const { agents, api, stateStore, controller } = harness();
  const result = await controller.handleUpdate(update("/dsh run inspect the repository"));
  assert.equal(result.accepted, true);
  await result.completion;

  assert.equal(agents.created.length, 1);
  assert.deepEqual(agents.created[0].meta, { cwd: "/fixed/workspace" });
  assert.deepEqual(agents.created[0].agentOptions, {});
  const agent = agents.live.get(agents.created[0].sessionId);
  assert.deepEqual(agent.followups[0], {
    content: [{ type: "text", text: "inspect the repository" }],
    source: { kind: "user" },
  });
  assert.ok(api.sent.some((message) => message.text.includes("任务已提交")));
  assert.ok(api.sent.some((message) => message.text.includes("visible result")));
  assert.ok(api.sent.every((message) => !message.text.includes("private reasoning")));
  const completed = api.sent.find((message) => message.text.includes("DeepSeek Harness 已完成"));
  assert.equal(completed.text.match(/visible result/g).length, 1);
  assert.equal(stateStore.saves, 1);
  assert.match(stateStore.value.chats["84"].sessionId, /^telegram-/);
  assert.equal(stateStore.value.chats["84"].cwd, "/fixed/workspace");
});

test("workspace changes and legacy mappings cannot resume or attach an old session", async (t) => {
  for (const cwd of [undefined, "/old/workspace"]) {
    await t.test(cwd ?? "legacy mapping", async () => {
      const { agents, api, stateStore, controller } = harness({
        initialState: {
          version: 1,
          offset: 42,
          chats: { "84": { sessionId: "old-session", cwd } },
        },
      });
      const oldAgent = new FakeAgent("old-session");
      agents.live.set(oldAgent.id, oldAgent);
      assert.equal(stateStore.value.offset, 42);
      assert.deepEqual(stateStore.value.chats, {});
      await controller.handleUpdate(update("/dsh status"));
      assert.ok(!api.sent[0].text.includes(oldAgent.id));

      const result = await controller.handleUpdate(update("/dsh run inspect workspace"));
      await result.completion;
      assert.equal(agents.resumed.length, 0);
      assert.equal(agents.created.length, 1);
      assert.deepEqual(agents.created[0].meta, { cwd: "/fixed/workspace" });
      assert.deepEqual(oldAgent.followups, []);
      assert.equal(stateStore.value.offset, 42);
      assert.equal(stateStore.value.chats["84"].cwd, "/fixed/workspace");
      await controller.stop();
    });
  }
});

test("a saved session in the same workspace resumes across controller restarts", async () => {
  const first = harness();
  const run = await first.controller.handleUpdate(update("/dsh run first task"));
  await run.completion;
  const sessionId = first.stateStore.value.chats["84"].sessionId;
  await first.controller.stop();

  const second = harness({ initialState: first.stateStore.value });
  const resumed = await second.controller.handleUpdate(update("/dsh run second task"));
  await resumed.completion;
  assert.equal(second.agents.created.length, 0);
  assert.equal(second.agents.resumed[0].resumeSessionId, sessionId);
  assert.equal(second.stateStore.saves, 0);
  await second.controller.stop();
});

test("workspace identity uses the canonical directory rather than a symlink spelling", async (t) => {
  const root = mkdtempSync(join(tmpdir(), "pao-telegram-workspace-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const workspace = join(root, "workspace");
  const alias = join(root, "alias");
  mkdirSync(workspace);
  symlinkSync(workspace, alias, "dir");
  const resolved = resolveTelegramConfig(
    { enabled: true, cwd: alias, allowedUserIds: [42], allowedChatIds: [84] },
    { TELEGRAM_BOT_TOKEN: "secret" },
  );
  assert.equal(resolved.cwd, realpathSync(workspace));
  const { agents, controller } = harness({
    controllerConfig: resolved,
    initialState: {
      version: 1,
      offset: 42,
      chats: { "84": { sessionId: "canonical-session", cwd: realpathSync(workspace) } },
    },
  });
  const result = await controller.handleUpdate(update("/dsh run continue"));
  await result.completion;
  assert.equal(agents.created.length, 0);
  assert.equal(agents.resumed[0].resumeSessionId, "canonical-session");
  await controller.stop();
});

test("busy recovered agents cannot queue prompts or expose unrelated turn output", async (t) => {
  for (const recovery of ["live", "resumed", "attached"]) {
    await t.test(recovery, async () => {
      const { agents, api, controller, events } = harness({
        initialState: {
          version: 1,
          offset: 42,
          chats: { "84": { sessionId: "busy-session", cwd: "/fixed/workspace" } },
        },
      });
      const agent = new FakeAgent("busy-session", { held: true });
      agent.ctx = events;
      if (recovery === "resumed") {
        agents.resume = async () => ({ agent, dispose: async () => {} });
      } else {
        agents.live.set(agent.id, agent);
      }
      if (recovery === "attached") {
        const initial = await controller.handleUpdate(update("/dsh run initial task"));
        agent.gate.resolve();
        await initial.completion;
        agent.gate = deferred();
        api.sent.length = 0;
      }
      agent.followup({ content: [{ type: "text", text: "local-only task" }] });
      const priorFollowups = agent.followups.length;
      const result = await controller.handleUpdate(update("/dsh run telegram task"));
      // The controller should return without waiting for the external turn.
      await result.completion;
      assert.equal(agent.followups.length, priorFollowups);
      assert.equal(controller.active.size, 0);
      assert.ok([...events.listeners.values()].every((listeners) => listeners.size === 0));
      assert.ok(api.sent.some((message) => message.text.includes("请等待")));
      assert.ok(api.sent.every((message) => !message.text.includes("任务已提交")));
      agent.gate.resolve();
      await agent.whenIdle();
      assert.ok(api.sent.every((message) => !message.text.includes("visible result")));
      assert.deepEqual(agent.cancelCauses, []);
      await controller.stop();
    });
  }
});

test("initial polling failures retry bootstrap before accepting new commands", async (t) => {
  for (const failure of ["transport", "malformed response"]) {
    await t.test(failure, async (t) => {
      t.mock.timers.enable({ apis: ["setTimeout"] });
      const { api, stateStore, controller, agents } = harness();
      const failed = deferred();
      const polled = deferred();
      const requests = [];
      const errors = [];
      controller.logger = { warn: () => failed.resolve(), error: (message) => errors.push(message) };
      api.getUpdates = async ({ offset, timeoutSeconds, signal }) => {
        requests.push({ offset, timeoutSeconds });
        if (requests.length === 1) {
          if (failure === "transport") throw new Error("temporary transport failure");
          return { malformed: true };
        }
        if (requests.length === 2) return [update("/dsh run stale task", { updateId: 100 })];
        if (requests.length === 3) return [update("/dsh help", { updateId: 101 })];
        polled.resolve();
        return new Promise((resolve) => signal.addEventListener("abort", () => resolve([]), { once: true }));
      };
      controller.start();
      await failed.promise;
      assert.equal(requests.length, 1);
      t.mock.timers.tick(1_000);
      await polled.promise;
      assert.deepEqual(requests, [
        { offset: -1, timeoutSeconds: 0 },
        { offset: -1, timeoutSeconds: 0 },
        { offset: 101, timeoutSeconds: 25 },
        { offset: 102, timeoutSeconds: 25 },
      ]);
      assert.equal(stateStore.value.offset, 102);
      assert.equal(agents.created.length, 0);
      assert.equal(api.sent.length, 1);
      assert.ok(api.sent[0].text.includes("DeepSeek Harness Telegram controller"));
      assert.deepEqual(errors, []);
      await controller.stop();
    });
  }
});

test("stop interrupts the initial-poll retry delay without another request", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { api, controller } = harness();
  const failed = deferred();
  let requests = 0;
  controller.logger = { warn: () => failed.resolve() };
  api.getUpdates = async () => {
    requests += 1;
    throw new Error("temporary transport failure");
  };
  controller.start();
  await failed.promise;
  await controller.stop();
  t.mock.timers.tick(1_000);
  assert.equal(requests, 1);
});

test("the installed Harness durable session event works without assistant streaming", async () => {
  const { api, controller } = harness({ streaming: false, cloneSessionEvents: true });
  const result = await controller.handleUpdate(update("/dsh run use durable events"));
  await result.completion;

  const completed = api.sent.find((message) => message.text.includes("DeepSeek Harness 已完成"));
  assert.ok(completed.text.includes("visible result"));
  assert.ok(!completed.text.includes("private reasoning"));
});

test("cancel stops only the Telegram-owned active turn with a typed user cause", async () => {
  const { agents, api, controller } = harness({ held: true });
  const run = await controller.handleUpdate(update("/dsh run long task"));
  const cancelled = await controller.handleUpdate(update("/dsh cancel", { updateId: 2 }));
  assert.equal(cancelled.accepted, true);
  await run.completion;

  const agent = agents.live.get(agents.created[0].sessionId);
  assert.deepEqual(agent.cancelCauses, [{ kind: "user" }]);
  assert.ok(api.sent.some((message) => message.text.includes("已请求")));
  assert.ok(api.sent.every((message) => !message.text.includes("DeepSeek Harness 已完成")));
});

test("cancel during asynchronous Agent creation prevents the prompt from starting", async () => {
  const { agents, api, controller } = harness({ creationHeld: true });
  const run = await controller.handleUpdate(update("/dsh run do not start"));
  const cancelled = await controller.handleUpdate(update("/dsh cancel", { updateId: 2 }));
  assert.equal(cancelled.accepted, true);
  agents.creationGate.resolve();
  await run.completion;

  const agent = agents.live.get(agents.created[0].sessionId);
  assert.deepEqual(agent.followups, []);
  assert.ok(api.sent.every((message) => !message.text.includes("DeepSeek Harness 已完成")));
});

test("new rejects remote paths and never turns Telegram text into cwd", async () => {
  const { agents, api, controller } = harness();
  const result = await controller.handleUpdate(update("/dsh new /tmp/other"));
  assert.equal(result.accepted, false);
  assert.equal(agents.created.length, 0);
  assert.ok(api.sent[0].text.includes("不接受路径"));
});

test("state is credential-free, atomic, and permission restricted", (t) => {
  const root = mkdtempSync(join(tmpdir(), "pao-telegram-state-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const path = join(root, "nested", "state.json");
  const store = new TelegramStateStore(path);
  store.save({
    version: 1,
    offset: 9,
    chats: { "84": { sessionId: "telegram-test", createdAt: "2026-09-29T00:00:00Z" } },
  });
  assert.equal(statSync(path).mode & 0o777, 0o600);
  assert.deepEqual(store.load().chats["84"].sessionId, "telegram-test");
  assert.doesNotMatch(readFileSync(path, "utf8"), /BOT_TOKEN|secret/i);
});

test("Bot API errors and logs cannot disclose the token", async () => {
  const token = "12345:super-secret";
  const api = new TelegramBotApi({
    token,
    fetchImpl: async () =>
      new Response(JSON.stringify({ ok: false, error_code: 401, description: "Unauthorized" }), {
        status: 401,
        headers: { "content-type": "application/json" },
      }),
  });
  await assert.rejects(
    api.getUpdates({ offset: null, timeoutSeconds: 1 }),
    (error) => !String(error).includes(token) && /telegram_api_error:401/.test(String(error)),
  );

  const transportApi = new TelegramBotApi({
    token,
    fetchImpl: async (url) => {
      throw new Error(`network failed for ${url}`);
    },
  });
  await assert.rejects(
    transportApi.getUpdates({ offset: null, timeoutSeconds: 1 }),
    (error) => !String(error).includes(token) && /transport_failed/.test(String(error)),
  );
});

test("Telegram responses stay below the transport ceiling", () => {
  const chunks = telegramTextChunks("🛠".repeat(8_500));
  assert.equal(chunks.length, 5);
  assert.ok(chunks.every((chunk) => chunk.length <= 3_900));
  assert.equal(chunks.join(""), "🛠".repeat(8_500));
});

test("cancel during a busy-session rejection cannot stop the external turn", async () => {
  const { agents, api, controller, events } = harness({
    initialState: {
      version: 1,
      offset: 42,
      chats: { "84": { sessionId: "busy-session", cwd: "/fixed/workspace" } },
    },
  });
  const agent = new FakeAgent("busy-session", { held: true });
  agent.ctx = events;
  agents.live.set(agent.id, agent);
  agent.followup({ content: [{ type: "text", text: "local-only task" }] });
  const receipt = deferred();
  const rejecting = deferred();
  const sendText = api.sendText.bind(api);
  api.sendText = async (chatId, text) => {
    if (text.includes("请等待")) {
      rejecting.resolve();
      await receipt.promise;
    }
    return sendText(chatId, text);
  };
  const run = await controller.handleUpdate(update("/dsh run telegram task"));
  await rejecting.promise;
  await controller.handleUpdate(update("/dsh cancel"));
  assert.deepEqual(agent.cancelCauses, []);
  assert.equal(agent.status, "running");
  receipt.resolve();
  await run.completion;
  agent.gate.resolve();
  await agent.whenIdle();
  assert.ok(api.sent.every((message) => !message.text.includes("visible result")));
  await controller.stop();
});

test("failed task receipts retain the active turn and its cancellation handle", async () => {
  const { agents, api, controller } = harness({ held: true });
  const sendText = api.sendText.bind(api);
  const failed = deferred();
  controller.logger = { warn: () => failed.resolve() };
  api.sendText = async (chatId, text) => {
    if (text.includes("任务已提交")) throw new Error("receipt transport failure");
    return sendText(chatId, text);
  };
  const run = await controller.handleUpdate(update("/dsh run long task"));
  await failed.promise;
  await setImmediate();
  assert.equal(controller.active.size, 1);
  const duplicate = await controller.handleUpdate(update("/dsh run another task"));
  assert.equal(duplicate.reason, "running");
  await controller.handleUpdate(update("/dsh cancel"));
  await run.completion;
  const agent = agents.live.get(agents.created[0].sessionId);
  assert.deepEqual(agent.cancelCauses, [{ kind: "user" }]);
  assert.equal(agent.followups.length, 1);
  assert.equal(controller.active.size, 0);
  assert.ok(api.sent.every((message) => !message.text.includes("任务失败")));
  await controller.stop();
});

test("later local output stays isolated while Telegram delivery is pending", async (t) => {
  for (const delay of ["receipt", "result"]) {
    await t.test(delay, async () => {
      const { agents, api, controller, events } = harness();
      const delivery = deferred();
      const pending = deferred();
      const sendText = api.sendText.bind(api);
      api.sendText = async (chatId, text) => {
        if (text.includes(delay === "receipt" ? "任务已提交" : "DeepSeek Harness 已完成")) {
          pending.resolve();
          await delivery.promise;
        }
        return sendText(chatId, text);
      };
      const run = await controller.handleUpdate(update("/dsh run telegram task"));
      await pending.promise;
      await setImmediate();
      assert.ok([...events.listeners.values()].every((listeners) => listeners.size === 0));
      const agent = agents.live.get(agents.created[0].sessionId);
      agent.status = "running";
      events.emit("session/event", agent.session, {
        type: "assistant/message",
        data: { message: { content: [{ type: "text", text: "private later output" }] } },
      });
      events.emit("agent/error", { agent, error: new Error("unrelated local error") });
      if (delay === "result") {
        await controller.handleUpdate(update("/dsh cancel"));
        assert.deepEqual(agent.cancelCauses, []);
      }
      delivery.resolve();
      await run.completion;
      assert.ok(api.sent.some((message) => message.text.includes("visible result")));
      assert.ok(api.sent.every((message) => !message.text.includes("private later output")));
      assert.ok(api.sent.every((message) => !message.text.includes("任务失败")));
      agent.status = "idle";
      await controller.stop();
    });
  }
});

test("failed submissions release cancel ownership before sending an error", async () => {
  const { agents, api, controller, events } = harness();
  await controller.handleUpdate(update("/dsh new"));
  const agent = agents.live.get(agents.created[0].sessionId);
  agent.followup = () => { throw new Error("cannot submit"); };
  const delivery = deferred();
  const pending = deferred();
  const sendText = api.sendText.bind(api);
  api.sendText = async (chatId, text) => {
    if (text.includes("任务失败")) {
      pending.resolve();
      await delivery.promise;
    }
    return sendText(chatId, text);
  };
  const run = await controller.handleUpdate(update("/dsh run rejected task"));
  await pending.promise;
  assert.ok([...events.listeners.values()].every((listeners) => listeners.size === 0));
  agent.status = "running";
  await controller.handleUpdate(update("/dsh cancel"));
  assert.deepEqual(agent.cancelCauses, []);
  delivery.resolve();
  await run.completion;
  agent.status = "idle";
  await controller.stop();
});
