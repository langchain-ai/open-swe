import {
  test,
  expect,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

type CodeChannel = { id: string; status: string; archived: boolean };

type TimelineEntry = {
  kind: "text" | "task" | "plan";
  text?: string;
  id?: string;
  title?: string;
  status?: string;
};

type Stream = {
  ts: string;
  channel: string;
  thread_ts: string;
  task_display_mode: string;
  recipient_user_id: string;
  text: string;
  state: string;
  session_status: string;
  timeline: TimelineEntry[];
  plan_title: string;
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

/** Open a code channel and wait until its own session has spoken in it. */
async function channelWithSession(page: Page): Promise<CodeChannel> {
  await page.locator("#send").click();
  await expect
    .poll(async () => (await codeChannels(page.request)).length, {
      timeout: 30_000,
    })
    .toBe(1);
  const channel = (await codeChannels(page.request))[0];
  await expect
    .poll(
      async () =>
        (await streams(page.request, channel.id))
          .map((stream) => stream.text)
          .join("\n"),
      { timeout: 60_000 },
    )
    .toContain(HANDOFF_LINE);
  return channel;
}

async function sendInChannel(
  page: Page,
  channel: string,
  text: string,
  threadTs?: string,
): Promise<string> {
  const response = await page.request.post("/mock/slack/code-channel/send", {
    data: { channel, text, ...(threadTs ? { thread_ts: threadTs } : {}) },
  });
  const body = (await response.json()) as { ok: boolean; ts: string };
  expect(body.ok).toBe(true);
  return body.ts;
}

test.describe("Slack code channel transcript", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/mock/slack");
    await page.locator("#reset").click();
    await expect(page.locator("#thread")).toContainText("No messages yet");
  });

  test("the agent's words arrive before the cards that explain them", async ({
    page,
  }) => {
    const channel = await channelWithSession(page);
    const [stream] = await streams(page.request, channel.id);

    // Nothing is carded before the agent has spoken, and the words announcing a
    // tool call arrive before that call's card.
    const spoken = stream.timeline.findIndex(
      (entry) =>
        entry.kind === "text" &&
        (entry.text ?? "").includes("Looking at what the checkout"),
    );
    const carded = stream.timeline.findIndex(
      (entry) => entry.kind === "task" && entry.title === "Listed /workspace",
    );
    expect(spoken).toBeGreaterThanOrEqual(0);
    expect(carded).toBeGreaterThan(spoken);

    // The block is named once, from the words that opened the turn.
    expect(stream.plan_title).toBe(
      "Publishing the diff view for the handed-over task.",
    );

    // A Slack tool's whole effect is this channel, so it draws no card.
    expect(
      stream.timeline.filter(
        (entry) =>
          entry.kind === "task" &&
          (entry.title ?? "").includes("manage code channel"),
      ),
    ).toEqual([]);
  });

  test("the transcript is one top-level streaming message per turn", async ({
    page,
  }) => {
    const channel = await channelWithSession(page);
    const [stream] = await streams(page.request, channel.id);

    expect(stream.thread_ts).toBe("");
    expect(stream.task_display_mode).toBe("plan");
    // Streaming into a channel needs a recipient, or Slack refuses the call.
    expect(stream.recipient_user_id).toBeTruthy();

    // The turn is closed out, and the session goes back to waiting for input.
    await expect
      .poll(async () => (await streams(page.request, channel.id))[0]?.state, {
        timeout: 60_000,
      })
      .toBe("stopped");
    // Closing the stream reports the session status, so the channel stops
    // showing the agent as working even if nothing else says so.
    const [stopped] = await streams(page.request, channel.id);
    expect(stopped.session_status).toBe("active");
    await expect
      .poll(async () => (await codeChannels(page.request))[0]?.status, {
        timeout: 60_000,
      })
      .toBe("active");
  });

  test("a second turn gets its own streaming message", async ({ page }) => {
    const channel = await channelWithSession(page);
    await sendInChannel(
      page,
      channel.id,
      "Anything else? E2E_CODE_CHANNEL_FOLLOWUP",
    );

    await expect
      .poll(async () => (await streams(page.request, channel.id)).length, {
        timeout: 60_000,
      })
      .toBe(2);
    const [, second] = await streams(page.request, channel.id);
    expect(second.text).toContain("this follow-up reached the channel session");
    expect(second.thread_ts).toBe("");
  });

  test("a reply in a thread the user started streams into that thread", async ({
    page,
  }) => {
    const channel = await channelWithSession(page);
    const rootTs = await sendInChannel(page, channel.id, "A thread starter");
    await sendInChannel(
      page,
      channel.id,
      "And the question E2E_CODE_CHANNEL_FOLLOWUP",
      rootTs,
    );

    await expect
      .poll(
        async () =>
          (await streams(page.request, channel.id)).map(
            (stream) => stream.thread_ts,
          ),
        { timeout: 60_000 },
      )
      .toContain(rootTs);
  });

  test("a message past Slack's text cap continues in another message", async ({
    page,
  }) => {
    const channel = await channelWithSession(page);
    await sendInChannel(
      page,
      channel.id,
      "Write it all out E2E_CODE_CHANNEL_LONG",
    );

    // One turn, more than one streaming message: the transcript rolled over
    // rather than losing the append Slack would have rejected.
    await expect
      .poll(async () => (await streams(page.request, channel.id)).length, {
        timeout: 60_000,
      })
      .toBeGreaterThan(2);
    const rolled = (await streams(page.request, channel.id)).slice(1);
    expect(rolled.every((stream) => stream.text.length <= 9_000)).toBe(true);
    expect(rolled.map((stream) => stream.text).join("")).toContain(
      "Here is the long report.",
    );
  });

  test("a failed tool the agent recovers from does not read as an error", async ({
    page,
  }) => {
    const channel = await channelWithSession(page);
    await sendInChannel(
      page,
      channel.id,
      "Try the bogus action E2E_CODE_CHANNEL_TOOL_FAILURE",
    );

    // The agent's own words carry what went wrong.
    await expect
      .poll(
        async () =>
          (await streams(page.request, channel.id))
            .map((stream) => stream.text)
            .join(""),
        { timeout: 60_000 },
      )
      .toContain("That call was rejected.");

    // The card stays quiet: the run recovered, so nothing about it went wrong.
    const statuses = (await streams(page.request, channel.id))
      .flatMap((stream) => stream.timeline)
      .filter((entry) => entry.kind === "task")
      .map((entry) => entry.status);
    expect(statuses).not.toContain("error");
  });

  test("the dashboard links a channel session to its channel", async ({
    page,
  }) => {
    const channel = await channelWithSession(page);
    await page.request.post("/control/login", {
      data: { login: "alice", email: "alice@example.com" },
    });

    const findThread = async (): Promise<string | null> => {
      const response = await page.request.get("/dashboard/api/threads");
      if (!response.ok()) return null;
      const threads = (await response.json()) as {
        id: string;
        codeChannelUrl?: string | null;
      }[];
      return (
        threads.find((thread) =>
          (thread.codeChannelUrl ?? "").includes(channel.id),
        )?.id ?? null
      );
    };
    await expect.poll(findThread, { timeout: 60_000 }).not.toBeNull();
    const threadId = await findThread();

    await page.goto(`/agents/${threadId}`);
    const link = page.getByRole("link", { name: "Open code channel" });
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute(
      "href",
      new RegExp(`channel=${channel.id}&team=T_TEST`),
    );
  });
});
