import { test, expect, type Page } from "@playwright/test";

import { SAME_USER, loginAs } from "./helpers/dashboard";

// An agent-defined slacklet, end to end, with only the LLM and the external
// SaaS boundaries faked:
//
//   someone asks for a release checklist in mock Slack ->
//   the agent writes a JavaScript program and calls the real `create_slacklet`,
//   which runs its `init` and `render` in QuickJS and posts the message ->
//   `update_slacklet` applies a `check` command through the program's `apply`,
//   editing the message in place ->
//   an op the program refuses changes nothing and the agent gets the error ->
//   the web UI shows the same message and the command log.

function slacklet(page: Page) {
  return page
    .locator(".msg.bot")
    .filter({ hasText: "Release v1.2.0" })
    .filter({ hasNotText: "Checklist is live" });
}

test.describe("Agent-defined slacklet", () => {
  test("the agent's own program decides the state, the commands and the message", async ({
    page,
    request,
  }) => {
    test.setTimeout(180_000);
    await request.post("/control/reset");

    await page.goto("/mock/slack");
    await page
      .locator("#text")
      .fill(
        "<@U0BOT> track the release checklist for v1.2.0 here E2E_DYNAMIC_SLACKLET",
      );
    const sent = page.waitForResponse(
      (response) =>
        response.url().endsWith("/mock/slack/send") &&
        response.request().method() === "POST",
    );
    await page.locator("#send").click();
    const { thread_id: threadId } = (await (await sent).json()) as {
      thread_id: string;
    };
    expect(threadId).toBeTruthy();

    const reply = page
      .locator(".msg.bot")
      .filter({ hasText: "Checklist is live." });
    await expect(reply).toContainText(
      "My uncheck was refused: The command was refused and not applied: apply failed",
      { timeout: 120_000 },
    );
    await expect(reply).toContainText("unknown op uncheck");

    await expect(slacklet(page)).toHaveCount(1);
    await expect(slacklet(page)).toContainText("Release v1.2.0");
    await expect(slacklet(page)).toContainText("✅ Tag v1.2.0");
    await expect(slacklet(page)).toContainText("⬜ Publish notes");
    await expect(slacklet(page)).toContainText("1 of 3 done");
    await expect(slacklet(page)).not.toContainText("#2 check");

    // The checklist sits in a collapsible container, open until someone folds it.
    const box = slacklet(page).locator("details.container");
    await expect(box).toHaveAttribute("open", "");
    await box.locator("summary").click();
    await expect(box).not.toHaveAttribute("open", "");
    await expect(box.getByText("⬜ Publish notes")).toBeHidden();

    await loginAs(page, SAME_USER);
    await page.goto(`/agents/${threadId}`);
    await expect(page.getByTestId("slacklet-message")).toContainText(
      "✅ Tag v1.2.0",
      { timeout: 30_000 },
    );
    const container = page.getByTestId("slack-container");
    await expect(container).toContainText("1 of 3 done");
    await container.locator("summary").click();
    await expect(container.getByText("⬜ Publish notes")).toBeHidden();

    // The command log is a small closed disclosure under the message.
    const log = page.getByTestId("slacklet-log");
    await expect(log.locator("summary")).toHaveText("2 commands");
    await expect(page.getByTestId("slacklet-commands")).toBeHidden();
    await log.locator("summary").click();
    await expect(
      page.getByTestId("slacklet-commands").locator("li"),
    ).toHaveText([/#1 init/, /#2 check\s*Tagged v1\.2\.0/]);
  });
});
