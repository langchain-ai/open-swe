import { spawn } from "node:child_process";
import { openSync, closeSync, readFileSync } from "node:fs";
import { readFile, writeFile, mkdir, mkdtemp, rm } from "node:fs/promises";
import { createServer } from "node:net";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { pathToFileURL } from "node:url";

const SCHEMA_VERSION = "mda.eval-agent-result/v1";
const SERVER_START_TIMEOUT_MS = 120_000;
const IDLE_POLL_MS = 500;

async function main() {
  const instructionPath = process.argv[2];
  const projectDir = requiredEnv("MDA_EVAL_PROJECT_DIR");
  const modelName = process.env.MDA_EVAL_MODEL_NAME || undefined;
  const modelOverride = process.env.MDA_EVAL_MODEL_OVERRIDE || undefined;
  const graphName = process.env.MDA_EVAL_GRAPH ?? "agent";
  const summaryPath = requiredEnv("MDA_EVAL_SUMMARY_PATH");
  const resultPath = requiredEnv("MDA_EVAL_RESULT_PATH");
  const appDir = process.env.MDA_EVAL_APP_DIR ?? "/app";
  const sessionId = process.env.HARBOR_SESSION_ID ?? "mda-eval";

  const instruction = await readFile(instructionPath, "utf8");
  process.chdir(projectDir);
  const identity = await loadIdentityFixture();
  const manifest = await readManifest(projectDir);
  // mda names each async subagent's graph `<lead graph>--<subagent>`.
  const hasAsyncSubagents = Object.keys(manifest.graphs ?? {}).some((name) =>
    name.startsWith(`${graphName}--`)
  );
  let result;
  if (hasAsyncSubagents) {
    result = await runOnAgentServer(projectDir, manifest, graphName, instruction, {
      identity,
      logPath: path.join(path.dirname(summaryPath), "agent-server.log"),
    });
  } else {
    // Pass model only at graph compile time. Including it in invoke `configurable`
    // can make LangChain treat `openai:…` as a raw OpenAI model id.
    const graph = await loadGraph(projectDir, graphName, {
      model: modelOverride,
      appDir,
      sessionId,
      identity,
    });
    result = await withLangSmithParent(projectDir, () =>
      invokeGraph(graph, instruction, {
        appDir,
        sessionId,
        identity,
      })
    );
  }
  const answer = extractAnswer(result);

  await mkdir(path.dirname(resultPath), { recursive: true });
  await writeFile(resultPath, `${JSON.stringify(jsonSafe(result), null, 2)}\n`, "utf8");

  const summary = {
    schemaVersion: SCHEMA_VERSION,
    runtime: "typescript",
    answerWritten: answer,
    model: modelName,
    usage: extractUsage(result),
    // Observability for identity injection (not under /app).
    ...identitySummary(identity),
  };
  await mkdir(path.dirname(summaryPath), { recursive: true });
  await writeFile(summaryPath, `${JSON.stringify(summary, null, 2)}\n`, "utf8");
}

function identitySummary(identity) {
  const callerId = identity?.user?.id;
  return typeof callerId === "string" ? { identityUserId: callerId } : {};
}

async function readManifest(projectDir) {
  return JSON.parse(await readFile(path.join(projectDir, "langgraph.json"), "utf8"));
}

