import { test, expect, type Page } from "@playwright/test";
import { SAME_USER, loginAs, seedPull } from "./helpers/dashboard";

test.describe("review queue", () => {
  test("lists only ready pull requests from the repositories the user picked", async ({
    page,
  }) => {
    await page.request.post("/control/reset");
    await loginAs(page, SAME_USER);

    const ready = await seedPull(page, {
      owner: "fakeorg",
      repo: "demo",
      title: "Ready: add greet helper",
      check_conclusion: "success",
      mergeable: true,
      additions: 12,
      deletions: 3,
      files: 2,
    });
    const draft = await seedPull(page, {
      owner: "fakeorg",
      repo: "demo",
      title: "Draft: still cooking",
      draft: true,
      check_conclusion: "success",
    });
    const failing = await seedPull(page, {
      owner: "fakeorg",
      repo: "demo",
      title: "Failing: checks are red",
      check_conclusion: "failure",
    });
    const conflicting = await seedPull(page, {
      owner: "fakeorg",
      repo: "demo",
      title: "Conflicting: needs a rebase",
      check_conclusion: "success",
      mergeable: false,
    });
    const companion = await seedPull(page, {
      owner: "anotherorg",
      repo: "companion",
      title: "Companion ready",
      check_conclusion: "success",
      additions: 4,
      deletions: 1,
      files: 1,
    });

    await page.goto("/agents/reviews");
    await page.getByTestId("review-queue-tab").click();
    await expect(page.getByTestId("review-queue-empty")).toBeVisible();

    await addRepo(page, "fakeorg/demo");
    await expect(
      page.getByTestId("review-queue-chip-fakeorg/demo"),
    ).toBeVisible();

    const readyRow = page.getByTestId(`review-queue-row-fakeorg/demo-${ready}`);
    await expect(readyRow).toBeVisible();
    await expect(readyRow).toContainText("Ready: add greet helper");
    await expect(readyRow).toContainText("+12");
    await expect(readyRow).toContainText("−3");
    await expect(readyRow).toContainText("2 files");

    for (const number of [draft, failing, conflicting]) {
      await expect(
        page.getByTestId(`review-queue-row-fakeorg/demo-${number}`),
      ).toHaveCount(0);
    }
    const companionRow = page.getByTestId(
      `review-queue-row-anotherorg/companion-${companion}`,
    );
    await expect(companionRow).toHaveCount(0);

    await addRepo(page, "anotherorg/companion");
    await expect(companionRow).toBeVisible();
    await expect(companionRow).toContainText("Companion ready");

    await page.reload();
    await page.getByTestId("review-queue-tab").click();
    await expect(
      page.getByTestId("review-queue-chip-fakeorg/demo"),
    ).toBeVisible();
    await expect(
      page.getByTestId("review-queue-chip-anotherorg/companion"),
    ).toBeVisible();

    await page.getByRole("button", { name: "Remove fakeorg/demo" }).click();
    await expect(readyRow).toHaveCount(0);
    await expect(companionRow).toBeVisible();

    await page
      .getByRole("button", { name: "Remove anotherorg/companion" })
      .click();
    await expect(page.getByTestId("review-queue-empty")).toBeVisible();
  });
});

// The picker is a popover whose panel overlays the chips and rows, so close it
// again — a second click on the trigger would toggle it shut instead of open.
async function addRepo(page: Page, nameWithOwner: string) {
  await page.getByTestId("review-queue-repo-picker").click();
  const input = page.getByTestId("review-queue-repo-input");
  await input.fill(nameWithOwner);
  await input.press("Enter");
  await page.keyboard.press("Escape");
  await expect(input).toBeHidden();
}
