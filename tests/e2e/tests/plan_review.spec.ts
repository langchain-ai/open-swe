import {
  test,
  expect,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

// Full plan-review flow, driven through the mock Slack UI + the real dashboard:
//   user asks Open SWE in Slack to PLAN something ->
//   agent calls enter_plan_mode, posts the plan-review link to Slack, writes the
//   plan as a markdown file (save_plan), and posts "ready" back to Slack ->
//   owner (user1) and a collaborator (user2) open the plan and leave whole-document
//   comments over plain HTTP (each polls and sees the other's) ->
//   only the owner can approve -> on approval the agent implements, opens a PR,
//   and replies in Slack with the link, echoing the reviewers' comments (which the
//   server harvested from the comment store).
// Only the LLM is faked; all agent + dashboard code runs for real.

const OWNER = { login: "alice", email: "alice@example.com" };
const COLLABORATOR = { login: "bob", email: "bob@example.com" };

// Scope to one thread via the "Open in Web" link every bot post carries. A run
// started by an earlier spec can still be in flight and post here after this
// test's `/control/reset`, and an unscoped read lets that stale message satisfy
// a poll — which then stops waiting for the message this test actually wants.
async function botMessages(
  request: APIRequestContext,
  threadId?: string,
): Promise<Array<string>> {
  const res = await request.get("/mock/slack/messages");
  const msgs = (await res.json()) as Array<{ text: string; is_bot: boolean }>;
  return msgs
    .filter((m) => m.is_bot)
    .map((m) => m.text)
    .filter((text) => threadId === undefined || text.includes(threadId));
}

async function addComment(
  page: Page,
  text: string,
  shortcut?: "meta" | "control",
) {
  const input = page.getByTestId("comment-input");
  await input.fill(text);
  if (shortcut === "meta") {
    await input.press("Meta+Enter");
    return;
  }
  if (shortcut === "control") {
    await input.press("Control+Enter");
    return;
  }
  await page.getByTestId("comment-submit").click();
}

test.describe("Plan review (HTTP comments)", () => {
  test("Slack plan approval button starts implementation", async ({ page }) => {
    test.setTimeout(180_000);
    await page.goto("/mock/slack");
    await page.locator("#reset").click();
    await expect(page.locator("#thread")).toContainText("No messages yet");

    await page
      .locator("#text")
      .fill("<@U0BOT> plan how to add a greet() helper");
    await page.locator("#send").click();

    const ready = page
      .locator(".msg.bot")
      .filter({ hasText: /plan is ready for review/i });
    await expect(ready).toBeVisible({ timeout: 60_000 });
    const approve = ready.getByRole("button", {
      name: "Approve & implement",
    });
    await expect(approve).toBeVisible();
    await expect(
      ready.getByRole("button", { name: "Request changes" }),
    ).toBeVisible();

    await approve.click();
    await expect(ready).toContainText("Selected: Approve & implement");
    await expect(ready.getByRole("button")).toHaveCount(0);

    const reply = page
      .locator(".msg.bot")
      .filter({ has: page.locator('a[href*="/pull/"]') });
    await expect(reply).toBeVisible({ timeout: 90_000 });
    await expect(reply).toContainText("Add greet() helper");

    await page.goto("/mock/github");
    await expect(page.locator('.pr[data-pr="1"]')).toContainText(
      "Add greet() helper",
    );
  });

  test("Slack plan request → comments → collaborator approves → PR", async ({
    browser,
    request,
  }, testInfo) => {
    // 1. A user asks the bot to PLAN something in Slack.
    await request.post("/control/reset");
    const send = await request.post("/mock/slack/send", {
      data: {
        text: "<@U0BOT> plan how to add a greet() helper",
        mention_bot: true,
      },
    });
    const { thread_id: threadId } = (await send.json()) as {
      thread_id: string;
    };
    expect(threadId).toBeTruthy();
    const planPath = `/agents/${threadId}/plan`;

    // 1a. enter_plan_mode must actually engage, not error out. Its Command must
    //     carry a terminating ToolMessage; without it the tool call fails and is
    //     swallowed into an error tool message while the agent silently proceeds
    //     as a normal run. Assert the tool's success message landed in the thread.
    await expect
      .poll(
        async () => {
          const res = await request.get(`/threads/${threadId}/state`);
          const state = (await res.json()) as {
            values?: { messages?: Array<{ content?: unknown }> };
          };
          return (state.values?.messages ?? [])
            .map((m) =>
              typeof m.content === "string"
                ? m.content
                : JSON.stringify(m.content),
            )
            .some((c) => c.includes("Plan mode is active"));
        },
        { timeout: 60_000 },
      )
      .toBe(true);

    // 2. The agent shares the plan-review link, then announces the plan is ready.
    await expect
      .poll(async () => (await botMessages(request, threadId)).join("\n"), {
        timeout: 60_000,
      })
      .toMatch(/\/agents\/[^/]+\/plan\b/);
    await expect
      .poll(async () => (await botMessages(request, threadId)).join("\n"), {
        timeout: 60_000,
      })
      .toMatch(/ready for review/i);

    // 3. A logged-out user follows the plan deep link, signs in through the fake
    //    GitHub OAuth simulator, and lands back on the same plan page.
    const loggedOutCtx = await browser.newContext({
      viewport: { width: 900, height: 700 },
    });
    const loggedOut = await loggedOutCtx.newPage();
    await loggedOut.goto(planPath);
    await expect(loggedOut).toHaveURL(
      new RegExp(`/login\\?redirect=.*${threadId}.*plan`),
    );
    await expect(loggedOut.getByText("Sign in to Open SWE")).toBeVisible({
      timeout: 30_000,
    });
    await loggedOut.getByRole("link", { name: "Continue with GitHub" }).click();
    await expect(loggedOut).toHaveURL(/\/fake-gh\/login\/oauth\/authorize/);
    await expect(loggedOut.getByTestId("fake-github-login")).toBeVisible();
    await loggedOut.getByLabel("GitHub user").selectOption(OWNER.login);
    await loggedOut.getByRole("button", { name: "Authorize Open SWE" }).click();
    await expect(loggedOut).toHaveURL(new RegExp(`/agents/${threadId}/plan$`));
    await expect(loggedOut.getByTestId("plan-review")).toBeVisible({
      timeout: 30_000,
    });
    await expect(loggedOut.getByTestId("plan-document")).toContainText(
      "greet",
      {
        timeout: 30_000,
      },
    );
    const summaryBox = await loggedOut
      .getByTestId("plan-summary")
      .boundingBox();
    const actionsBox = await loggedOut
      .getByTestId("plan-actions")
      .boundingBox();
    expect(summaryBox).not.toBeNull();
    expect(actionsBox).not.toBeNull();
    expect(actionsBox!.y).toBeGreaterThanOrEqual(
      summaryBox!.y + summaryBox!.height,
    );
    const screenshotPath = testInfo.outputPath("plan-review-tablet.png");
    await loggedOut
      .getByTestId("plan-review")
      .screenshot({ path: screenshotPath });
    await testInfo.attach("plan-review-tablet", {
      path: screenshotPath,
      contentType: "image/png",
    });
    await loggedOutCtx.close();

    // 4. The OWNER opens the conversation, follows the "Review plan" banner, and
    //    sees the rendered plan.
    const ownerCtx = await browser.newContext({
      permissions: ["clipboard-read", "clipboard-write"],
    });
    await ownerCtx.request.post("/control/login", { data: OWNER });
    const owner = await ownerCtx.newPage();
    await owner.goto(`/agents/${threadId}`);
    const reviewLink = owner.getByTestId("review-plan-link");
    await expect(reviewLink).toBeVisible({ timeout: 30_000 });
    await reviewLink.click();
    await expect(owner).toHaveURL(new RegExp(`/agents/${threadId}$`));
    await expect(
      owner.locator('button[aria-current="page"]', { hasText: "Plan" }),
    ).toBeVisible();
    await expect(owner.getByTestId("plan-review")).toBeVisible({
      timeout: 30_000,
    });
    await expect(owner.getByText("Back to conversation")).toHaveCount(0);
    await expect(owner.getByTestId("plan-document")).toContainText("greet", {
      timeout: 30_000,
    });
    await expect(owner.getByTestId("approve-plan")).toBeVisible();
    // "Request changes" is meaningless with no feedback → disabled until a
    // comment exists.
    await expect(owner.getByTestId("reject-plan")).toBeDisabled();

    // Copy the whole plan as markdown.
    await owner.getByTestId("copy-plan").click();
    await expect(owner.getByTestId("copy-plan")).toContainText("Copied!");
    const clipboard = await owner.evaluate(() =>
      navigator.clipboard.readText(),
    );
    expect(clipboard).toContain("## Plan: Add greet() helper");
    expect(clipboard).toContain("### Verification");

    // Owner leaves a comment with Cmd+Enter.
    await addComment(owner, "Owner: looks solid, ship it.", "meta");
    await expect(owner.getByTestId("plan-comment")).toHaveCount(1);
    await expect(owner.getByTestId("reject-plan")).toBeEnabled();

    // 5. A COLLABORATOR opens the same plan and sees the owner's comment.
    const collabCtx = await browser.newContext();
    await collabCtx.request.post("/control/login", { data: COLLABORATOR });
    const collab = await collabCtx.newPage();
    await collab.goto(planPath);
    await expect(collab.getByTestId("plan-review")).toBeVisible({
      timeout: 30_000,
    });
    await expect(collab.getByTestId("plan-document")).toContainText("greet", {
      timeout: 30_000,
    });
    await expect(collab.getByTestId("plan-comment")).toHaveCount(1, {
      timeout: 30_000,
    });
    await expect(collab.getByTestId("plan-comment")).toContainText(
      "looks solid",
    );
    await expect(collab.getByTestId("approve-plan")).toBeVisible();
    await expect(collab.getByTestId("reject-plan")).toBeVisible();

    // Collaborator leaves feedback with Ctrl+Enter.
    await addComment(
      collab,
      "Reviewer: please also add a docstring.",
      "control",
    );
    await expect(collab.getByTestId("plan-comment")).toHaveCount(2);

    // 6. The collaborator approves and returns to the main conversation while
    //    implementation starts.
    await collab.getByTestId("approve-plan").click();
    await expect(collab).toHaveURL(new RegExp(`/agents/${threadId}$`));

    // 7. The agent implements, opens a PR, and links it back in the Slack thread,
    //    echoing the reviewers' feedback — which proves the comments were stored
    //    and harvested server-side on approve.
    await expect
      .poll(async () => (await botMessages(request, threadId)).join("\n"), {
        timeout: 90_000,
      })
      .toMatch(/\/pull\//);
    expect((await botMessages(request, threadId)).join("\n")).toMatch(
      /docstring/,
    );

    const prs = (await (
      await request.get("/mock/github/data")
    ).json()) as Array<unknown>;
    expect(prs.length).toBeGreaterThan(0);

    await ownerCtx.close();
    await collabCtx.close();
  });

  test("plan decisions return to a connected conversation", async ({
    browser,
    request,
  }) => {
    test.setTimeout(180_000);
    await request.post("/control/reset");
    const send = await request.post("/mock/slack/send", {
      data: {
        text: "<@U0BOT> plan how to add a greet() helper",
        mention_bot: true,
      },
    });
    const { thread_id: threadId } = (await send.json()) as {
      thread_id: string;
    };
    const planPath = `/agents/${threadId}/plan`;

    await expect
      .poll(async () => (await botMessages(request, threadId)).join("\n"), {
        timeout: 60_000,
      })
      .toMatch(/ready for review/i);

    const ownerCtx = await browser.newContext();
    await ownerCtx.request.post("/control/login", { data: OWNER });
    const owner = await ownerCtx.newPage();
    await owner.goto(planPath);
    await expect(owner.getByTestId("plan-review")).toBeVisible({
      timeout: 30_000,
    });
    await addComment(owner, "Please revise the verification steps.");
    await expect(owner.getByTestId("reject-plan")).toBeEnabled();

    const hydrated = owner.waitForResponse((response) => {
      const path = new URL(response.url()).pathname;
      return (
        response.request().method() === "GET" &&
        path === `/dashboard/api/threads/${threadId}/state`
      );
    });
    await owner.getByTestId("reject-plan").click();
    await expect(owner).toHaveURL(new RegExp(`/agents/${threadId}$`));
    expect((await hydrated).ok()).toBeTruthy();
    await expect(
      owner.getByText(
        "I'll wait for your review and approval before implementing.",
      ),
    ).toHaveCount(2, { timeout: 60_000 });
    await expect(
      owner.getByText("A plan is ready for your review."),
    ).toBeVisible({ timeout: 60_000 });

    await owner.goto(planPath);
    await expect(owner.getByTestId("approve-plan")).toBeVisible({
      timeout: 30_000,
    });
    await owner.getByTestId("approve-plan").click();
    await expect(owner).toHaveURL(new RegExp(`/agents/${threadId}$`));
    await expect
      .poll(async () => {
        const prs = (await (
          await request.get("/mock/github/data")
        ).json()) as Array<unknown>;
        return prs.length;
      })
      .toBeGreaterThan(0);

    await ownerCtx.close();
  });
});
