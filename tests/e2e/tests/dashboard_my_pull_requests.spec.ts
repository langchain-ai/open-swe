import { test, expect, type Locator, type Page } from "@playwright/test";
import {
  SAME_USER,
  loginAs,
  readPullRequest,
  seedOpenPullRequest,
  setRepoMergeMethods,
  type SeededPullRequest,
  type SeedPullRequestOptions,
} from "./helpers/dashboard";

const DEMO = "fakeorg/demo";
const COMPANION = "anotherorg/companion";

// The three row statuses the bulk preconditions key off, as overallStatus()
// derives them: a conflict wins over a failing check, and Approved needs a
// clean merge state, no failing checks and an APPROVED review.
const approved: SeedPullRequestOptions = {
  mergeable: true,
  mergeable_state: "clean",
  reviews: [{ author: "reviewer", state: "APPROVED" }],
};
const conflicted: SeedPullRequestOptions = {
  mergeable: false,
  mergeable_state: "dirty",
};
const failing: SeedPullRequestOptions = {
  mergeable: true,
  mergeable_state: "clean",
  check_runs: [
    { name: "unit-tests", status: "completed", conclusion: "failure" },
  ],
};

function row(page: Page, pr: SeededPullRequest) {
  return page.getByRole("listitem").filter({ has: selectBox(page, pr) });
}

function selectBox(page: Page, pr: SeededPullRequest) {
  return page.getByRole("checkbox", {
    name: `Select PR #${pr.number} in ${pr.repo}`,
  });
}

// Per-row Fix/Merge buttons carry the same accessible names as the bulk bar's,
// so every bulk assertion has to go through the group.
function bulkBar(page: Page) {
  return page.getByRole("group", { name: "Bulk pull request actions" });
}

async function openMine(page: Page) {
  await page.goto("/agents/reviews");
  await expect(
    page.getByRole("heading", { name: "Pull Requests" }),
  ).toBeVisible();
}

async function expectStatus(target: Locator, status: string) {
  await expect(target).toContainText(status, { timeout: 15_000 });
}

async function select(page: Page, ...pullRequests: SeededPullRequest[]) {
  for (const pr of pullRequests) await selectBox(page, pr).check();
  await expect(bulkBar(page)).toContainText(`${pullRequests.length} selected`);
}

