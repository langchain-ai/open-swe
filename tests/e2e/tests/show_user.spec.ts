import { test, expect, type Page } from "@playwright/test";

import {
  SAME_USER,
  dismissOnboardingIfShown,
  loginAs,
  typeIntoComposer,
} from "./helpers/dashboard";

const DIFF_TITLE = "Show User E2E Diff";

// `show_user` is wired only for dashboard/desktop runs, so these drive the real
// dashboard composer rather than the Slack mock other specs use.
async function startWebThread(page: Page, prompt: string) {
  await loginAs(page, SAME_USER);
  await page.goto("/agents");
  await dismissOnboardingIfShown(page);
  await typeIntoComposer(page, prompt);
  await expect(page).toHaveURL(/\/agents\/[^/]+$/);
}

test.describe("show_user", () => {
  // Covers the whole path for real: the tool runs a command in the sandbox,
  // captures its stdout, and the artifact reaches the transcript as a diff card.
  // Line selection and composer quoting are covered by ShowUserCard's unit tests.
  test("renders a command's captured output as a diff card", async ({
    page,
  }) => {
    await startWebThread(page, "E2E_SHOW_USER_DIFF show me the change");

    const card = page.locator("section").filter({ hasText: DIFF_TITLE });
    await expect(card).toBeVisible({ timeout: 60_000 });
    await expect(card).toContainText("greet.py");
    await expect(card).toContainText('return f"hi {name}"');

    // Nothing is selected yet, so there is nothing to quote.
    await expect(card.getByRole("button", { name: /^Comment/ })).toBeDisabled();
  });

  test("renders no card when the command fails", async ({ page }) => {
    await startWebThread(page, "E2E_SHOW_USER_FAIL show me the output");

    await expect(page.getByText("no card was rendered")).toBeVisible({
      timeout: 60_000,
    });
    // The tool raised instead of building a card from the partial stdout.
    await expect(
      page.locator("section").filter({ hasText: "Show User" }),
    ).toHaveCount(0);
  });
});
