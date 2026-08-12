import { test, expect, type APIRequestContext } from "@playwright/test";

// Feature: Slack redelivers an event when it doesn't get a 2xx within three
// seconds. Each redelivery carries the same event_id, and must not start a
// second agent run (which the user sees as two replies to one mention).

type SendResult = {
  thread_ts: string;
  thread_id: string;
  event_id: string;
  webhook: { status: string; reason?: string };
};

async function send(
  request: APIRequestContext,
  data: Record<string, unknown>,
): Promise<SendResult> {
  const res = await request.post("/mock/slack/send", { data });
  return (await res.json()) as SendResult;
}

async function botTexts(request: APIRequestContext): Promise<string[]> {
  const res = await request.get("/mock/slack/messages");
  const msgs = (await res.json()) as Array<{ text: string; is_bot: boolean }>;
  return msgs.filter((m) => m.is_bot).map((m) => m.text);
}

async function runCount(
  request: APIRequestContext,
  threadId: string,
): Promise<number> {
  const res = await request.get(`/threads/${threadId}/runs`);
  if (!res.ok()) return -1;
  const runs = (await res.json()) as unknown[];
  return runs.length;
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

test.describe("Slack event redelivery", () => {
  test("a redelivered mention starts no second run", async ({ request }) => {
    await request.post("/control/reset");

    const opened = await send(request, {
      text: "<@U0BOT> please add a greet() helper and open a PR",
      mention_bot: true,
    });
    expect(opened.webhook.status).toBe("accepted");

    // Slack's first retry — same payload, same event_id, retry header set.
    const firstRetry = await send(request, { redeliver: true, retry_num: 1 });
    expect(firstRetry.event_id).toBe(opened.event_id);
    expect(firstRetry.webhook.status).toBe("ignored");

    // A retry usually lands on an instance that never saw the original, and so
    // has only the store to dedupe on.
    await request.post("/control/forget-slack-events");
    const secondRetry = await send(request, { redeliver: true, retry_num: 2 });
    expect(secondRetry.webhook.status).toBe("ignored");

    // The one run that was accepted still completes normally.
    await expect
      .poll(async () => (await botTexts(request)).join("\n"), {
        timeout: 60_000,
      })
      .toContain("/pull/");
    await expect
      .poll(() => threadStatus(request, opened.thread_id), { timeout: 30_000 })
      .not.toBe("busy");

    expect(await runCount(request, opened.thread_id)).toBe(1);
  });
});
