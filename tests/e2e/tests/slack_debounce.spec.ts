import { test, expect, type APIRequestContext } from "@playwright/test";

// Feature: while Open SWE is busy on a thread, rapid follow-ups are debounced —
// coalesced onto the thread's message queue (for the active run to drain at its
// next model call) instead of each halting and resuming the run. Driven through
// the real webhook + real agent; the LLM is faked and holds the first run open
// so follow-ups land mid-run.

type SendResult = {
  thread_ts: string;
  thread_id: string;
  webhook: { status: string };
};

async function send(
  request: APIRequestContext,
  data: Record<string, unknown>,
): Promise<SendResult> {
  const res = await request.post("/mock/slack/send", { data });
  return (await res.json()) as SendResult;
}

async function threadStatus(
  request: APIRequestContext,
  threadId: string,
): Promise<string> {
  const res = await request.get(`/threads/${threadId}`);
  if (!res.ok()) return "";
  const thread = (await res.json()) as { status?: string };
  return thread.status ?? "";
}

async function queuedCount(
  request: APIRequestContext,
  threadId: string,
): Promise<number> {
  const res = await request.get(
    `/control/queued?thread_id=${encodeURIComponent(threadId)}`,
  );
  const body = (await res.json()) as { queued_count: number };
  return body.queued_count;
}

async function runCount(
  request: APIRequestContext,
  threadId: string,
): Promise<number> {
  const res = await request.get(`/threads/${threadId}/runs`);
  if (!res.ok()) return -1;
  const runs = (await res.json()) as unknown[];
  return Array.isArray(runs) ? runs.length : -1;
}

test.describe("Slack busy-thread interrupt debounce", () => {
  test("rapid follow-ups coalesce onto the queue without new runs", async ({
    request,
  }) => {
    await request.post("/control/reset");

    // 1. Start a run that holds the thread busy (the fake LLM sleeps on the
    //    E2E_BUSY_HOLD marker), so follow-ups arrive mid-run.
    const opened = await send(request, {
      text: "<@U0BOT> please add a greet() helper and open a PR E2E_BUSY_HOLD",
      mention_bot: true,
    });
    const threadId = opened.thread_id;
    const threadTs = opened.thread_ts;
    expect(opened.webhook.status).toBe("accepted");

    // 2. Wait until the run is actually busy before firing follow-ups.
    await expect
      .poll(() => threadStatus(request, threadId), { timeout: 30_000 })
      .toBe("busy");
    const runsWhileBusy = await runCount(request, threadId);

    // 3. First follow-up while busy → parked on the queue, no new run.
    const b = await send(request, {
      text: "<@U0BOT> also rename it to hello()",
      mention_bot: true,
      thread_ts: threadTs,
    });
    expect(b.webhook.status).toBe("accepted");
    await expect
      .poll(() => queuedCount(request, threadId), { timeout: 30_000 })
      .toBe(1);

    // 4. Second follow-up while still busy → coalesced onto the SAME queue.
    const c = await send(request, {
      text: "<@U0BOT> and add a type hint",
      mention_bot: true,
      thread_ts: threadTs,
    });
    expect(c.webhook.status).toBe("accepted");
    await expect
      .poll(() => queuedCount(request, threadId), { timeout: 30_000 })
      .toBe(2);

    // 5. Neither follow-up spawned its own run — the active run absorbs both.
    expect(await threadStatus(request, threadId)).toBe("busy");
    expect(await runCount(request, threadId)).toBe(runsWhileBusy);
  });
});
