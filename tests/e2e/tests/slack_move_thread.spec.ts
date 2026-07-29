import { test, expect, type APIRequestContext } from "@playwright/test";

type SendResult = {
  thread_ts: string;
  thread_id: string;
  webhook: { status: string; reason?: string };
};

type SlackMessage = {
  channel: string;
  text: string;
  is_bot: boolean;
  ts: string;
  thread_ts: string;
};

async function send(
  request: APIRequestContext,
  data: Record<string, unknown>,
): Promise<SendResult> {
  const response = await request.post("/mock/slack/send", { data });
  return (await response.json()) as SendResult;
}

async function messages(
  request: APIRequestContext,
  channel: string,
): Promise<SlackMessage[]> {
  const response = await request.get(
    `/mock/slack/messages?channel=${encodeURIComponent(channel)}`,
  );
  return (await response.json()) as SlackMessage[];
}

async function stateText(
  request: APIRequestContext,
  threadId: string,
): Promise<string> {
  const response = await request.get(`/threads/${threadId}/state`);
  if (!response.ok()) return "";
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

async function movedRoot(
  request: APIRequestContext,
  channel: string,
): Promise<SlackMessage | undefined> {
  return (await messages(request, channel)).find(
    (message) =>
      message.is_bot &&
      message.text.includes("Continue the existing Open SWE task"),
  );
}

test.describe("Slack thread moves", () => {
  test("cross-channel move preserves destination state and detaches the source", async ({
    request,
  }) => {
    await request.post("/control/reset");
    const source = await send(request, {
      channel: "C_SOURCE",
      text: "<@U0BOT> E2E_MOVE_THREAD move this Open SWE thread",
      mention_bot: true,
    });
    expect(source.webhook.status).toBe("accepted");

    await expect
      .poll(() => movedRoot(request, "C_TARGET"), { timeout: 60_000 })
      .toBeTruthy();
    const destination = await movedRoot(request, "C_TARGET");
    expect(destination).toBeTruthy();

    const destinationFollowup = await send(request, {
      channel: "C_TARGET",
      thread_ts: destination!.thread_ts,
      text: "<@U0BOT> E2E_DESTINATION_FOLLOWUP confirm the state continued",
      mention_bot: true,
    });
    expect(destinationFollowup.thread_id).toBe(source.thread_id);
    await expect
      .poll(() => stateText(request, source.thread_id), { timeout: 60_000 })
      .toContain("E2E_DESTINATION_FOLLOWUP");

    const sourceRetag = await send(request, {
      channel: "C_SOURCE",
      thread_ts: source.thread_ts,
      text: "<@U0BOT> E2E_SOURCE_RETAG start a fresh Open SWE thread",
      mention_bot: true,
    });
    expect(sourceRetag.thread_id).not.toBe(source.thread_id);
    await expect
      .poll(() => stateText(request, sourceRetag.thread_id), {
        timeout: 60_000,
      })
      .toContain("E2E_MOVE_THREAD");
    expect(await stateText(request, sourceRetag.thread_id)).toContain(
      "E2E_SOURCE_RETAG",
    );
  });

  test("same-channel move creates a new Slack root for the same agent state", async ({
    request,
  }) => {
    await request.post("/control/reset");
    const source = await send(request, {
      channel: "C_SAME",
      text: "<@U0BOT> E2E_MOVE_SAME_CHANNEL move this Open SWE thread",
      mention_bot: true,
    });

    await expect
      .poll(() => movedRoot(request, "C_SAME"), { timeout: 60_000 })
      .toBeTruthy();
    const destination = await movedRoot(request, "C_SAME");
    expect(destination!.thread_ts).not.toBe(source.thread_ts);

    const followup = await send(request, {
      channel: "C_SAME",
      thread_ts: destination!.thread_ts,
      text: "<@U0BOT> E2E_DESTINATION_FOLLOWUP continue here",
      mention_bot: true,
    });
    expect(followup.thread_id).toBe(source.thread_id);
  });
});
