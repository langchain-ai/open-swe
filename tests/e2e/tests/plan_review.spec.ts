import { test, expect, type APIRequestContext } from "@playwright/test";

// Full plan-review flow, driven through the mock Slack UI + the real dashboard:
//   user asks Open SWE in Slack to PLAN something ->
//   agent calls enter_plan_mode, posts the plan-review link to Slack, writes the
//   plan as a self-contained HTML file (save_plan), and posts "ready" back to Slack ->
//   owner (user1) and a collaborator (user2) open the full-screen plan artifact.
// Plan decisions stay in Slack, where approving starts implementation and opens a PR.
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

test.describe("Plan review", () => {
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

  test("Slack plan request → users see a full-screen artifact", async ({
    browser,
    request,
  }) => {
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

    const loggedOutCtx = await browser.newContext({
      viewport: { width: 900, height: 700 },
    });
    const loggedOut = await loggedOutCtx.newPage();
    await loggedOut.goto(planPath);
    await expect(loggedOut).toHaveURL(
      new RegExp(`/login\\?redirect=.*${threadId}.*plan`),
    );
    await loggedOut.getByRole("link", { name: "Continue with GitHub" }).click();
    await loggedOut.getByLabel("GitHub user").selectOption(OWNER.login);
    await loggedOut.getByRole("button", { name: "Authorize Open SWE" }).click();
    await expect(loggedOut).toHaveURL(new RegExp(`/agents/${threadId}/plan$`));
    await expect(
      loggedOut
        .getByTestId("plan-artifact-frame")
        .contentFrame()
        .getByText("Add greet() helper"),
    ).toBeVisible({ timeout: 30_000 });
    await expect(loggedOut.getByText("Back to conversation")).toBeVisible();
    await expect(loggedOut.getByTestId("plan-review")).toHaveCount(0);
    await loggedOutCtx.close();

    const ownerCtx = await browser.newContext();
    await ownerCtx.request.post("/control/login", { data: OWNER });
    const owner = await ownerCtx.newPage();
    await owner.goto(`/agents/${threadId}`);
    await owner.getByTestId("inline-plan-artifact").click();
    await expect(owner).toHaveURL(new RegExp(`/agents/${threadId}/plan$`));
    await expect(owner.getByTestId("plan-artifact-frame")).toBeVisible();
    await expect(owner.getByTestId("plan-review")).toHaveCount(0);
    await owner.getByText("Back to conversation").click();
    await expect(owner).toHaveURL(new RegExp(`/agents/${threadId}$`));
    await ownerCtx.close();

    const collabCtx = await browser.newContext();
    await collabCtx.request.post("/control/login", { data: COLLABORATOR });
    const collab = await collabCtx.newPage();
    await collab.goto(planPath);
    await expect(
      collab
        .getByTestId("plan-artifact-frame")
        .contentFrame()
        .getByText("Add greet() helper"),
    ).toBeVisible({ timeout: 30_000 });
    await expect(collab.getByTestId("plan-review")).toHaveCount(0);
    await collabCtx.close();
  });
});
