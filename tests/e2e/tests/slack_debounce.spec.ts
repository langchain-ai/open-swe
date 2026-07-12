import { test, expect, type APIRequestContext } from "@playwright/test";

// Feature: while Open SWE is busy, *untagged* follow-ups are debounced —
// coalesced onto the thread's message queue (for the active run to drain at its
// next model call) instead of each halting and resuming the run. An explicit
// @-mention is NOT debounced (it keeps interrupting immediately). Driven through
// the real webhook + real agent; the LLM is faked and holds a run open so
// follow-ups land mid-run.

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

async function botTexts(request: APIRequestContext): Promise<string> {
  const res = await request.get("/mock/slack/messages");
  const msgs = (await res.json()) as Array<{ text: string; is_bot: boolean }>;
  return msgs
    .filter((m) => m.is_bot)
    .map((m) => m.text)
    .join("\n");
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

test.describe("Slack busy-thread interrupt debounce", () => {
  test("untagged follow-ups on a busy thread coalesce onto the queue", async ({
    request,
  }) => {
    await request.post("/control/reset");

    // Phase 1: open a two-party thread so Open SWE has participated (a
    // prerequisite for untagged follow-ups to be accepted).
    const opened = await send(request, {
      text: "<@U0BOT> please add a greet() helper and open a PR",
      mention_bot: true,
    });
    const threadId = opened.thread_id;
    const threadTs = opened.thread_ts;
    await expect
      .poll(() => botTexts(request), { timeout: 60_000 })
      .toContain("/pull/");
    await expect
      .poll(() => threadStatus(request, threadId), { timeout: 30_000 })
      .not.toBe("busy");

    // Phase 2: start a run that holds the thread busy (fake LLM sleeps on the
    // marker). This one IS tagged, so it dispatches immediately.
    const busy = await send(request, {
      text: "<@U0BOT> now also tweak it E2E_BUSY_HOLD",
      mention_bot: true,
      thread_ts: threadTs,
    });
    expect(busy.webhook.status).toBe("accepted");
    await expect
      .poll(() => threadStatus(request, threadId), { timeout: 30_000 })
      .toBe("busy");

    // 3. First UNTAGGED follow-up while busy → parked on the queue, no interrupt.
    const b = await send(request, {
      text: "also rename it to hello()",
      mention_bot: false,
      thread_ts: threadTs,
    });
    expect(b.webhook.status).toBe("accepted");
    await expect
      .poll(() => queuedCount(request, threadId), { timeout: 30_000 })
      .toBe(1);

    // 4. Second UNTAGGED follow-up while still busy → coalesced onto the queue.
    const c = await send(request, {
      text: "and add a type hint",
      mention_bot: false,
      thread_ts: threadTs,
    });
    expect(c.webhook.status).toBe("accepted");
    await expect
      .poll(() => queuedCount(request, threadId), { timeout: 30_000 })
      .toBe(2);

    // The run is still busy — the untagged follow-ups did not interrupt it.
    expect(await threadStatus(request, threadId)).toBe("busy");
  });
});
