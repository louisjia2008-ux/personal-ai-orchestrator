/**
 * pao-dsh-dispatch — Jev quota-aware model dispatch for DeepSeek Harness.
 *
 * Registers one Agent-scoped waterfall listener on `agent/request`: for the
 * first step of each turn it runs the dispatch core (live pool probes →
 * hard gates → Jev decision → burn-first policy) and rewrites the request
 * config's provider/model to the chosen target. Later steps in the same
 * turn reuse the decision. Failures never block the agent: they log and
 * fall through to the seed config.
 *
 * Requires TYPESAFE_API_KEY in the environment (Jev decision layer).
 * Plugin config (cordis.patch.yml `config:`):
 *   auto: true            dispatch every turn (default)
 *   providers: [...]      override provider-route discovery
 *   keyEnv: {zai, minimax, deepseek}   env var names for pool keys
 */

import { appendFileSync } from "node:fs";
import { dispatchTask, resolveKeys, configuredProviders } from "./dispatch.js";

export const name = "pao-dsh-dispatch";

function debugFile(line) {
  const path = process.env.DSH_DISPATCH_DEBUG_FILE;
  if (path) {
    try {
      appendFileSync(path, JSON.stringify({ t: new Date().toISOString(), ...line }) + "\n");
    } catch {
      /* best effort */
    }
  }
}

function lastUserText(messages) {
  let text = "";
  for (const m of messages ?? []) {
    const blocks = Array.isArray(m?.content) ? m.content : [];
    const joined = blocks
      .filter((b) => b?.type === "text" && typeof b.text === "string")
      .map((b) => b.text)
      .join("\n")
      .trim();
    if (joined) text = joined;
  }
  return text;
}

export function apply(ctx, config = {}) {
  const enabled = config.auto !== false;
  const keys = resolveKeys(config.keyEnv ?? {});
  const providers = Array.isArray(config.providers) && config.providers.length
    ? new Set(config.providers)
    : configuredProviders();

  ctx.logger?.info?.(
    `pao-dsh-dispatch loaded: auto=${enabled}, routes=[${[...providers].join(",")}]`,
  );
  debugFile({ event: "loaded", routes: [...providers], keys: Object.keys(keys) });

  if (!process.env.TYPESAFE_API_KEY) {
    ctx.logger?.warn?.(
      "TYPESAFE_API_KEY not set — dispatch disabled, requests keep their seed model",
    );
    return;
  }

  /** agentId → { turn, prompt, decision } for the current turn. */
  const perAgent = new Map();

  ctx.on("agent/pre-step", (payload, next) => {
    const agentId = payload?.agent?.id;
    if (agentId !== undefined) {
      const prompt = lastUserText(payload.messages);
      const prev = perAgent.get(agentId);
      if (prompt) {
        perAgent.set(agentId, { turn: payload.turn, prompt, decision: prev?.decision });
      }
    }
    return next();
  });

  ctx.on(
    "agent/request",
    async (payload, next) => {
      const resolved = await next();
      if (!enabled) return resolved;

      const agentId = payload?.agent?.id;
      const entry = perAgent.get(agentId);
      if (!entry || payload.turn !== entry.turn || !entry.prompt) return resolved;

      if (entry.decision === undefined) {
        try {
          entry.decision = await dispatchTask(entry.prompt, { keys, providers });
        } catch (e) {
          entry.decision = null;
          debugFile({ event: "dispatch-error", error: String(e?.message ?? e) });
          ctx.logger?.warn?.(`pao-dsh-dispatch failed: ${e?.message ?? e}`);
          return resolved;
        }
        debugFile({ event: "decision", prompt: entry.prompt.slice(0, 80), result: entry.decision });
        if (!entry.decision?.decision) {
          ctx.logger?.warn?.(
            `pao-dsh-dispatch: no executable target — ${entry.decision?.reason ?? "unknown"}`,
          );
          return resolved;
        }
        const d = entry.decision;
        const quota = (d.quotaZai ?? [])
          .map((w) => `${w.kind} ${Math.round(w.used * 100)}% ${w.pressure}`)
          .join(", ");
        ctx.logger?.info?.(
          `pao-dsh-dispatch: ${d.decision} ${d.mode} conf=${d.confidence?.toFixed?.(2)} tier=${d.tierScore?.toFixed?.(1)}${quota ? ` zai=[${quota}]` : ""}`,
        );
      }

      const d = entry.decision;
      if (!d?.target) return resolved;
      const t = d.target;
      if (resolved.provider === t.provider && resolved.model === t.model) return resolved;
      return { ...resolved, provider: t.provider, model: t.model };
    },
  );
}
