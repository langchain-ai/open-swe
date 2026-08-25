import { test, expect, type Page } from "@playwright/test";

// Runs that Open SWE dispatches itself (Slack, Linear, GitHub, schedules) must
// stream the same Protocol v2 events as runs the dashboard starts: `tools`
// events for the root agent and namespaced events for its subagents. The
// server fixes a run's streaming protocol at creation, so a legacy-shaped
// `runs.create` leaves the dashboard with `values` only — subagent cards then
// never show nested activity, and tool status comes from message replay alone.
const USER = { login: "alice", email: "alice@example.com" };

interface ProtocolEvent {
  method: string;
  params?: { namespace?: string[]; data?: Record<string, unknown> };
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

// Replay the thread's event stream from the beginning (what the dashboard's
// `useStream` does when it joins a run it did not start) until the root run
// completes, and return every event seen.
async function replayEvents(page: Page, threadId: string) {
  return page.evaluate(async (id) => {
    const events: ProtocolEvent[] = [];
    const controller = new AbortController();
    const deadline = setTimeout(() => controller.abort(), 60_000);
    try {
      const response = await fetch(`/dashboard/api/threads/${id}/stream/events`, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          accept: "text/event-stream",
        },
        body: JSON.stringify({
          channels: ["values", "lifecycle", "tools"],
          namespaces: [[]],
          depth: 5,
          since: 0,
        }),
        signal: controller.signal,
      });
      if (!response.ok) {
        throw new Error(`events stream failed: ${response.status} ${await response.text()}`);
      }
      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let completed = false;
      while (!completed) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let separator: number;
        while ((separator = buffer.search(/\r?\n\r?\n/)) >= 0) {
          const frame = buffer.slice(0, separator);
          buffer = buffer.slice(separator).replace(/^\r?\n\r?\n/, "");
          const data = frame
            .split(/\r?\n/)
            .filter((line) => line.startsWith("data:"))
            .map((line) => line.slice(5).trim())
            .join("");
          if (!data) continue;
          const event = JSON.parse(data) as ProtocolEvent;
          events.push(event);
          if (
            event.method === "lifecycle" &&
            event.params?.data?.event === "completed" &&
            (event.params.namespace ?? []).length === 0
          ) {
            completed = true;
          }
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
    return events;
  }, threadId);
}

test.describe("dispatched run events", () => {
  test("a Slack-dispatched run streams tool events for the root agent and its subagents", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/mock/slack");
    await page.locator("#reset").click();
    const threadId = await dispatchFromSlack(
      page,
      "<@U0BOT> E2E_DELEGATE investigate the repository",
    );

    const events = await replayEvents(page, threadId);

    const toolEvents = events.filter((event) => event.method === "tools");
    const namespaceOf = (event: ProtocolEvent) => event.params?.namespace ?? [];
    // `tool-started` carries the name; `tool-finished` only the call id.
    const startedNames = (scope: ProtocolEvent[]) =>
      scope
        .filter((event) => event.params?.data?.event === "tool-started")
        .map((event) => event.params?.data?.tool_name);
    const finishedIds = (scope: ProtocolEvent[]) =>
      scope
        .filter((event) => event.params?.data?.event === "tool-finished")
        .map((event) => event.params?.data?.tool_call_id);

    // The root agent's two `task` calls, started and finished.
    const rootTools = toolEvents.filter((event) => namespaceOf(event).length === 0);
    expect(startedNames(rootTools)).toEqual(["task", "task"]);
    expect(finishedIds(rootTools).sort()).toEqual(["call-subagent-files", "call-subagent-layout"]);

    // Each subagent runs under its own namespace and reports its own shell
    // steps there — the events the dashboard's subagent cards subscribe to.
    const nested = toolEvents.filter((event) => namespaceOf(event).length > 0);
    const nestedNamespaces = new Set(nested.map((event) => namespaceOf(event).join("/")));
    expect(nestedNamespaces.size).toBe(2);
    for (const namespace of nestedNamespaces) {
      const scope = nested.filter((event) => namespaceOf(event).join("/") === namespace);
      expect(startedNames(scope)).toEqual(["execute", "execute"]);
      expect(finishedIds(scope).sort()).toEqual(["call-subagent-echo", "call-subagent-ls"]);
    }

    const subagentLifecycle = events
      .filter((event) => event.method === "lifecycle" && namespaceOf(event).length > 0)
      .map((event) => event.params?.data?.graph_name);
    expect(subagentLifecycle).toContain("general-purpose");
  });
});