test.describe("my pull requests", () => {
  test.beforeEach(async ({ page }) => {
    await page.request.post("/control/reset");
    await loginAs(page, SAME_USER);
  });

  test("lists the signed-in user's open PRs with live status", async ({
    page,
  }) => {
    const ok = await seedOpenPullRequest(page, {
      repo: DEMO,
      title: "Approved change",
      ...approved,
    });
    const conflict = await seedOpenPullRequest(page, {
      repo: COMPANION,
      title: "Conflicting change",
      ...conflicted,
    });
    const broken = await seedOpenPullRequest(page, {
      repo: DEMO,
      title: "Failing change",
      ...failing,
    });
    const theirs = await seedOpenPullRequest(page, {
      repo: DEMO,
      title: "Someone else's change",
      author: "bob",
      ...approved,
    });

    await openMine(page);

    await expectStatus(row(page, ok), "Approved");
    await expect(row(page, ok)).toContainText("Approved change");
    await expectStatus(row(page, conflict), "Conflicted");
    await expect(row(page, conflict)).toContainText(COMPANION);
    await expectStatus(row(page, broken), "Failing");
    await expect(row(page, broken)).toContainText("unit-tests");

    await expect(selectBox(page, theirs)).toHaveCount(0);
    await expect(page.getByText("Someone else's change")).toHaveCount(0);
  });

  test("selects rows individually, by page, and clears the selection", async ({
    page,
  }) => {
    const first = await seedOpenPullRequest(page, {
      repo: DEMO,
      title: "First change",
      ...approved,
    });
    const second = await seedOpenPullRequest(page, {
      repo: COMPANION,
      title: "Second change",
      ...conflicted,
    });
    const third = await seedOpenPullRequest(page, {
      repo: DEMO,
      title: "Third change",
      ...failing,
    });

    await openMine(page);
    await expect(selectBox(page, third)).toBeVisible();
    await expect(bulkBar(page)).toHaveCount(0);

    await select(page, first, second);

    await page
      .getByRole("checkbox", { name: "Select all PRs on this page" })
      .check();
    await expect(bulkBar(page)).toContainText("3 selected");
    await expect(selectBox(page, third)).toBeChecked();

    await bulkBar(page)
      .getByRole("button", { name: "Clear selection" })
      .click();
    await expect(bulkBar(page)).toHaveCount(0);
    await expect(selectBox(page, first)).not.toBeChecked();
  });

  test("closes the selected pull requests after confirmation", async ({
    page,
  }) => {
    const mine = await seedOpenPullRequest(page, {
      repo: DEMO,
      title: "Close me",
      ...conflicted,
    });
    const other = await seedOpenPullRequest(page, {
      repo: COMPANION,
      title: "Close me too",
      ...approved,
    });

    await openMine(page);
    await expectStatus(row(page, mine), "Conflicted");
    await select(page, mine, other);

    await bulkBar(page).getByRole("button", { name: "Close" }).click();
    const dialog = page.getByRole("alertdialog");
    await expect(
      dialog.getByRole("heading", { name: "Close 2 pull requests?" }),
    ).toBeVisible();
    await expect(dialog).toContainText(`${DEMO}#${mine.number}`);
    await expect(dialog).toContainText(`${COMPANION}#${other.number}`);

    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toHaveCount(0);
    await expect(selectBox(page, mine)).toBeVisible();

    await bulkBar(page).getByRole("button", { name: "Close" }).click();
    await page
      .getByRole("alertdialog")
      .getByRole("button", { name: "Close pull requests" })
      .click();

    await expect(page.getByText("Closed 2 pull requests")).toBeVisible();
    await expect(selectBox(page, mine)).toHaveCount(0);
    await expect(selectBox(page, other)).toHaveCount(0);

    expect(
      (await readPullRequest(page, "fakeorg", "demo", mine.number)).state,
    ).toBe("closed");
    expect(
      (await readPullRequest(page, "anotherorg", "companion", other.number))
        .state,
    ).toBe("closed");
  });

  test("queues fixes only when every selected PR is broken", async ({
    page,
  }) => {
    const conflict = await seedOpenPullRequest(page, {
      repo: DEMO,
      title: "Fix the conflict",
      ...conflicted,
    });
    const broken = await seedOpenPullRequest(page, {
      repo: COMPANION,
      title: "Fix the checks",
      ...failing,
    });
    const ok = await seedOpenPullRequest(page, {
      repo: DEMO,
      title: "Nothing to fix",
      ...approved,
    });

    await openMine(page);
    await expectStatus(row(page, conflict), "Conflicted");
    await expectStatus(row(page, broken), "Failing");
    await expectStatus(row(page, ok), "Approved");

    await select(page, conflict, ok);
    const fix = bulkBar(page).getByRole("button", { name: "Fix" });
    await expect(fix).toBeDisabled();
    await expect(fix).toHaveAttribute(
      "title",
      "Every selected PR must be conflicted or failing",
    );

    await selectBox(page, ok).uncheck();
    await selectBox(page, broken).check();
    await expect(bulkBar(page)).toContainText("2 selected");
    await expect(fix).toBeEnabled();

    // Each fix opens a thread and dispatches a real run, so wait on the
    // responses: a rejected one names the reason instead of timing out on the
    // toast that never arrives.
    const dispatched = Promise.all(
      [conflict, broken].map((pr) =>
        page.waitForResponse(
          (response) =>
            response.url().includes(`/${pr.number}/fix`) &&
            response.request().method() === "POST",
          { timeout: 30_000 },
        ),
      ),
    );
    await fix.click();
    for (const response of await dispatched) {
      expect(response.status(), await response.text()).toBe(200);
    }
    await expect(
      page.getByText("Queued fixes for 2 pull requests"),
    ).toBeVisible();
  });

  test("merges with the only method both repositories allow", async ({
    page,
  }) => {
    await setRepoMergeMethods(page, DEMO, ["squash"]);
    await setRepoMergeMethods(page, COMPANION, ["squash", "merge"]);
    const mine = await seedOpenPullRequest(page, {
      repo: DEMO,
      title: "Squash me",
      ...approved,
    });
    const other = await seedOpenPullRequest(page, {
      repo: COMPANION,
      title: "Squash me too",
      ...approved,
    });

    await openMine(page);
    await expectStatus(row(page, mine), "Approved");
    await expectStatus(row(page, other), "Approved");
    await select(page, mine, other);

    const merge = bulkBar(page).getByRole("button", { name: "Merge" });
    await expect(merge).toBeEnabled();
    await merge.click();

    const dialog = page.getByRole("dialog");
    await expect(
      dialog.getByRole("heading", { name: "Merge 2 pull requests?" }),
    ).toBeVisible();
    await expect(dialog).toContainText("Merge method: Squash merge");
    await expect(
      dialog.getByLabel("Merge method", { exact: true }),
    ).toHaveCount(0);

    await dialog.getByRole("button", { name: "Merge pull requests" }).click();
    await expect(page.getByText("Merged 2 pull requests")).toBeVisible();
    await expect(selectBox(page, mine)).toHaveCount(0);
    await expect(selectBox(page, other)).toHaveCount(0);

    const merged = await readPullRequest(page, "fakeorg", "demo", mine.number);
    expect(merged.merged).toBe(true);
    expect(merged.merge_method).toBe("squash");
    const mergedOther = await readPullRequest(
      page,
      "anotherorg",
      "companion",
      other.number,
    );
    expect(mergedOther.merged).toBe(true);
    expect(mergedOther.merge_method).toBe("squash");
  });

  test("requires a merge method when the repositories share several", async ({
    page,
  }) => {
    await setRepoMergeMethods(page, DEMO, ["squash", "rebase"]);
    await setRepoMergeMethods(page, COMPANION, ["squash", "rebase"]);
    const mine = await seedOpenPullRequest(page, {
      repo: DEMO,
      title: "Rebase me",
      ...approved,
    });
    const other = await seedOpenPullRequest(page, {
      repo: COMPANION,
      title: "Rebase me too",
      ...approved,
    });

    await openMine(page);
    await expectStatus(row(page, mine), "Approved");
    await expectStatus(row(page, other), "Approved");
    await select(page, mine, other);
    await bulkBar(page).getByRole("button", { name: "Merge" }).click();

    const dialog = page.getByRole("dialog");
    const method = dialog.getByLabel("Merge method", { exact: true });
    await expect(method).toBeVisible();
    const confirm = dialog.getByRole("button", {
      name: "Merge pull requests",
    });
    await expect(confirm).toBeDisabled();

    await method.selectOption("rebase");
    await expect(confirm).toBeEnabled();
    await confirm.click();

    await expect(page.getByText("Merged 2 pull requests")).toBeVisible();
    expect(
      (await readPullRequest(page, "fakeorg", "demo", mine.number))
        .merge_method,
    ).toBe("rebase");
    expect(
      (await readPullRequest(page, "anotherorg", "companion", other.number))
        .merge_method,
    ).toBe("rebase");
  });

  test("blocks merging when a selected PR is not approved", async ({
    page,
  }) => {
    const ok = await seedOpenPullRequest(page, {
      repo: DEMO,
      title: "Ready to merge",
      ...approved,
    });
    const conflict = await seedOpenPullRequest(page, {
      repo: COMPANION,
      title: "Not ready",
      ...conflicted,
    });

    await openMine(page);
    await expectStatus(row(page, ok), "Approved");
    await expectStatus(row(page, conflict), "Conflicted");
    await select(page, ok, conflict);

    const merge = bulkBar(page).getByRole("button", { name: "Merge" });
    await expect(merge).toBeDisabled();
    await expect(merge).toHaveAttribute(
      "title",
      "Every selected PR must be approved and passing",
    );
  });
});
