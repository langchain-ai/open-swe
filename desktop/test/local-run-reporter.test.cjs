const test = require("node:test");
const assert = require("node:assert/strict");

const { LocalRunReporter } = require("../src/local-run-reporter.cjs");

test("interrupts the tracked local run before reporting it", async (t) => {
  const cancelled = [];
  const reports = [];
  const rawMessage = {
    id: "message-1",
    type: "ai",
    content: [{ type: "reasoning", reasoning: "checking" }],
    tool_calls: [
      { id: "call-1", name: "read_file", args: { path: "README.md" } },
    ],
  };
  const reporter = new LocalRunReporter({
    supervisor: {
      cancelRun: async (threadId, runId) => cancelled.push([threadId, runId]),
      getThreadState: async () => ({ values: { messages: [rawMessage] } }),
    },
    device: { id: "device-1", name: "Laptop" },
    report: async (body) => reports.push(body),
  });
  t.after(() => reporter.close());
  reporter.track("thread-1", "run-1");

  await reporter.interrupt("thread-1");

  assert.deepEqual(cancelled, [["thread-1", "run-1"]]);
  assert.equal(reports.at(-1).status, "interrupted");
  assert.deepEqual(reports.at(-1).messages, [rawMessage]);
  await assert.rejects(
    reporter.interrupt("thread-1"),
    /active local run is unavailable/,
  );
});
