import { test, expect, type APIRequestContext } from "@playwright/test";

type SendResult = {
  thread_ts: string;
  thread_id: string;
  webhook: { status: string; reason?: string };
};

async function send(
  request: APIRequestContext,
  data: Record<string, unknown>,
): Promise<SendResult> {
  const response = await request.post("/mock/slack/send", { data });
  return (await response.json()) as SendResult;
}

async function botTexts(request: APIRequestContext): Promise<string[]> {
  const response = await request.get("/mock/slack/messages");
  const messages = (await response.json()) as Array<{
    text: string;
    is_bot: boolean;
  }>;
  return messages
    .filter((message) => message.is_bot)
    .map((message) => message.text);
}

async function stateText(
  request: APIRequestContext,
  threadId: string,
): Promise<string> {
  const response = await request.get(`/threads/${threadId}/state`);
  const state = (await response.json()) as {
    values?: { messages?: Array<{ content?: unknown }> };
  };
  return (state.values?.messages ?? [])
    .map((message) =>
      typeof message.content === "string"
        ? message.content
        : JSON.stringify(message.content),
    )
    .join("\n");
}

test("a stale Slack reply fails, re-reads the thread, and then succeeds", async ({
  request,
}) => {
  await request.post("/control/reset");
  const opened = await send(request, {
    text: "<@U0BOT> E2E_OPTIMISTIC_LOCK exercise the versioned reply flow",
    mention_bot: true,
  });
  expect(opened.webhook.status).toBe("accepted");

  await expect
    .poll(async () => (await botTexts(request)).join("\n"), {
      timeout: 30_000,
    })
    .toContain("Optimistic lock flow started.");

  const reaction = await request.post("/mock/slack/reaction", {
    data: { thread_ts: opened.thread_ts, reaction: "eyes" },
  });
  expect(await reaction.json()).toEqual({
    status: "ignored",
    reason: "Reaction not tracked for feedback",
  });
  await expect
    .poll(async () => (await botTexts(request)).join("\n"), {
      timeout: 30_000,
    })
    .toContain("A reaction did not invalidate the thread version.");

  const intervening = await send(request, {
    text: "<@U_BOB> adding new context while Open SWE is working",
    mention_bot: false,
    thread_ts: opened.thread_ts,
  });
  expect(intervening.webhook).toEqual({
    status: "ignored",
    reason: "Not an app mention, DM, or plan reply",
  });

  await expect
    .poll(() => stateText(request, opened.thread_id), { timeout: 60_000 })
    .toContain("Slack thread version mismatch");
  await expect
    .poll(() => stateText(request, opened.thread_id), { timeout: 60_000 })
    .toContain('"thread_version": 2');
  await expect
    .poll(async () => (await botTexts(request)).join("\n"), {
      timeout: 60_000,
    })
    .toContain("Re-read the thread and posted with the updated version.");
  await expect
    .poll(() => stateText(request, opened.thread_id), { timeout: 60_000 })
    .toContain('{"success": true, "thread_version": 2}');

  expect((await botTexts(request)).join("\n")).not.toContain(
    "This stale reply must not be posted.",
  );
});
