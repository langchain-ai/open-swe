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

async function botMessages(
  request: APIRequestContext,
  threadTs: string,
): Promise<Array<string>> {
  const res = await request.get("/mock/slack/messages");
  const msgs = (await res.json()) as Array<{
    text: string;
    is_bot: boolean;
    thread_ts: string;
  }>;
  return msgs
    .filter((m) => m.is_bot && m.thread_ts === threadTs)
    .map((m) => m.text);
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
  test("Slack plan request → comments → owner approves → PR", async ({
    browser,
    request,
  }) => {
    // 1. A user asks the bot to PLAN something in Slack.
    await request.post("/control/reset");
    const send = await request.post("/mock/slack/send", {
      data: {
        text: "<@U0BOT> plan how to add a greet() helper",
        mention_bot: true,
      },
    });
    const { thread_id: threadId, thread_ts: threadTs } =
      (await send.json()) as {
        thread_id: string;
        thread_ts: string;
      };
    expect(threadId).toBeTruthy();
    expect(threadTs).toBeTruthy();
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
      .poll(async () => (await botMessages(request, threadTs)).join("\n"), {
        timeout: 60_000,
      })
      .toMatch(/\/agents\/[^/]+\/plan\b/);
    await expect
      .poll(async () => (await botMessages(request, threadTs)).join("\n"), {
        timeout: 60_000,
      })
      .toMatch(/ready for review/i);

    // 3. A logged-out user follows the plan deep link, signs in through the fake
    //    GitHub OAuth simulator, and lands back on the same plan page.
    const loggedOutCtx = await browser.newContext();
    const loggedOut = await loggedOutCtx.newPage();
    await loggedOut.goto(planPath);
    await expect(loggedOut).toHaveURL(
      new RegExp(`/login\\?redirect=.*${threadId}.*plan`),
    );
    await expect(loggedOut.getByText("Sign in to open-swe")).toBeVisible({
      timeout: 30_000,
    });
    await loggedOut.getByRole("link", { name: "Continue with GitHub" }).click();
    await expect(loggedOut).toHaveURL(/\/fake-gh\/login\/oauth\/authorize/);
    await expect(loggedOut.getByTestId("fake-github-login")).toBeVisible();
    await loggedOut.getByLabel("GitHub user").selectOption(OWNER.login);
    await loggedOut.getByRole("button", { name: "Authorize open-swe" }).click();
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
    await expect(owner).toHaveURL(new RegExp(`/agents/${threadId}/plan$`));
    await expect(owner.getByTestId("plan-review")).toBeVisible({
      timeout: 30_000,
    });
    await expect(owner.getByText("Back to conversation")).toBeVisible();
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

    // 5. A COLLABORATOR opens the same plan: sees it AND the owner's comment
    //    (fetched over HTTP), but has NO approve button.
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
    await expect(collab.getByTestId("approve-plan")).toHaveCount(0);
    await expect(collab.getByTestId("reject-plan")).toBeVisible();

    // Collaborator leaves feedback with Ctrl+Enter.
    await addComment(
      collab,
      "Reviewer: please also add a docstring.",
      "control",
    );
    await expect(collab.getByTestId("plan-comment")).toHaveCount(2);

    // 6. The owner sees the collaborator's comment (polled), then approves and
    //    returns to the main conversation while implementation starts.
    await expect(owner.getByTestId("plan-comment")).toHaveCount(2, {
      timeout: 30_000,
    });
    await owner.getByTestId("approve-plan").click();
    await expect(owner).toHaveURL(new RegExp(`/agents/${threadId}$`));

    // 7. The agent implements, opens a PR, and links it back in the Slack thread,
    //    echoing the reviewers' feedback — which proves the comments were stored
    //    and harvested server-side on approve.
    await expect
      .poll(async () => (await botMessages(request, threadTs)).join("\n"), {
        timeout: 90_000,
      })
      .toMatch(/\/pull\//);
    expect((await botMessages(request, threadTs)).join("\n")).toMatch(
      /docstring/,
    );

    const prs = (await (
      await request.get("/mock/github/data")
    ).json()) as Array<unknown>;
    expect(prs.length).toBeGreaterThan(0);

    await ownerCtx.close();
    await collabCtx.close();
  });
});
