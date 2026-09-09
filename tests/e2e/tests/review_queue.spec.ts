import { test, expect, type Page } from "@playwright/test";
import {
  SAME_USER,
  loginAs,
  seedPull,
  setReviewQueueFailure,
} from "./helpers/dashboard";

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

  test("narrows a repository to the paths configured on its chip", async ({
    page,
  }) => {
    await page.request.post("/control/reset");
    await loginAs(page, SAME_USER);

    const uiPull = await seedPull(page, {
      owner: "fakeorg",
      repo: "demo",
      title: "Ready: restyle the button",
      check_conclusion: "success",
      mergeable: true,
      file_paths: ["ui/src/a.tsx"],
    });
    const agentPull = await seedPull(page, {
      owner: "fakeorg",
      repo: "demo",
      title: "Ready: rename a node",
      check_conclusion: "success",
      mergeable: true,
      file_paths: ["agent/x.py"],
    });

    await page.goto("/agents/reviews");
    await page.getByTestId("review-queue-tab").click();
    await addRepo(page, "fakeorg/demo");

    const uiRow = page.getByTestId(`review-queue-row-fakeorg/demo-${uiPull}`);
    const agentRow = page.getByTestId(
      `review-queue-row-fakeorg/demo-${agentPull}`,
    );
    await expect(uiRow).toBeVisible();
    await expect(agentRow).toBeVisible();

    await setPaths(page, "fakeorg/demo", "ui/");
    await expect(
      page.getByTestId("review-queue-chip-fakeorg/demo"),
    ).toContainText(/ui\/?/);
    await expect(uiRow).toBeVisible();
    await expect(agentRow).toHaveCount(0);
    await expect(uiRow.getByTestId("review-queue-matched-path")).toHaveText(
      /^ui\/?$/,
    );

    await setPaths(page, "fakeorg/demo", "");
    await expect(uiRow).toBeVisible();
    await expect(agentRow).toBeVisible();
  });

  test("surfaces the GitHub failure instead of an endless skeleton", async ({
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
    });

    await page.goto("/agents/reviews?tab=queue");
    await addRepo(page, "fakeorg/demo");
    const readyRow = page.getByTestId(`review-queue-row-fakeorg/demo-${ready}`);
    await expect(readyRow).toBeVisible();

    await setReviewQueueFailure(page, true);
    await page.reload();
    const error = page.getByTestId("review-queue-error");
    await expect(error).toBeVisible();
    await expect(error).toContainText("could not fetch pull requests");

    await setReviewQueueFailure(page, false);
    await page.reload();
    await expect(readyRow).toBeVisible();
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

// The chip body opens the path editor: one path per line, empty clears the filter.
async function setPaths(page: Page, nameWithOwner: string, paths: string) {
  await page.getByTestId(`review-queue-chip-${nameWithOwner}`).click();
  const input = page.getByTestId("review-queue-paths-input");
  await expect(input).toBeVisible();
  await input.fill(paths);
  await page.getByTestId("review-queue-paths-save").click();
  await expect(input).toBeHidden();
}