async function runOnAgentServer(projectDir, manifest, graphName, instruction, { identity, logPath }) {
  const require = createRequire(path.join(projectDir, "package.json"));
  const { Client } = await import(
    pathToFileURL(require.resolve("@langchain/langgraph-sdk")).href
  );
  const serverDir = await mkdtemp(path.join(tmpdir(), "mda-eval-server-"));
  const configPath = await writeServerConfig(serverDir, projectDir, manifest, identity, require);
  const port = await freePort();
  await mkdir(path.dirname(logPath), { recursive: true });
  const log = openSync(logPath, "a");
  const server = spawn(
    process.execPath,
    [
      path.join(projectDir, "node_modules", ".bin", "langgraphjs"),
      "dev",
      "--config",
      configPath,
      "--host",
      "127.0.0.1",
      "--port",
      String(port),
      "--no-browser",
      "--no-reload",
      // The JS dev server already runs 10 jobs at once, enough for the lead
      // and its async subagents.
    ],
    { cwd: serverDir, env: serverEnv(), stdio: ["ignore", log, log], detached: true }
  );
  closeSync(log);
  try {
    const url = `http://127.0.0.1:${port}`;
    await waitForServer(url, server, logPath);
    const client = new Client({ apiUrl: url });
    const { thread_id: threadId } = await client.threads.create();
    // `runs.create` takes no per-request headers.
    const leadClient = new Client({
      apiUrl: url,
      defaultHeaders: harborTraceHeaders({ nestUnderParent: true }),
    });
    const run = await leadClient.runs.create(threadId, graphName, {
      input: { messages: [{ role: "user", content: instruction }] },
      config: serverRunConfig(identity),
    });
    await client.runs.join(threadId, run.run_id);
    const finished = await client.runs.get(threadId, run.run_id);
    if (finished.status === "error" || finished.status === "timeout") {
      throw new Error(
        `the agent's run ended with status ${JSON.stringify(finished.status)}:\n${tail(logPath)}`
      );
    }
    await waitUntilIdle(client);
    const thread = await client.threads.get(threadId);
    if (thread.status === "error") {
      throw new Error(`the agent's last run failed:\n${tail(logPath)}`);
    }
    const state = await client.threads.getState(threadId);
    return state.values;
  } finally {
    await stop(server);
    await rm(serverDir, { recursive: true, force: true });
  }
}

