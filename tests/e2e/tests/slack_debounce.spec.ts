import { test, expect, type APIRequestContext } from "@playwright/test";

// Feature: while Open SWE is busy, an untagged two-party follow-up interrupts
// the active run just like an explicit mention. Driven through the real webhook +
// real agent; the LLM is faked and holds a run open so the follow-up lands mid-run.

type SendResult = {
  thread_ts: string;
  thread_id: string;
  webhook: { status: string; reason?: string };
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

async function runStatuses(
  request: APIRequestContext,
  threadId: string,
): Promise<string[]> {
  const res = await request.get(`/threads/${threadId}/runs`);
  if (!res.ok()) return [];
  const runs = (await res.json()) as Array<{ status?: string }>;
  return runs.flatMap((run) => (run.status ? [run.status] : []));
}

test.describe("Slack busy-thread follow-ups", () => {
  test("an untagged two-party follow-up interrupts the active run", async ({
    request,
  }) => {
    await request.post("/control/reset");
    const channel = `C_INTERRUPT_${Date.now()}`;

    // Phase 1: open a two-party thread so Open SWE has participated (a
    // prerequisite for untagged follow-ups to be accepted).
    const opened = await send(request, {
      text: "<@U0BOT> please add a greet() helper and open a PR",
      mention_bot: true,
      channel,
    });
    const threadId = opened.thread_id;
    const threadTs = opened.thread_ts;
    await expect
      .poll(() => botTexts(request), { timeout: 60_000 })
      .toContain("/pull/");
    await expect
      .poll(() => threadStatus(request, threadId), { timeout: 60_000 })
      .not.toBe("busy");

    // Phase 2: start a run that holds the thread busy (fake LLM sleeps on the
    // marker). This one IS tagged, so it dispatches immediately.
    const busy = await send(request, {
      text: "<@U0BOT> now also tweak it E2E_BUSY_HOLD",
      mention_bot: true,
      channel,
      thread_ts: threadTs,
    });
    expect(busy.webhook.status, busy.webhook.reason).toBe("accepted");
    await expect
      .poll(() => threadStatus(request, threadId), { timeout: 30_000 })
      .toBe("busy");

    const followUp = await send(request, {
      text: "also rename it to hello()",
      mention_bot: false,
      channel,
      thread_ts: threadTs,
    });
    expect(followUp.webhook.status).toBe("accepted");
    await expect
      .poll(() => runStatuses(request, threadId), { timeout: 30_000 })
      .toContain("interrupted");
  });
});
