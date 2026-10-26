/** Quota-aware dispatch. Every turn must receive an admitted target before a request. */
import { dispatchTask, resolveKeys, configuredProviders } from "./dispatch.js";

export const name = "pao-dsh-dispatch";

export class DispatchBlockedError extends Error {
  constructor(reason) {
    super(`pao-dsh-dispatch blocked: ${reason}`);
    this.name = "DispatchBlockedError";
    this.code = "PAO_DISPATCH_BLOCKED";
  }
}

function lastUserText(messages) {
  let text = "";
  for (const message of messages ?? []) {
    if (message?.role !== "user") continue;
    const content = message.content;
    const joined = typeof content === "string" ? content.trim() : (Array.isArray(content) ? content : [])
      .filter((b) => b?.type === "text" && typeof b.text === "string")
      .map((b) => b.text).join("\n").trim();
    if (joined) text = joined;
  }
  return text;
}

export function apply(ctx, config = {}, { dispatch = dispatchTask, keys: suppliedKeys, providers: suppliedProviders } = {}) {
  const enabled = config.auto !== false;
  const keys = suppliedKeys ?? resolveKeys(config.keyEnv ?? {});
  const providers = suppliedProviders ?? (Array.isArray(config.providers) && config.providers.length
    ? new Set(config.providers) : configuredProviders());
  const perAgent = new Map();
  ctx.logger?.info?.(`pao-dsh-dispatch loaded: auto=${enabled}`);

  ctx.on("agent/pre-step", (payload, next) => {
    const id = payload?.agent?.id;
    const previous = perAgent.get(id);
    const prompt = lastUserText(payload?.messages) ||
      (previous?.turn === payload.turn ? previous.prompt : "");
    if (id !== undefined && (!previous || previous.turn !== payload.turn || previous.prompt !== prompt)) {
      perAgent.set(id, { turn: payload.turn, prompt, pending: undefined });
    }
    return next();
  });

  ctx.on("agent/disposed", (payload) => { perAgent.delete(payload?.agent?.id); });
  ctx.on("dispose", () => { perAgent.clear(); });

  ctx.on("agent/request", async (payload, next) => {
    if (!enabled) return next();
    const id = payload?.agent?.id;
    const entry = perAgent.get(id);
    if (!entry || entry.turn !== payload.turn || !entry.prompt) {
      throw new DispatchBlockedError("turn context is unavailable");
    }
    entry.pending ??= Promise.resolve().then(() => dispatch(entry.prompt, { keys, providers }))
      .catch(() => ({ action: "BLOCK", decision: null, reason: "dispatch unavailable" }));
    const result = await entry.pending;
    if (perAgent.get(id) !== entry) throw new DispatchBlockedError("turn changed during admission");
    if (!["ALLOW", "SAFE_FALLBACK"].includes(result?.action) || !result?.target || !result.decision) {
      throw new DispatchBlockedError(result?.reason ?? "no admitted target");
    }
    const resolved = await next();
    if (perAgent.get(id) !== entry) throw new DispatchBlockedError("turn changed during request resolution");
    return { ...resolved, provider: result.target.provider, model: result.target.model };
  });
}
