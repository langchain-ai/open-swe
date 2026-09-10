import { test, expect, type APIRequestContext } from "@playwright/test";

type CodeChannel = {
  id: string;
  name: string;
  status: string;
  archived: boolean;
  context_bar_items: { key: string; label: string }[];
  commands: { name: string }[];
};

type Stream = {
  ts: string;
  channel: string;
  thread_ts: string;
  task_display_mode: string;
  text: string;
  state: string;
  tasks: Record<string, { title?: string; status?: string }>;
};

const HANDOFF_LINE = "Picking up the flaky CI investigation in this channel";

async function codeChannels(
  request: APIRequestContext,
): Promise<CodeChannel[]> {
  const response = await request.get("/mock/slack/code-channels");
  return (
    ((await response.json()) as { channels?: CodeChannel[] }).channels ?? []
  );
}

async function streams(
  request: APIRequestContext,
  channel: string,
): Promise<Stream[]> {
  const response = await request.get(
    `/mock/slack/streams?channel=${encodeURIComponent(channel)}`,
  );
  return ((await response.json()) as { streams?: Stream[] }).streams ?? [];
}

async function boundThread(
  request: APIRequestContext,
  channel: string,
): Promise<string | null> {
  const response = await request.get(
    `/mock/slack/thread-map?channel=${encodeURIComponent(channel)}&ts=0`,
  );
  return (
    ((await response.json()) as { thread_id?: string | null }).thread_id ?? null
  );
}

async function openCodeChannel(page: {
  locator: (selector: string) => { click: () => Promise<void> };
  waitForResponse: (predicate: (response: any) => boolean) => Promise<any>;
  request: APIRequestContext;
}): Promise<{ originThreadId: string; channel: CodeChannel }> {
  const send = page.waitForResponse(
    (response) =>
      response.url().endsWith("/mock/slack/send") &&
      response.request().method() === "POST",
  );
  await page.locator("#send").click();
  const originThreadId = ((await (await send).json()) as { thread_id?: string })
    .thread_id;
  expect(originThreadId).toBeTruthy();

  await expect
    .poll(async () => (await codeChannels(page.request)).length, {
      timeout: 30_000,
    })
    .toBe(1);
  return {
    originThreadId: originThreadId as string,
    channel: (await codeChannels(page.request))[0],
  };
}

test.describe("Slack Code Channels", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/mock/slack");
    await page.locator("#reset").click();
    await expect(page.locator("#thread")).toContainText("No messages yet");
  });

  test("opening a channel hands the task to a session of its own", async ({
    page,
  }) => {
    const { originThreadId, channel } = await openCodeChannel(page);
    expect(channel.name).toBe("Investigate flaky CI failures");

    // The session the channel was handed to picks the task up from its
    // instructions, and says so in the channel.
    await expect
      .poll(
        async () =>
          (await streams(page.request, channel.id))
            .map((stream) => stream.text)
            .join("\n"),
        { timeout: 60_000 },
      )
      .toContain(HANDOFF_LINE);

    // It is a session of its own, and the channel stayed open for it.
    const sessionThreadId = await boundThread(page.request, channel.id);
    expect(sessionThreadId).toBeTruthy();
    expect(sessionThreadId).not.toBe(originThreadId);
    expect((await codeChannels(page.request))[0].archived).toBe(false);
  });

  test("the channel session speaks without being asked to post", async ({
    page,
  }) => {
    const { channel } = await openCodeChannel(page);

    // No posting tool is involved: the run itself is streamed into the channel.
    await expect
      .poll(
        async () => {
          const [stream] = await streams(page.request, channel.id);
          return stream?.text ?? "";
        },
        { timeout: 60_000 },
      )
      .toContain(HANDOFF_LINE);

    const [stream] = await streams(page.request, channel.id);
    // A code channel is one flowing session, so the transcript is a top-level
    // message with the run's tool activity interleaved into what it says.
    expect(stream.thread_ts).toBe("");
    // Steps collapse into one plan block rather than a card per call.
    expect(stream.task_display_mode).toBe("plan");
    expect(Object.values(stream.tasks).length).toBeGreaterThan(0);
    await expect
      .poll(async () => (await streams(page.request, channel.id))[0]?.state, {
        timeout: 60_000,
      })
      .toBe("stopped");
  });
});
