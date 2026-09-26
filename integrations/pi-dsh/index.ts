/**
 * pi-dsh — Jev quota-aware model dispatch as a pi extension.
 *
 * Commands:
 *   /dsh              status (enabled? last decision?)
 *   /dsh on|off       toggle auto-dispatch per turn
 *   /dsh pick <task>  one-shot decision for an arbitrary task text
 *
 * When enabled, every submitted prompt passes through the dispatch core
 * (live pool probes → hard gates → Jev → policy) before the agent run;
 * the session model is switched to the chosen target via pi.setModel().
 * Failures never block the agent — they notify and fall back to the
 * current model.
 *
 * Needs TYPESAFE_API_KEY in the environment (Jev decision layer).
 * DSH_AUTO=1 starts with auto-dispatch enabled.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { dispatchTask, TARGETS, type DispatchResult } from "./dispatch";

function summary(d: DispatchResult): string {
  if (!d.decision) return `dsh: no executable target — ${d.reason}`;
  const quota = (d.quotaZai ?? [])
    .map((w) => `${Math.round(w.used * 100)}% ${w.pressure}`)
    .join(", ");
  const bal =
    d.deepseekBalanceCny === null || d.deepseekBalanceCny === undefined
      ? ""
      : ` | DS ¥${d.deepseekBalanceCny.toFixed(2)}`;
  const s = `dsh: ${d.decision} (${d.mode}, conf ${(d.confidence ?? 0).toFixed(2)}, tier ${(d.tierScore ?? 0).toFixed(1)})${quota ? ` | ZAI ${quota}` : ""}${bal}`;
  if (process.env.DSH_DEBUG) console.error(`[dsh] ${s}`);
  return s;
}

export default function (pi: ExtensionAPI) {
  let enabled = process.env.DSH_AUTO === "1";
  let last: DispatchResult | null = null;
  if (process.env.DSH_DEBUG) console.error("[dsh] extension loaded, auto=" + enabled);

  pi.registerCommand("dsh", {
    description: "Jev quota-aware model dispatch (on|off|pick <task>)",
    handler: async (args: string, ctx: any) => {
      const [sub, ...rest] = (args ?? "").trim().split(/\s+/);
      if (sub === "on") {
        enabled = true;
        ctx.ui.notify("dsh: auto-dispatch ON", "info");
      } else if (sub === "off") {
        enabled = false;
        ctx.ui.notify("dsh: auto-dispatch OFF", "info");
      } else if (sub === "pick") {
        const task = rest.join(" ").trim();
        if (!task) {
          ctx.ui.notify("dsh: usage /dsh pick <task text>", "warn");
          return;
        }
        try {
          last = await dispatchTask(task);
          ctx.ui.notify(summary(last), "info");
        } catch (e: any) {
          ctx.ui.notify(`dsh: dispatch failed — ${e?.message ?? e}`, "error");
        }
      } else {
        ctx.ui.notify(
          `dsh: auto-dispatch ${enabled ? "ON" : "OFF"}${last ? ` | last: ${last.decision ?? "none"}` : ""}`,
          "info",
        );
      }
    },
  });

  pi.on("before_agent_start", async (event: any, ctx: any) => {
    if (process.env.DSH_DEBUG)
      console.error(`[dsh] before_agent_start enabled=${enabled} prompt=${(event.prompt ?? "").length}ch`);
    if (!enabled) return;
    const prompt = (event.prompt ?? "").trim();
    if (!prompt) return;

    let d: DispatchResult;
    try {
      d = await dispatchTask(prompt);
    } catch (e: any) {
      if (process.env.DSH_DEBUG) console.error(`[dsh] dispatch failed: ${e?.message ?? e}`);
      ctx.ui.notify(`dsh: dispatch failed — ${e?.message ?? e}`, "error");
      return; // never block the agent
    }
    last = d;

    if (!d.decision || !d.target) {
      if (process.env.DSH_DEBUG) console.error(`[dsh] no executable target: ${d.reason}`);
      ctx.ui.notify(`dsh: no executable target — ${d.reason}`, "error");
      return;
    }

    const t = d.target;
    const cur = ctx.model;
    const curLabel = cur ? `${cur.provider}/${cur.id}` : "none";
    if (cur && cur.provider === t.provider && cur.id === t.model) {
      ctx.ui.notify(summary(d) + " — keep current", "info");
      return;
    }

    const model = ctx.modelRegistry?.find?.(t.provider, t.model);
    if (!model) {
      if (process.env.DSH_DEBUG) console.error(`[dsh] ${t.provider}/${t.model} not in catalogue`);
      ctx.ui.notify(`dsh: ${t.provider}/${t.model} not in model catalogue`, "error");
      return;
    }
    const ok = await pi.setModel(model);
    if (process.env.DSH_DEBUG)
      console.error(`[dsh] switch ${curLabel} -> ${t.provider}/${t.model} ok=${ok} (${d.mode}, conf ${(d.confidence ?? 0).toFixed(2)}, tier ${(d.tierScore ?? 0).toFixed(1)})`);
    if (ok) {
      ctx.ui.notify(
        `dsh: ${curLabel} → ${t.provider}/${t.model} (conf ${(d.confidence ?? 0).toFixed(2)}, tier ${(d.tierScore ?? 0).toFixed(1)})`,
        "info",
      );
      if (d.deferAdvice) ctx.ui.notify(`dsh ⚠ ${d.deferAdvice}`, "warn");
    } else {
      ctx.ui.notify(`dsh: setModel failed for ${t.provider}/${t.model}`, "error");
    }
  });
}