async function writeServerConfig(serverDir, projectDir, manifest, identity, require) {
  // The build's config, outside the graded workspace. The runner env already
  // holds `.env`, and a trial serves no HTTP routes or the project's own auth.
  // The JS server resolves config paths against the config's own directory.
  const { env: _env, http: _http, auth: _auth, ...config } = manifest;
  const fromServer = (file) => `./${path.relative(serverDir, file)}`;
  config.dependencies = [fromServer(projectDir)];
  config.graphs = Object.fromEntries(
    Object.entries(manifest.graphs).map(([name, ref]) => [
      name,
      fromServer(path.join(projectDir, ref.replace(/^\.\//, ""))),
    ])
  );
  const user = identityUser(identity);
  if (user) {
    const authModule = pathToFileURL(require.resolve("@langchain/langgraph-sdk/auth")).href;
    await writeFile(
      path.join(serverDir, "eval_auth.mjs"),
      `import { Auth } from ${JSON.stringify(authModule)};\n\n` +
        `const USER = ${JSON.stringify(user)};\n\n` +
        "export const auth = new Auth().authenticate(async () => ({ ...USER }));\n",
      "utf8"
    );
    // deepagents launches async subagents as Studio.
    config.auth = { path: "./eval_auth.mjs:auth", disable_studio_auth: true };
  }
  const configPath = path.join(serverDir, "langgraph.json");
  await writeFile(configPath, JSON.stringify(config, null, 2), "utf8");
  return configPath;
}

// Harbor sends unset vars as "", and the server rejects LANGSMITH_TRACING="".
function serverEnv() {
  return Object.fromEntries(Object.entries(process.env).filter(([, value]) => value));
}

function freePort() {
  return new Promise((resolve, reject) => {
    const probe = createServer();
    probe.once("error", reject);
    probe.listen(0, "127.0.0.1", () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
  });
}

async function waitForServer(url, server, logPath) {
  const deadline = Date.now() + SERVER_START_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (server.exitCode !== null || server.signalCode !== null) {
      throw new Error(
        `the local Agent Server exited with status ${server.exitCode ?? server.signalCode}:\n${tail(logPath)}`
      );
    }
    try {
      if ((await fetch(`${url}/ok`)).ok) return;
    } catch {}
    await sleep(250);
  }
  throw new Error(
    `the local Agent Server did not start in ${SERVER_START_TIMEOUT_MS / 1000} s:\n${tail(logPath)}`
  );
}

function serverRunConfig(identity) {
  const configurable = {};
  const provider = identitySourceProvider(identity);
  if (provider !== undefined) {
    configurable.mda_source_provider = provider;
  }
  return { configurable };
}

/**
 * The Harbor trial's trace headers, flagged for MDA to nest a run under when
 * asked. The JS dev server doesn't forward `langsmith-trace` yet, so a run on
 * it doesn't nest.
 */
function harborTraceHeaders({ nestUnderParent = false } = {}) {
  const parent = process.env.HARBOR_LANGSMITH_PARENT;
  if (!parent) return undefined;
  const harborBaggage = process.env.HARBOR_LANGSMITH_BAGGAGE;
  if (!nestUnderParent) {
    return { "langsmith-trace": parent, ...(harborBaggage ? { baggage: harborBaggage } : {}) };
  }
  let metadata = {};
  const baggage = [];
  for (const item of (harborBaggage ?? "").split(",")) {
    const separator = item.indexOf("=");
    const key = separator === -1 ? item : item.slice(0, separator);
    if (key === "langsmith-metadata") {
      try {
        const decoded = JSON.parse(decodeURIComponent(item.slice(separator + 1)));
        metadata = decoded && typeof decoded === "object" && !Array.isArray(decoded) ? decoded : {};
      } catch {
        metadata = {};
      }
    } else if (item) {
      baggage.push(item);
    }
  }
  metadata.ls_nest_under_parent = true;
  baggage.push(`langsmith-metadata=${encodeURIComponent(JSON.stringify(metadata))}`);
  return { "langsmith-trace": parent, baggage: baggage.join(",") };
}

async function waitUntilIdle(client) {
  let idleChecks = 0;
  while (idleChecks < 2) {
    await sleep(IDLE_POLL_MS);
    idleChecks = (await hasActiveRuns(client)) ? 0 : idleChecks + 1;
  }
}

async function hasActiveRuns(client) {
  // A thread stays busy from run creation until its last active run ends.
  return (await client.threads.search({ status: "busy", limit: 1 })).length > 0;
}

async function stop(server) {
  if (server.exitCode !== null || server.signalCode !== null) return;
  const exited = new Promise((resolve) => server.once("exit", resolve));
  killGroup(server, "SIGTERM");
  const timedOut = await Promise.race([exited.then(() => false), sleep(10_000).then(() => true)]);
  if (timedOut) {
    killGroup(server, "SIGKILL");
    await exited;
  }
}

function killGroup(server, signal) {
  try {
    process.kill(-server.pid, signal);
  } catch {}
}

function tail(logPath, lines = 40) {
  try {
    return readFileSync(logPath, "utf8").split("\n").slice(-lines).join("\n");
  } catch {
    return "";
  }
}

async function loadGraph(projectDir, graphName, options) {
  const manifest = JSON.parse(await readFile(path.join(projectDir, "langgraph.json"), "utf8"));
  const graphRef = manifest.graphs?.[graphName];
  if (typeof graphRef !== "string") {
    throw new Error(`graph ${graphName} not found in langgraph.json`);
  }
  const [moduleRef, exportName] = graphRef.split(":");
  const modulePath = path.join(projectDir, moduleRef.replace(/^\.\//, ""));
  const imported = await import(pathToFileURL(modulePath).href);
  const factory = imported[exportName];
  if (typeof factory !== "function") {
    throw new Error(`graph export ${exportName} is not a function`);
  }
  return factory(config(options));
}

async function invokeGraph(graph, instruction, options) {
  const payload = { messages: [{ role: "user", content: instruction }] };
  const runConfig = config(options);
  if (typeof graph?.invoke === "function") {
    return await graph.invoke(payload, runConfig);
  }
  if (typeof graph === "function") {
    return await graph(payload, runConfig);
  }
  throw new TypeError("compiled graph is not invokable");
}

async function withLangSmithParent(projectDir, invoke) {
  const headers = harborTraceHeaders();
  if (!headers) {
    return await invoke();
  }
  const require = createRequire(path.join(projectDir, "package.json"));
  const { RunTree } = require("langsmith/run_trees");
  const { withRunTree } = require("langsmith/traceable");
  const runTree = RunTree.fromHeaders(headers, { tracingEnabled: true });
  return runTree ? await withRunTree(runTree, invoke) : await invoke();
}

function config({ model, appDir, sessionId, identity }) {
  const configurable = {
    cwd: appDir,
    thread_id: sessionId,
  };
  if (model) {
    configurable.model = model;
  }
  applyIdentityFixture(configurable, identity);
  return { configurable };
}

/**
 * Map checked-in eval `identity.json` onto the trusted auth-user seam so
 * `buildRuntimeIdentity` / tools see the same envelope as post-ingress runs.
 */
function applyIdentityFixture(configurable, identity) {
  const user = identityUser(identity);
  if (!user) {
    return;
  }
  configurable.langgraph_auth_user = user;
  const provider = identitySourceProvider(identity);
  if (provider !== undefined) {
    configurable.mda_source_provider = provider;
  }
}

function identityUser(identity) {
  if (!identity || typeof identity !== "object") {
    return undefined;
  }
  const caller = identity.user;
  if (
    !caller ||
    typeof caller !== "object" ||
    typeof caller.id !== "string" ||
    caller.id.trim().length === 0
  ) {
    return undefined;
  }
  const user = {
    identity: caller.id,
    permissions: [],
    mda_user_id: caller.id,
    mda_user_kind: caller.kind === "service" ? "service" : "person",
  };
  if (typeof caller.email === "string") {
    user.mda_user_email = caller.email;
  }
  if (Array.isArray(identity.groups)) {
    const groups = identity.groups.filter((group) => typeof group === "string");
    if (groups.length > 0) {
      user.mda_groups = groups;
    }
  }
  if (
    identity.claims &&
    typeof identity.claims === "object" &&
    !Array.isArray(identity.claims)
  ) {
    user.mda_claims = identity.claims;
  }
  return user;
}

function identitySourceProvider(identity) {
  const provider = identity?.source?.provider;
  return typeof provider === "string" ? provider : undefined;
}

async function loadIdentityFixture() {
  const identityPath = process.env.MDA_EVAL_IDENTITY_PATH;
  if (!identityPath) {
    return undefined;
  }
  try {
    return JSON.parse(await readFile(identityPath, "utf8"));
  } catch (error) {
    if (error && typeof error === "object" && error.code === "ENOENT") {
      return undefined;
    }
    throw error;
  }
}

function extractAnswer(value) {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const messages = value.messages;
    if (Array.isArray(messages) && messages.length > 0) {
      return messageText(messages[messages.length - 1]);
    }
    for (const key of ["answer", "output", "content"]) {
      if (typeof value[key] === "string") {
        return value[key];
      }
    }
  }
  return messageText(value);
}

function messageText(value) {
  if (typeof value === "string") {
    return value;
  }
  if (value?.content !== undefined) {
    if (typeof value.content === "string") {
      return value.content;
    }
    if (Array.isArray(value.content)) {
      return value.content
        .map((part) =>
          part && typeof part === "object" && typeof part.text === "string"
            ? part.text
            : String(part)
        )
        .join("");
    }
  }
  return JSON.stringify(jsonSafe(value));
}

function extractUsage(value) {
  return value && typeof value === "object" && typeof value.usage === "object"
    ? value.usage
    : undefined;
}

function jsonSafe(value) {
  // JSON.stringify already invokes `toJSON` on LangChain message/result objects.
  return JSON.parse(
    JSON.stringify(value, (_key, item) =>
      typeof item === "bigint" ? item.toString() : item
    )
  );
}

function requiredEnv(name) {
  const value = process.env[name];
  if (!value) {
    throw new Error(`${name} is required`);
  }
  return value;
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
