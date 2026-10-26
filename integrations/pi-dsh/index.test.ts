import { test } from "node:test";
import assert from "node:assert/strict";
import { createDshExtension } from "./index.ts";

test("Pi consumes blocked inputs before inference; only admitted model switches continue", async () => {
  const old = process.env.DSH_AUTO;
  process.env.DSH_AUTO = "1";
  try {
    for (const failure of ["gate", "dispatch", "catalogue", "catalogue-throws", "switch", "switch-throws", "none"]) {
      const hooks = new Map();
      const notices: string[] = [];
      const pi: any = {
        registerCommand() {}, on: (name: string, handler: any) => hooks.set(name, handler),
        setModel: async () => {
          if (failure === "switch-throws") throw new Error("auth changed while switching");
          return failure !== "switch";
        },
      };
      createDshExtension(async () => {
        if (failure === "dispatch") throw new Error("offline");
        if (failure === "gate") return { action: "BLOCK", decision: null, reason: "quota unknown" };
        return { action: "ALLOW", decision: "admitted", target: { tid: "test", provider: "test", model: "test", tier: 3, pool: "test" } };
      })(pi);
      assert.equal(hooks.has("before_agent_start"), false);
      const result = await hooks.get("input")({ text: "task" }, {
        isIdle: () => true, model: { provider: "seed", id: "seed" },
        modelRegistry: { find: () => {
          if (failure === "catalogue-throws") throw new Error("catalogue unavailable");
          return failure === "catalogue" ? undefined : { provider: "test", id: "test" };
        } },
        ui: { notify: (text: string) => notices.push(text) },
      });
      assert.equal(result?.action, failure === "none" ? undefined : "handled");
      assert.ok(notices.length > 0);
    }
  } finally {
    if (old === undefined) delete process.env.DSH_AUTO; else process.env.DSH_AUTO = old;
  }
});
