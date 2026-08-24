import assert from "node:assert/strict";
import test from "node:test";

const { modelCredentialStatus } = require("../build/local-runtime.cjs");
const { LocalGraphClient } = require("../build/local-graph-client.cjs");

const ORIGIN = "http://127.0.0.1:41000";

test("reports whether the selected provider is configured", () => {
  assert.deepEqual(modelCredentialStatus("openai:gpt-test", {}), {
    available: false,
    variable: "OPENAI_API_KEY",
    canSignIn: true,
  });
  assert.deepEqual(
    modelCredentialStatus("openai:gpt-test", { OPENAI_API_KEY: "test-key" }),
    {
      available: true,
      variable: "OPENAI_API_KEY",
    },
  );
  assert.deepEqual(
    modelCredentialStatus("openai:gpt-test", {}, { openAiOAuth: true }),
    {
      available: true,
      variable: null,
      canSignIn: true,
    },
  );
  assert.deepEqual(
    modelCredentialStatus("google_genai:test", { GEMINI_API_KEY: "test-key" }),
    {
      available: true,
      variable: "GEMINI_API_KEY",
    },
  );
  assert.deepEqual(modelCredentialStatus("custom:test", {}), {
    available: true,
    variable: null,
  });
});

test("reaches the graph through the local server's route, not its private port", async () => {
  const requests: Request[] = [];
  const client = new LocalGraphClient({
    origin: () => ORIGIN,
    fetch: async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push(new Request(input, init));
      return new Response(null, { status: 200 });
    },
  });

  await client.createThread("thread-1");

  assert.equal(requests[0].url, `${ORIGIN}/local-graph/threads`);
  assert.equal(requests[0].method, "POST");
  assert.deepEqual(await requests[0].json(), {
    thread_id: "thread-1",
    if_exists: "do_nothing",
    metadata: { graph_id: "agent" },
  });
});

test("rejects a failed thread creation", async () => {
  const client = new LocalGraphClient({
    origin: () => ORIGIN,
    fetch: async () => new Response(null, { status: 503 }),
  });

  await assert.rejects(
    client.createThread("thread-1"),
    /Could not create local graph thread \(503\)/,
  );
});

test("derives thread activity, and reports none before the server is up", async () => {
  const idle = new LocalGraphClient({
    origin: () => null,
    fetch: () => assert.fail("must not reach a server that is not running"),
  });
  assert.deepEqual(await idle.threadActivity(), {});

  const client = new LocalGraphClient({
    origin: () => ORIGIN,
    fetch: async () =>
      Response.json([
        { thread_id: "thread-1", status: "busy" },
        { thread_id: "thread-2", status: "idle" },
        { thread_id: "thread-3", status: "error" },
      ]),
  });
  assert.deepEqual(await client.threadActivity(), {
    "thread-1": "running",
    "thread-3": "error",
  });

  const failing = new LocalGraphClient({
    origin: () => ORIGIN,
    fetch: async () => {
      throw new Error("connection refused");
    },
  });
  assert.equal(await failing.threadActivity(), null);
});
