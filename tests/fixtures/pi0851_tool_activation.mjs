// Offline SDK contract. No Pi worker, prompt, credentials, or inference.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import net from 'node:net';
import http from 'node:http';
import https from 'node:https';
const [packageRoot, root, payload, installedTool] = process.argv.slice(2);
const denyNetwork = () => { throw new Error('offline activation forbids network'); };
globalThis.fetch = denyNetwork;
net.Socket.prototype.connect = denyNetwork;
http.request = https.request = denyNetwork;
const sdk = await import(pathToFileURL(path.join(packageRoot, 'dist/index.js')));
const { AuthStorage } = await import(pathToFileURL(path.join(packageRoot, 'dist/core/auth-storage.js')));
const runtime = await sdk.ModelRuntime.create({
  credentials: AuthStorage.inMemory(), modelsPath: null,
  modelsStorePath: path.join(root, 'models-store'),
  allowModelNetwork: false, refreshOnCreate: false,
});
let modelCalls = 0;
runtime.streamSimple = () => { modelCalls++; throw new Error('inference is forbidden'); };
const model = runtime.getModel('minimax-cn', 'MiniMax-M3');
assert.ok(model);
const report = { version: sdk.VERSION, model_calls: 0 };
const invocations = JSON.parse(fs.readFileSync(payload, 'utf8'));
for (const [index, key] of ['disabled', 'enabled', 'installed_only'].entries()) {
  const argv = invocations[index === 1 ? 1 : 0];
  const parsed = sdk.parseArgs(argv);
  const agentDir = path.join(root, key);
  fs.mkdirSync(agentDir);
  const settings = sdk.SettingsManager.inMemory();
  const extensionPaths = argv.flatMap((a, i) => a === '-e' ? [argv[i+1]] : []);
  if (key === 'installed_only') extensionPaths.push(installedTool);
  const loader = new sdk.DefaultResourceLoader({
    cwd: root, agentDir, settingsManager: settings,
    additionalExtensionPaths: extensionPaths, noExtensions: true,
    noSkills: true, noPromptTemplates: true, noThemes: true, noContextFiles: true,
  });
  await loader.reload();
  const { session, extensionsResult } = await sdk.createAgentSession({
    cwd: root, agentDir, modelRuntime: runtime, model,
    tools: parsed.tools, resourceLoader: loader, settingsManager: settings,
    sessionManager: sdk.SessionManager.inMemory(root),
  });
  assert.equal(extensionsResult.errors.length, 0);
  report[key] = session.getActiveToolNames();
  session.dispose();
}
report.model_calls = modelCalls;
console.log(JSON.stringify(report));
