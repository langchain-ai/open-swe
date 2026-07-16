import { test, expect, type Page } from "@playwright/test";

// Drives the REAL built ui/ app (served same-origin from the harness) for the
// Slack → web handoff. Only the LLM/GitHub/Slack/token boundaries are faked.
const SAME_USER = { login: "alice", email: "alice@example.com" };
const OTHER_USER = { login: "bob", email: "bob@example.com" };

async function loginAs(page: Page, user: { login: string; email: string }) {
  const res = await page.request.post("/control/login", { data: user });
  expect(res.ok()).toBeTruthy();
}

async function openRunningThreadViaSlackLink(page: Page) {
  await page.goto("/mock/slack");
  await page.locator("#reset").click();
  await expect(page.locator("#thread")).toContainText("No messages yet");
  await page
    .locator("#text")
    .fill("<@U0BOT> please add a greet() helper and open a PR");
  await page.locator("#send").click();

  const webLink = page.locator('.msg.bot a[href*="/agents/"]').first();
  await expect(webLink).toBeVisible();
  await webLink.click();
  await expect(page).toHaveURL(/\/agents\//);
}

// Run the Slack flow so a thread + PR exist, then click the bot's real
// "Open in Web" link, landing on the actual dashboard app.
async function openThreadViaSlackLink(page: Page) {
  await page.goto("/mock/slack");
  await page.locator("#reset").click();
  await expect(page.locator("#thread")).toContainText("No messages yet");
  await page
    .locator("#text")
    .fill("<@U0BOT> please add a greet() helper and open a PR");
  await page.locator("#send").click();
  await expect(
    page.locator(".msg.bot").filter({ hasText: "Add greet() helper" }),
  ).toBeVisible();

  const webLink = page.locator('.msg.bot a[href*="/agents/"]').first();
  await expect(webLink).toBeVisible();
  await webLink.click();
  await expect(page).toHaveURL(/\/agents\//);
}

// The SDK hydrates an idle thread's transcript from getState on load, which can
// briefly lag; a reload re-fetches it. Retry until the PR link renders.
async function expectTranscriptVisible(page: Page) {
  await expect(async () => {
    await page.reload();
    await expect(
      page.getByRole("link", { name: "Add greet() helper" }).first(),
    ).toBeVisible({ timeout: 8000 });
  }).toPass({ timeout: 60000 });
}

test.describe("Slack → web handoff (real dashboard UI)", () => {
  test("the SAME user continues the conversation in the web app", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await openThreadViaSlackLink(page);

    // The owner sees the composer (either the follow-up bar once the transcript
    // hydrates, or the empty-state bar before it — both mean they can type).
    const composer = page.getByPlaceholder(
      /Add a follow up|Send the first message/,
    );
    await expect(composer).toBeVisible();

    // Continue from the web — a new agent reply streams into the same thread.
    await composer.fill("Looks good — can you also add a docstring?");
    await composer.press("Enter");
    await expect(
      page.getByText(/anything else you'd like changed/),
    ).toBeVisible();

    // The transcript that started in Slack is here too (incl. the PR link).
    await expect(
      page.getByRole("link", { name: "Add greet() helper" }).first(),
    ).toBeVisible();
  });

  test("shows follow-ups queued while the agent is still running", async ({
    page,
  }, testInfo) => {
    await loginAs(page, SAME_USER);
    await openRunningThreadViaSlackLink(page);

    const queuedText = "Please queue this follow-up while you finish the PR.";
    const busyComposer = page.getByPlaceholder(
      "Send a message to queue next...",
    );
    await expect(busyComposer).toBeVisible();
    await busyComposer.fill(queuedText);
    await busyComposer.press("Enter");

    const queuedMessage = page
      .getByTestId("queued-message")
      .filter({ hasText: queuedText });
    await expect(queuedMessage).toBeVisible();
    const screenshotPath = testInfo.outputPath("queued-messages-dashboard.png");
    await page.screenshot({ path: screenshotPath, fullPage: true });
    await testInfo.attach("queued-messages-dashboard", {
      path: screenshotPath,
      contentType: "image/png",
    });
  });

  test("a DIFFERENT user can post, and their message is attributed", async ({
    page,
  }) => {
    await loginAs(page, OTHER_USER);
    await openThreadViaSlackLink(page);

    // The same thread + transcript is visible…
    await expectTranscriptVisible(page);

    // …and a non-owner now gets a composer too (owner-only restriction removed).
    const composer = page.getByPlaceholder(
      /Add a follow up|Send the first message/,
    );
    await expect(composer).toBeVisible();

    // Posting starts a new run — the agent's follow-up reply streams in.
    await composer.fill("Can you also add a docstring?");
    await composer.press("Enter");
    await expect(
      page.getByText(/anything else you'd like changed/),
    ).toBeVisible();

    // The non-owner's message is tagged server-side with their GitHub login, so
    // the owner can tell who sent it.
    await expect(
      page.getByText(new RegExp(`@${OTHER_USER.login}`)).first(),
    ).toBeVisible();
  });
});
