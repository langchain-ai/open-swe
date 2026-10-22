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
    await expect(owner.getByTestId("artifact-chat")).toBeVisible();
    await expect(owner.getByTestId("composer-editor")).toBeVisible();
    await expect(
      owner.getByRole("button", { name: "Submit comments" }),
    ).toHaveCount(0);

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
    await verificationHeading.click();
    await expect(
      owner.getByRole("list", {
        name: "Selections attached to your next message",
      }),
    ).toContainText("Artifact");
    await owner
      .getByTestId("composer-editor")
      .fill("Clarify the expected output.");
    await owner
      .getByRole("button", { name: "Send message", exact: true })
      .click();
    const feedback = owner
      .getByTestId("user-message")
      .filter({ hasText: "Clarify the expected output." });
    await expect(feedback).toBeVisible();
    await expect(feedback.locator("pre")).toContainText("Verification");
    await expect(
      owner.getByRole("list", {
        name: "Selections attached to your next message",
      }),
    ).toHaveCount(0);

    await owner.getByTestId("copy-plan").click();
    await expect(owner.getByTestId("copy-plan")).toContainText("Copied!");
    const clipboard = await owner.evaluate(() =>
      navigator.clipboard.readText(),
    );
    expect(clipboard).toContain("<title>Greeting Blueprint</title>");
    expect(clipboard).toContain("<h2>Verification</h2>");

    const artifact = await (
      await owner.request.get(`/dashboard/api/plan/${threadId}`)
    ).json();
    const updated = await owner.evaluate(
      async ({ threadId, html }) => {
        const result = await fetch(`/dashboard/api/plan/${threadId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ html }),
        });
        return result.ok;
      },
      {
        threadId,
        html: artifact.html.replace(
          "<h2>Verification</h2>",
          "<h2>Updated verification</h2>",
        ),
      },
    );
    expect(updated).toBeTruthy();
    await expect(
      ownerArtifact.contentFrame().getByText("Updated verification"),
    ).toBeVisible();
    await expect(feedback).toBeVisible();

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
    await expect(collab.getByTestId("artifact-chat")).toBeVisible();
    await expect(
      collab
        .getByTestId("user-message")
        .filter({ hasText: "Clarify the expected output." }),
    ).toBeVisible({ timeout: 30_000 });
    await collab.getByRole("link", { name: "Back to conversation" }).click();
    await expect(collab).toHaveURL(new RegExp(`/agents/${threadId}$`));
    await expect(collab.getByTestId("composer-editor")).toBeVisible();
    await expect(collab.getByTestId("inline-plan-artifact")).toBeVisible();

    const minified =
      "<html><body><section><h2>Section one</h2><p>Before first</p><p><span>Repeated label</span></p><p>After first</p></section><section><h2>Section two</h2><p>Before second</p><p><span>Repeated label</span></p><p>After second</p><p>Trailing second</p></section></body></html>";
    expect(
      await owner.evaluate(
        async ({ threadId, html }) =>
          (
            await fetch(`/dashboard/api/plan/${threadId}`, {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ html }),
            })
          ).ok,
        { threadId, html: minified },
      ),
    ).toBeTruthy();
    const repeated = ownerArtifact
      .contentFrame()
      .getByText("Repeated label", { exact: true })
      .last();
    await expect(repeated).toBeVisible();
    await repeated.evaluate((element) => {
      const range = document.createRange();
      range.selectNodeContents(element);
      const selection = window.getSelection();
      selection?.removeAllRanges();
      selection?.addRange(range);
      element.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    });
    await expect(
      owner.getByRole("list", {
        name: "Selections attached to your next message",
      }),
    ).toBeVisible();
    await owner
      .getByTestId("composer-editor")
      .fill("Disambiguate this repeated label.");
    const sent = owner.waitForRequest(
      (req) =>
        req.method() === "POST" &&
        req.url().includes(threadId) &&
        (req.postData()?.includes("Disambiguate this repeated label.") ??
          false),
    );
    await owner
      .getByRole("button", { name: "Send message", exact: true })
      .click();
    const modelInput = JSON.stringify((await sent).postDataJSON());
    expect(modelInput).toContain("Context before (not selected):");
    expect(modelInput).toContain("Section two");
    expect(modelInput).toContain("Before second");
    expect(modelInput).toContain("Selected text:");
    expect(modelInput).toContain("> 1 | Repeated label");
    expect(modelInput).toContain("Context after (not selected):");
    expect(modelInput).toContain("After second");
    expect(modelInput).toContain("Trailing second");
    expect(modelInput).not.toContain("Before first");
    const disambiguated = owner
      .getByTestId("user-message")
      .filter({ hasText: "Disambiguate this repeated label." });
    await expect(disambiguated.locator("pre")).toHaveText("Repeated label");

    const prs = (await (
      await request.get("/mock/github/data")
    ).json()) as Array<unknown>;
    expect(prs).toHaveLength(0);

    await ownerCtx.close();
    await collabCtx.close();
  });
});
