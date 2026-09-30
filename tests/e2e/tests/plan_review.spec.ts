import { test, expect, type APIRequestContext } from "@playwright/test";

const OWNER = { login: "alice", email: "alice@example.com" };
const COLLABORATOR = { login: "bob", email: "bob@example.com" };

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

test.describe("HTML artifacts", () => {
  test("Slack publishes an artifact link without decision buttons", async ({
    page,
  }) => {
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
    await expect(ready.locator('a[href*="/plan"]')).toBeVisible();
    await expect(ready.getByRole("button")).toHaveCount(0);
  });

  test("Slack artifact → sign in → view and comment together", async ({
    browser,
    request,
  }, testInfo) => {
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
    await expect(
      loggedOut
        .getByTestId("plan-artifact-frame")
        .contentFrame()
        .getByText("Add greet() helper"),
    ).toBeVisible({ timeout: 30_000 });
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

    const ownerCtx = await browser.newContext({
      permissions: ["clipboard-read", "clipboard-write"],
    });
    await ownerCtx.request.post("/control/login", { data: OWNER });
    const owner = await ownerCtx.newPage();
    await owner.goto(`/agents/${threadId}`);
    const reviewLink = owner.getByTestId("inline-plan-artifact");
    await expect(reviewLink).toBeVisible({ timeout: 30_000 });
    await expect(reviewLink).toHaveCSS("height", "250px");
    await expect(owner.getByTestId("inline-plan-fade")).toBeVisible();
    await expect(
      owner.locator('button[aria-current="page"]', { hasText: "Plan" }),
    ).toHaveCount(0);
    await reviewLink.click();
    await expect(owner).toHaveURL(new RegExp(`/agents/${threadId}/plan$`));
    await expect(owner.getByTestId("plan-review")).toBeVisible({
      timeout: 30_000,
    });
    await expect(owner.getByText("Back to conversation")).toBeVisible();
    const ownerArtifact = owner.getByTestId("plan-artifact-frame");
    await expect(
      ownerArtifact.contentFrame().getByText("Add greet() helper"),
    ).toBeVisible({
      timeout: 30_000,
    });
    await expect(ownerArtifact).toHaveAttribute(
      "sandbox",
      "allow-scripts allow-downloads",
    );
    const embeddedSummaryBox = await owner
      .getByTestId("plan-summary")
      .boundingBox();
    const embeddedActionsBox = await owner
      .getByTestId("plan-actions")
      .boundingBox();
    expect(embeddedSummaryBox).not.toBeNull();
    expect(embeddedActionsBox).not.toBeNull();
    expect(embeddedActionsBox!.x).toBeGreaterThanOrEqual(
      embeddedSummaryBox!.x + embeddedSummaryBox!.width,
    );
    await expect(
      owner.getByRole("button", { name: /approve|request changes/i }),
    ).toHaveCount(0);
    await expect(owner.getByTestId("edit-plan")).toHaveCount(0);
    await expect(owner.getByTestId("plan-editor")).toHaveCount(0);
    await expect(owner.getByTestId("plan-comments")).toBeVisible();

    const verificationHeading = ownerArtifact
      .contentFrame()
      .getByText("Verification");
    await verificationHeading.evaluate((element) => {
      const range = document.createRange();
      range.selectNodeContents(element);
      const selection = window.getSelection();
      selection?.removeAllRanges();
      selection?.addRange(range);
      element.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    });
    await expect(owner.getByTestId("comment-composer")).toContainText(
      "Verification",
    );
    await owner
      .getByTestId("comment-input")
      .fill("Clarify the expected output.");
    await owner.getByTestId("comment-submit").click();
    await expect(owner.getByTestId("plan-comment")).toContainText(
      "Clarify the expected output.",
    );
    await expect(
      ownerArtifact.contentFrame().locator(".plan-annotation-marker"),
    ).toBeVisible();

    await owner.getByTestId("copy-plan").click();
    await expect(owner.getByTestId("copy-plan")).toContainText("Copied!");
    const clipboard = await owner.evaluate(() =>
      navigator.clipboard.readText(),
    );
    expect(clipboard).toContain("<title>Greeting Blueprint</title>");
    expect(clipboard).toContain("<h2>Verification</h2>");

    const collabCtx = await browser.newContext();
    await collabCtx.request.post("/control/login", { data: COLLABORATOR });
    const collab = await collabCtx.newPage();
    await collab.goto(planPath);
    await expect(collab.getByTestId("plan-review")).toBeVisible({
      timeout: 30_000,
    });
    await expect(
      collab
        .getByTestId("plan-artifact-frame")
        .contentFrame()
        .getByText("Add greet() helper"),
    ).toBeVisible({ timeout: 30_000 });
    await expect(collab.getByTestId("plan-comments")).toBeVisible();
    await expect(
      collab.getByRole("button", { name: /approve|request changes/i }),
    ).toHaveCount(0);
    await expect(collab.getByTestId("plan-comment")).toContainText(
      "Clarify the expected output.",
    );
    await expect(collab.getByTestId("comment-delete")).toHaveCount(0);
    await owner.getByTestId("comment-delete").click();
    await expect(owner.getByTestId("plan-comment")).toHaveCount(0);
    await expect(collab.getByTestId("plan-comment")).toHaveCount(0);
    await expect(
      ownerArtifact.contentFrame().locator(".plan-annotation-marker"),
    ).toHaveCount(0);

    const hydrated = collab.waitForResponse((response) => {
      const path = new URL(response.url()).pathname;
      return (
        response.request().method() === "GET" &&
        (path === `/dashboard/api/threads/${threadId}/state` ||
          path === `/dashboard/api/threads/${threadId}/transcript`)
      );
    });
    await collab.getByRole("link", { name: "Back to conversation" }).click();
    await expect(collab).toHaveURL(new RegExp(`/agents/${threadId}$`));
    expect((await hydrated).ok()).toBeTruthy();
    await expect(collab.getByTestId("composer-editor")).toBeVisible();
    await expect(collab.getByTestId("inline-plan-artifact")).toBeVisible();

    const prs = (await (
      await request.get("/mock/github/data")
    ).json()) as Array<unknown>;
    expect(prs).toHaveLength(0);

    await ownerCtx.close();
    await collabCtx.close();
  });
});
