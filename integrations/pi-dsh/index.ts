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
 * An unavailable admission or model switch consumes the input before any
 * provider request. Steering is accepted only with auto-dispatch disabled.
 *
 * Needs TYPESAFE_API_KEY in the environment (Jev decision layer).
 * DSH_AUTO=1 starts with auto-dispatch enabled.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { dispatchTask, TARGETS, type DispatchResult } from "./dispatch.ts";

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

function notify(ctx: any, message: string, level: string) {
  try { ctx.ui.notify(message, level); } catch { /* UI availability never authorizes a request. */ }
}

export function createDshExtension(dispatch: typeof dispatchTask = dispatchTask) {
  return function (pi: ExtensionAPI) {
    let enabled = process.env.DSH_AUTO === "1";
    let last: DispatchResult | null = null;
    let admissionPending = false;
    if (process.env.DSH_DEBUG) console.error("[dsh] extension loaded, auto=" + enabled);

    pi.registerCommand("dsh", {
      description: "Jev quota-aware model dispatch (on|off|pick <task>)",
      handler: async (args: string, ctx: any) => {
        const [sub, ...rest] = (args ?? "").trim().split(/\s+/);
        if (sub === "on") {
          enabled = true;
          notify(ctx, "dsh: auto-dispatch ON", "info");
        } else if (sub === "off") {
          enabled = false;
          notify(ctx, "dsh: auto-dispatch OFF", "info");
        } else if (sub === "pick") {
          const task = rest.join(" ").trim();
          if (!task) {
            notify(ctx, "dsh: usage /dsh pick <task text>", "warn");
            return;
          }
          try {
            last = await dispatch(task);
            notify(ctx, summary(last), "info");
          } catch (e: any) {
            notify(ctx, "dsh: dispatch unavailable", "error");
          }
        } else {
          notify(ctx,
            `dsh: auto-dispatch ${enabled ? "ON" : "OFF"}${last ? ` | last: ${last.decision ?? "none"}` : ""}`,
            "info",
          );
        }
      },
    });

    pi.on("input", async (event: any, ctx: any) => {
      if (process.env.DSH_DEBUG)
        console.error(`[dsh] input enabled=${enabled} prompt=${(event.text ?? "").length}ch`);
      if (!enabled) return;
      const prompt = (event.text ?? "").trim();
      if (!prompt || !ctx.isIdle()) {
        notify(ctx, "dsh: submit a text task while the agent is idle", "error");
        return { action: "handled" };
      }

      if (admissionPending) {
        notify(ctx, "dsh: another input is awaiting admission; retry when idle", "error");
        return { action: "handled" };
      }
      admissionPending = true;
      try {
        const d = await dispatch(prompt);
        last = d;
        if (!["ALLOW", "SAFE_FALLBACK"].includes(d.action) || !d.decision || !d.target) {
          notify(ctx, `dsh: execution blocked — ${d.reason}`, "error");
          return { action: "handled" };
        }
        const target = d.target;
        const current = ctx.model;
        if (current?.provider === target.provider && current.id === target.model) {
          notify(ctx, summary(d) + " — keep current", "info");
          return;
        }
        const model = ctx.modelRegistry?.find?.(target.provider, target.model);
        if (!model) {
          notify(ctx, "dsh: admitted target is unavailable in the model catalogue", "error");
          return { action: "handled" };
        }
        if (!await pi.setModel(model)) {
          notify(ctx, "dsh: model switch failed; input was not sent", "error");
          return { action: "handled" };
        }
        notify(ctx, summary(d), "info");
        if (d.deferAdvice) notify(ctx, `dsh: ${d.deferAdvice}`, "warn");
      } catch {
        // Pi contains extension exceptions and otherwise CONTINUES the input.
        // Always return handled for failures anywhere in admission/model switching.
        notify(ctx, "dsh: admission or model switch unavailable; input was not sent", "error");
        return { action: "handled" };
      } finally {
        admissionPending = false;
      }
    });
  };
}

export default createDshExtension();
