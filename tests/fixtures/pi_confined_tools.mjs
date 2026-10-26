// Real Pi 0.85.1 extension activation and tool execution, with inference/network disabled.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import net from "node:net";
import http from "node:http";
import https from "node:https";
import { pathToFileURL } from "node:url";
const [packageRoot, root, guard] = process.argv.slice(2);
process.chdir(root);
process.env.HOME = path.dirname(root);
const deny = () => { throw new Error("network/inference forbidden"); };
globalThis.fetch = deny;
net.Socket.prototype.connect = deny;
http.request = https.request = deny;
const sdk = await import(pathToFileURL(path.join(packageRoot, "dist/index.js")));
const { AuthStorage } = await import(pathToFileURL(path.join(packageRoot, "dist/core/auth-storage.js")));
const runtime = await sdk.ModelRuntime.create({
  credentials: AuthStorage.inMemory(), modelsPath: null,
  modelsStorePath: path.join(path.dirname(root), "models"),
  allowModelNetwork: false, refreshOnCreate: false,
});
let modelCalls = 0;
runtime.streamSimple = () => { modelCalls++; deny(); };
const settings = sdk.SettingsManager.inMemory();
const agentDir = path.join(path.dirname(root), "agent");
fs.mkdirSync(agentDir);
const loader = new sdk.DefaultResourceLoader({
  cwd: root, agentDir, settingsManager: settings, additionalExtensionPaths: [guard],
  noExtensions: true, noSkills: true, noPromptTemplates: true, noThemes: true, noContextFiles: true,
});
await loader.reload();
const names = ["pao_read", "pao_edit", "pao_write", "pao_find", "pao_ls", "pao_grep"];
const { session, extensionsResult } = await sdk.createAgentSession({
  cwd: root, agentDir, modelRuntime: runtime, model: runtime.getModel("minimax-cn", "MiniMax-M3"),
  tools: names, noTools: "builtin", resourceLoader: loader, settingsManager: settings,
  sessionManager: sdk.SessionManager.inMemory(root),
});
assert.deepEqual(extensionsResult.errors, []);
assert.deepEqual(session.getActiveToolNames().sort(), [...names].sort());
const call = async (name, input) => {
  const tool = session._toolRegistry.get(name);
  const prepared = tool.prepareArguments ? tool.prepareArguments(input) : input;
  const result = await tool.execute("offline", prepared, new AbortController().signal);
  assert.equal(Boolean(result.isError), false, JSON.stringify(result));
  return result.content.map((part) => part.text ?? "").join("\n");
};
await call("pao_write", { path: "src/code.txt", content: "one\r\nneedle\r\nthree\r\n" });
assert.match(await call("pao_read", { path: "src/code.txt", offset: 2, limit: 1 }), /needle/);
await call("pao_edit", { path: "src/code.txt", oldText: "needle", newText: "edited" });
assert.match(fs.readFileSync("src/code.txt", "utf8"), /edited\r\n/);
assert.match(await call("pao_find", { pattern: "*.txt" }), /src\/code.txt/);
assert.match(await call("pao_ls", { path: "src" }), /code.txt/);
assert.match(await call("pao_grep", { pattern: "EDITED", ignoreCase: true, context: 1 }), /code.txt:2:edited/);
for (const location of ["@src", `file://${root}/src`, `@file://${root}/src`]) {
  assert.match(await call("pao_grep", { pattern: "edited", path: location }), /code.txt:2:edited/);
}
const canary = path.join(path.dirname(root), "outside.txt");
fs.writeFileSync(canary, "outside-canary");
fs.symlinkSync(canary, "existing");
fs.symlinkSync(path.join(path.dirname(root), "missing"), "dangling");
fs.symlinkSync(path.dirname(root), "parent");
fs.mkdirSync(".git");
fs.writeFileSync(".git/config", "protected");
fs.symlinkSync(path.join(root, ".git"), "alias");
for (const value of ["existing", "dangling", "parent/outside.txt", "~/outside.txt", `file://${canary}`, `@file://${canary}`, "alias/config", ".GIT/config"]) {
  await assert.rejects(call("pao_write", { path: value, content: "attack" }));
  await assert.rejects(call("pao_read", { path: value }));
  await assert.rejects(call("pao_grep", { path: value, pattern: "." }));
}
assert.equal(fs.readFileSync(canary, "utf8"), "outside-canary");
assert.equal(fs.existsSync(path.join(path.dirname(root), "missing")), false);
assert.equal(fs.readFileSync(".git/config", "utf8"), "protected");
assert.doesNotMatch(await call("pao_grep", { pattern: "outside-canary" }), /outside-canary/);
session.dispose();
// Exercise Pi's actual exception-containing input pipeline. A thrown extension
// error ordinarily continues input, so every admission failure must be handled.
const { ExtensionRunner } = await import(pathToFileURL(path.join(packageRoot, "dist/core/extensions/runner.js")));
const { createDshExtension } = await import(new URL("../../integrations/pi-dsh/index.ts", import.meta.url));
const oldAuto = process.env.DSH_AUTO;
process.env.DSH_AUTO = "1";
for (const failure of ["lookup", "switch", "switch-false", "none"]) {
  const handlers = new Map();
  const extension = { path: "trusted-pi-dsh", handlers };
  const api = {
    registerCommand() {}, on: (name, callback) => handlers.set(name, [callback]),
    setModel: async () => {
      if (failure === "switch") throw new Error("auth recheck unavailable");
      return failure !== "switch-false";
    },
  };
  createDshExtension(async () => ({ action: "ALLOW", decision: "admitted",
    target: { tid: "fixture", model: "fixture", provider: "fixture", pool: "fixture", tier: 3 },
  }))(api);
  const registry = { find: () => {
    if (failure === "lookup") throw new Error("catalogue unavailable");
    return { provider: "fixture", id: "fixture" };
  }};
  const runner = new ExtensionRunner([extension], {}, root, {}, registry);
  runner.getModel = () => ({ provider: "seed", id: "seed" });
  const errors = [];
  runner.errorListeners.add((error) => errors.push(error));
  const inputResult = await runner.emitInput("offline task", undefined, "interactive");
  assert.equal(inputResult.action, failure === "none" ? "continue" : "handled");
  assert.deepEqual(errors, []);
}
if (oldAuto === undefined) delete process.env.DSH_AUTO; else process.env.DSH_AUTO = oldAuto;
assert.equal(modelCalls, 0);
console.log(JSON.stringify({ sdk: sdk.VERSION, active_tools: names, boundary_cases: 8,
  real_input_pipeline_cases: 4, model_calls: modelCalls }));
