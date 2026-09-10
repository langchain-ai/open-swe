import { test, expect, type APIRequestContext } from "@playwright/test";

type CodeChannel = { id: string; name: string; status: string };

async function codeChannels(
  request: APIRequestContext,
): Promise<CodeChannel[]> {
  const response = await request.get("/mock/slack/code-channels");
  return (
    ((await response.json()) as { channels?: CodeChannel[] }).channels ?? []
  );
}

async function commandResponses(request: APIRequestContext): Promise<string[]> {
  const response = await request.get("/mock/slack/command-responses");
  const body = (await response.json()) as { responses?: { text?: string }[] };
  return (body.responses ?? []).map((answer) => answer.text ?? "");
}

async function invites(
  request: APIRequestContext,
): Promise<{ channel: string; users: string }[]> {
  const response = await request.get("/mock/slack/invites");
  return (
    (
      (await response.json()) as {
        invites?: { channel: string; users: string }[];
      }
    ).invites ?? []
  );
}

async function channelMessages(
  request: APIRequestContext,
  channel: string,
): Promise<string[]> {
  const response = await request.get(
    `/fake-slack/conversations.history?channel=${encodeURIComponent(channel)}`,
  );
  const body = (await response.json()) as { messages?: { text?: string }[] };
  return (body.messages ?? []).map((message) => message.text ?? "");
}

test.describe("Opening a code channel by command", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/mock/slack");
    await page.locator("#reset").click();
    await expect(page.locator("#thread")).toContainText("No messages yet");
  });

  test("a command starts a session in a channel of its own", async ({
    page,
  }) => {
    const ran = await page.request.post("/mock/slack/command", {
      data: { text: "E2E_CODE_CHANNEL investigate the flaky CI failures" },
    });
    expect(((await ran.json()) as { status: number }).status).toBe(200);

    await expect
      .poll(async () => (await codeChannels(page.request)).length, {
        timeout: 30_000,
      })
      .toBe(1);
    const [channel] = await codeChannels(page.request);

    // The caller is told where the work went, privately.
    await expect
      .poll(async () => (await commandResponses(page.request)).join("\n"), {
        timeout: 30_000,
      })
      .toContain(`<#${channel.id}>`);

    // And put in the channel, since a command leaves no message to invite them.
    await expect
      .poll(async () => await invites(page.request), { timeout: 30_000 })
      .toContainEqual({ channel: channel.id, users: "U_ALICE" });
  });

  test("nothing is posted in the channel the command was typed in", async ({
    page,
  }) => {
    await page.request.post("/mock/slack/command", {
      data: { text: "E2E_CODE_CHANNEL investigate the flaky CI failures" },
    });
    await expect
      .poll(async () => (await codeChannels(page.request)).length, {
        timeout: 30_000,
      })
      .toBe(1);

    // The origin channel is untouched: no thread, no announcement, nothing.
    expect(await channelMessages(page.request, "C_DEMO")).toEqual([]);
    await expect(page.locator("#thread")).toContainText("No messages yet");
  });

  test("a command with no prompt says what to type", async ({ page }) => {
    const ran = await page.request.post("/mock/slack/command", {
      data: { text: "  " },
    });

    const body = (await ran.json()) as { body?: { text?: string } };
    expect(body.body?.text).toContain("Say what the channel should work on");
    expect(await codeChannels(page.request)).toEqual([]);
  });
});
