import { test, expect } from "@playwright/test";

import { plannedCalls } from "./helpers/dashboard";

// Full happy path, driven entirely through the mock Slack + GitHub UIs:
//   user asks in Slack -> real agent implements in a local sandbox -> opens a PR
//   on the fake GitHub -> replies with the PR link in the SAME Slack thread.
// Only the LLM is faked; all agent code runs for real via langgraph dev.
test.describe("Open SWE full flow", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/mock/slack");
    await page.locator("#reset").click();
    await expect(page.locator("#thread")).toContainText("No messages yet");
  });

  test("Slack request → implements → opens PR → links it back in the thread", async ({
    page,
  }) => {
    await page
      .locator("#text")
      .fill("<@U0BOT> please add a greet() helper and open a PR");
    await page.locator("#send").click();

    // The user's message lands in the thread.
    await expect(
      page.locator(".msg").filter({ hasText: "add a greet() helper" }),
    ).toBeVisible();

    // The agent replies in the SAME thread with a link to the PR it opened.
    const reply = page
      .locator(".msg.bot")
      .filter({ hasText: "Add greet() helper" });
    await expect(reply).toBeVisible();
    const prLink = reply.locator('a[href*="/pull/"]');
    await expect(prLink).toBeVisible();
    const webLink = page.locator('.msg.bot a[href*="/agents/"]').first();
    const threadId =
      new URL((await webLink.getAttribute("href")) ?? "", "http://x").pathname
        .split("/")
        .pop() ?? "";

    // The run is worked out once as it starts: every other call, its preparation,
    // model calls and tool calls included, is answered from its token, as by a
    // replica that never served the run.
    expect(await plannedCalls(page, threadId)).toEqual(["start"]);

    // Follow the link → the PR page shows what GitHub would show.
    await prLink.click();
    await expect(page.locator("#pr-title")).toContainText("Add greet() helper");
    await expect(page.locator("#pr-state")).toHaveText("open");
    await expect(page.locator("#pr-head")).toHaveText("add-greet");
    await expect(
      page.locator('#pr-files li[data-file="greet.py"]'),
    ).toBeVisible();

    // The PR list view shows it too.
    await page.goto("/mock/github");
    await expect(page.locator('.pr[data-pr="1"]')).toContainText(
      "Add greet() helper",
    );
    await expect(page.locator('.pr[data-pr="1"]')).toContainText("greet.py");
  });

  test("Slack DM request without a bot mention still starts a run", async ({
    page,
    request,
  }) => {
    const send = await request.post("/mock/slack/send", {
      data: {
        text: "please add a greet() helper and open a PR",
        mention_bot: false,
        channel_type: "im",
      },
    });
    const sent = await send.json();
    expect(sent.webhook).toMatchObject({
      status: "accepted",
      message: "Slack mention queued",
    });

    await expect(
      page.locator(".msg").filter({ hasText: "add a greet() helper" }),
    ).toBeVisible();
    const reply = page
      .locator(".msg.bot")
      .filter({ hasText: "Add greet() helper" });
    await expect(reply).toBeVisible();
    await expect(reply.locator('a[href*="/pull/"]')).toBeVisible();
  });

  test("user memory recalls a number in a fresh DM thread", async ({
    page,
    request,
  }) => {
    const number = crypto.getRandomValues(new Uint32Array(1))[0].toString();
    const savedResponse = await request.post("/mock/slack/send", {
      data: {
        text: `Remember this number: E2E_MEMORY_SAVE ${number}`,
        mention_bot: false,
        channel_type: "im",
        channel: "D_MEMORY_ALICE",
      },
    });
    expect(savedResponse.ok()).toBeTruthy();
    const saved = await savedResponse.json();
    expect(saved.webhook.status).toBe("accepted");
    await expect(
      page
        .locator(".msg.bot")
        .filter({ hasText: "Your number is saved." })
        .first(),
    ).toBeVisible({
      timeout: 60_000,
    });
    await expect
      .poll(async () => {
        const response = await request.get(`/threads/${saved.thread_id}`);
        return (await response.json()).status;
      })
      .not.toBe("busy");

    const response = await request.post("/mock/slack/send", {
      data: {
        text: "What number did I ask you to remember? E2E_MEMORY_RECALL",
        mention_bot: false,
        channel_type: "im",
        channel: "D_MEMORY_ALICE",
      },
    });
    expect(response.ok()).toBeTruthy();
    const recalled = await response.json();
    expect(recalled.webhook.status).toBe("accepted");
    expect(recalled.thread_id).not.toBe(saved.thread_id);
    const replies = page.locator(
      `.msg.bot[data-thread-ts="${recalled.thread_ts}"]`,
    );
    await expect(replies.first()).toContainText(
      `Your remembered number is ${number}.`,
      { timeout: 60_000 },
    );
  });

  test("Slack breakout request starts a new top-level Open SWE thread", async ({
    page,
  }) => {
    await page
      .locator("#text")
      .fill(
        "<@U0BOT> please break out adding a greet() helper into a separate thread",
      );
    await page.locator("#send").click();

    await expect(
      page.getByRole("link", { name: "the breakout thread", exact: true }),
    ).toBeVisible({ timeout: 60_000 });
    await page.getByRole("button", { name: "# demo", exact: true }).click();

    const breakout = page
      .locator(".msg.bot")
      .filter({ hasText: /`\/breakout`: Add greet\(\) helper/ });
    await expect(breakout).toBeVisible({ timeout: 60_000 });
    const breakoutThreadTs = await breakout.getAttribute("data-thread-ts");
    expect(breakoutThreadTs).toBeTruthy();

    const breakoutThreadMessages = page.locator(
      `.msg.bot[data-thread-ts="${breakoutThreadTs}"]`,
    );
    await expect(
      breakoutThreadMessages.locator('a[href*="/agents/"]').first(),
    ).toBeVisible({
      timeout: 60_000,
    });
  });
});
