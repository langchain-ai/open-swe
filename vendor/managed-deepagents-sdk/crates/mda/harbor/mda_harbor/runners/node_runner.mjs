import { readFile, writeFile, mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { pathToFileURL } from "node:url";

const SCHEMA_VERSION = "mda.eval-agent-result/v1";

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
  // Pass model only at graph compile time. Including it in invoke `configurable`
  // can make LangChain treat `openai:…` as a raw OpenAI model id.
  const identity = await loadIdentityFixture();
  const graph = await loadGraph(projectDir, graphName, {
    model: modelOverride,
    appDir,
    sessionId,
    identity,
  });
  const result = await withLangSmithParent(projectDir, () =>
    invokeGraph(graph, instruction, {
      appDir,
      sessionId,
      identity,
    })
  );
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
  const parent = process.env.HARBOR_LANGSMITH_PARENT;
  if (!parent) {
    return await invoke();
  }

  const headers = { "langsmith-trace": parent };
  const baggage = process.env.HARBOR_LANGSMITH_BAGGAGE;
  if (baggage) {
    headers.baggage = baggage;
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
  if (!identity || typeof identity !== "object") {
    return;
  }
  const caller = identity.user;
  if (
    !caller ||
    typeof caller !== "object" ||
    typeof caller.id !== "string" ||
    caller.id.trim().length === 0
  ) {
    return;
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
  configurable.langgraph_auth_user = user;
  const provider = identity.source?.provider;
  if (typeof provider === "string") {
    configurable.mda_source_provider = provider;
  }
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
