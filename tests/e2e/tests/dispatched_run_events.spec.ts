import { test, expect, type Page } from "@playwright/test";

// Runs that Robin dispatches itself (Slack, Linear, GitHub, schedules) must
// stream what `@langchain/react` 2.0 needs to join them: the runs API's events,
// kept from the first so a page that opens late still gets the whole run, with
// each subagent's under its own namespace. A join carries only the modes the run
// started with, so a run started without the server's stream settings leaves the
// dashboard with `values` only: no subagent panels and no live tool calls.
const USER = { login: "alice", email: "alice@example.com" };

interface RunEvent {
  /** The stream mode, then the namespace it came from, if any: `values|tools:abc`. */
  name: string;
  data: unknown;
}

interface ToolCall {
  name?: string;
  id?: string;
}

async function login(page: Page) {
  const response = await page.request.post("/control/login", { data: USER });
  expect(response.ok()).toBeTruthy();
}

// Dispatch through the real Slack webhook path and return the thread id.
async function dispatchFromSlack(page: Page, text: string): Promise<string> {
  const send = await page.request.post("/mock/slack/send", { data: { text } });
  expect(send.ok()).toBeTruthy();
  const { thread_id: threadId } = (await send.json()) as { thread_id: string };
  expect(threadId).toBeTruthy();
  return threadId;
}

// Join the thread's first run from its first event, as `useStream` joins a run
// it did not start, until the run ends; return the run and every event seen.
async function joinFirstRun(page: Page, threadId: string) {
  return page.evaluate(async (id) => {
    let runs: Array<{ run_id: string }> = [];
    for (let attempt = 0; attempt < 100 && runs.length === 0; attempt++) {
      const listed = await fetch(`/dashboard/api/threads/${id}/runs?limit=10`);
      runs = listed.ok ? ((await listed.json()) as typeof runs) : [];
      if (runs.length === 0) await new Promise((r) => setTimeout(r, 300));
    }
    const runId = runs.at(-1)?.run_id;
    if (!runId) throw new Error("the dispatch started no run");
    const run = (await (
      await fetch(`/dashboard/api/threads/${id}/runs/${runId}`)
    ).json()) as { kwargs?: Record<string, unknown> };
    const events: Array<{ name: string; data: unknown }> = [];
    const controller = new AbortController();
    const deadline = setTimeout(() => controller.abort(), 60_000);
    try {
      const response = await fetch(
        `/dashboard/api/threads/${id}/runs/${runId}/stream`,
        {
          headers: { accept: "text/event-stream", "last-event-id": "-" },
          signal: controller.signal,
        },
      );
      if (!response.ok) {
        throw new Error(
          `run join failed: ${response.status} ${await response.text()}`,
        );
      }
      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let separator: number;
        while ((separator = buffer.search(/\r?\n\r?\n/)) >= 0) {
          const frame = buffer.slice(0, separator).split(/\r?\n/);
          buffer = buffer.slice(separator).replace(/^\r?\n\r?\n/, "");
          const name = frame
            .find((line) => line.startsWith("event:"))
            ?.slice(6)
            .trim();
          const data = frame
            .filter((line) => line.startsWith("data:"))
            .map((line) => line.slice(5).trim())
            .join("");
          if (name && data) events.push({ name, data: JSON.parse(data) });
        }
      }
    } catch (error) {
      // The deadline aborts the fetch; whatever was collected is still the
      // evidence the assertions need (and a clearer failure than an abort).
      if ((error as { name?: string }).name !== "AbortError") throw error;
    } finally {
      clearTimeout(deadline);
      controller.abort();
    }
    return { kwargs: run.kwargs ?? {}, events };
  }, threadId);
}

/** The tool calls the agent's messages in a `values` event make. */
function toolCalls(event: RunEvent): Array<ToolCall> {
  const messages = (event.data as { messages?: unknown } | null)?.messages;
  if (!Array.isArray(messages)) return [];
  return messages.flatMap((message) =>
    Array.isArray((message as { tool_calls?: unknown }).tool_calls)
      ? ((message as { tool_calls: Array<ToolCall> }).tool_calls ?? [])
      : [],
  );
}

test.describe("dispatched run events", () => {
  test("a Slack-dispatched run streams its root agent and its subagents for a late join", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/mock/slack");
    await page.locator("#reset").click();
    const threadId = await dispatchFromSlack(
      page,
      "<@U0BOT> E2E_DELEGATE investigate the repository",
    );

    const { kwargs, events } = await joinFirstRun(page, threadId);

    // What 2.0 reads off the run to join it: every subagent's events, and the
    // lifecycle events that say which subagent is which.
    expect(kwargs.subgraphs).toBe(true);
    expect(kwargs.stream_mode).toEqual(
      expect.arrayContaining(["values", "messages-tuple", "lifecycle"]),
    );
    const names = [...new Set(events.map((event) => event.name))];

    // The root agent's two `task` calls.
    const rootCalls = events
      .filter((event) => event.name === "values")
      .flatMap(toolCalls);
    expect(
      [
        ...new Set(
          rootCalls
            .filter((call) => call.name === "task")
            .map((call) => call.id),
        ),
      ].sort(),
      `events seen: ${names.join(", ")}`,
    ).toEqual(["call-subagent-files", "call-subagent-layout"]);

    // Each subagent runs under its own namespace and reports its own shell
    // steps there: the events a subagent panel shows.
    const nested = events.filter((event) => event.name.startsWith("values|"));
    const subagents = new Set(nested.map((event) => event.name.split("|")[1]));
    expect(subagents.size, `events seen: ${names.join(", ")}`).toBe(2);
    for (const subagent of subagents) {
      const calls = nested
        .filter((event) => event.name.split("|")[1] === subagent)
        .flatMap(toolCalls)
        .filter((call) => call.name === "execute");
      expect([...new Set(calls.map((call) => call.id))].sort()).toEqual([
        "call-subagent-echo",
        "call-subagent-ls",
      ]);
    }
  });
});
