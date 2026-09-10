import { test, expect } from "@playwright/test";

import {
  SAME_USER,
  dismissOnboardingIfShown,
  loginAs,
  typeIntoComposer,
} from "./helpers/dashboard";

const DIFF_TITLE = "Show User E2E Diff";

// `show_user` is wired only for dashboard/desktop runs, so these drive the real
// dashboard composer rather than the Slack mock other specs use.
async function startWebThread(page: import("@playwright/test").Page) {
  await loginAs(page, SAME_USER);
  await page.goto("/agents");
  await dismissOnboardingIfShown(page);
}

test.describe("show_user", () => {
  test("renders a command's diff output as a card and quotes lines into the composer", async ({
    page,
  }) => {
    await startWebThread(page);
    await typeIntoComposer(page, "E2E_SHOW_USER_DIFF show me the change");
    await expect(page).toHaveURL(/\/agents\/[^/]+$/);

    // The card is titled by the tool call, and renders the patch through Pierre
    // rather than as a plain preformatted block.
    const card = page.locator("section").filter({ hasText: DIFF_TITLE });
    await expect(card).toBeVisible({ timeout: 60_000 });
    await expect(
      card.locator("[data-diff], [data-file]").first(),
    ).toBeVisible();
    await expect(card).toContainText("greet.py");

    // Nothing is selected yet, so there is nothing to quote.
    const comment = card.getByRole("button", { name: /^Comment/ });
    await expect(comment).toBeDisabled();

    // Select the added line by dragging down its gutter, then quote it.
    const addedLine = card.locator('[data-line-type="addition"]').first();
    await expect(addedLine).toBeVisible();
    await addedLine.click();
    await expect(comment).toBeEnabled();
    await comment.click();

    const editor = page.getByTestId("composer-editor");
    await expect(editor).toContainText("greet.py");
    await expect(editor).toContainText('return f"hi {name}"');
  });

  test("surfaces a failing command as a tool error instead of a card", async ({
    page,
  }) => {
    await startWebThread(page);
    await typeIntoComposer(page, "E2E_SHOW_USER_FAIL show me the output");
    await expect(page).toHaveURL(/\/agents\/[^/]+$/);

    // The agent's closing message reports the failure, and the transcript
    // carries the captured stderr rather than a card built from partial stdout.
    await expect(page.getByText("no card was rendered")).toBeVisible({
      timeout: 60_000,
    });
    await expect(page.getByText("boom-e2e")).toBeVisible();
    await expect(
      page.locator("section").filter({ hasText: "Show User" }),
    ).toHaveCount(0);
  });
});
