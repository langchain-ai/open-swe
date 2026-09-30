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

// The three card statuses the per-card actions key off, as overallStatus()
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

function card(page: Page, pr: SeededPullRequest) {
  return page
    .getByRole("listitem")
    .filter({ hasText: new RegExp(`${pr.repo} #${pr.number}(?!\\d)`) });
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

    await expectStatus(card(page, ok), "Approved");
    await expect(card(page, ok)).toContainText("Approved change");
    await expectStatus(card(page, conflict), "Conflicted");
    await expect(card(page, conflict)).toContainText(COMPANION);
    await expectStatus(card(page, broken), "Failing");
    await expect(card(page, broken)).toContainText("unit-tests");

    await expect(card(page, theirs)).toHaveCount(0);
    await expect(page.getByText("Someone else's change")).toHaveCount(0);
  });

  test("closes a pull request from its own card after confirmation", async ({
    page,
  }) => {
    const mine = await seedOpenPullRequest(page, {
      repo: DEMO,
      title: "Close me",
      ...conflicted,
    });
    const other = await seedOpenPullRequest(page, {
      repo: COMPANION,
      title: "Keep me",
      ...approved,
    });

    await openMine(page);
    await expectStatus(card(page, mine), "Conflicted");

    await card(page, mine)
      .getByRole("button", { name: "Close", exact: true })
      .click();
    const dialog = page.getByRole("dialog");
    await expect(
      dialog.getByRole("heading", { name: `Close ${DEMO}#${mine.number}?` }),
    ).toBeVisible();

    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toHaveCount(0);
    await expect(card(page, mine)).toBeVisible();

    await card(page, mine)
      .getByRole("button", { name: "Close", exact: true })
      .click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Close pull request" })
      .click();

    await expect(page.getByText(`Closed ${DEMO}#${mine.number}`)).toBeVisible();
    await expect(card(page, mine)).toContainText("Closed ·");
    await expect(
      card(page, mine).getByRole("button", { name: "Close", exact: true }),
    ).toHaveCount(0);

    expect(
      (await readPullRequest(page, "fakeorg", "demo", mine.number)).state,
    ).toBe("closed");
    expect(
      (await readPullRequest(page, "anotherorg", "companion", other.number))
        .state,
    ).toBe("open");
  });

  test("takes a draft out of draft from its own card", async ({ page }) => {
    const draft = await seedOpenPullRequest(page, {
      repo: DEMO,
      title: "Still a draft",
      draft: true,
      ...approved,
    });
    const ready = await seedOpenPullRequest(page, {
      repo: COMPANION,
      title: "Already ready",
      ...approved,
    });

    await openMine(page);
    await expectStatus(card(page, draft), "Draft");
    await expect(
      card(page, ready).getByRole("button", { name: "Mark ready" }),
    ).toHaveCount(0);

    await card(page, draft).getByRole("button", { name: "Mark ready" }).click();
    await expect(
      page.getByText(`Marked ${DEMO}#${draft.number} ready for review`),
    ).toBeVisible();

    expect(
      (await readPullRequest(page, "fakeorg", "demo", draft.number)).draft,
    ).toBe(false);
    await expect(card(page, draft)).not.toContainText("Draft");
  });

  test("offers a fix only on the cards that are broken", async ({ page }) => {
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
    await expectStatus(card(page, conflict), "Conflicted");
    await expectStatus(card(page, broken), "Failing");
    await expectStatus(card(page, ok), "Approved");

    const fix = card(page, broken).getByRole("button", {
      name: "Fix",
      exact: true,
    });
    await expect(fix).toBeEnabled();
    await expect(
      card(page, conflict).getByRole("button", { name: "Fix", exact: true }),
    ).toBeEnabled();
    // Exact, like the assertions above: the title is a button that opens the
    // preview, and "Nothing to fix" would match a substring search for "Fix".
    await expect(
      card(page, ok).getByRole("button", { name: "Fix", exact: true }),
    ).toHaveCount(0);

    // The fix opens a thread and dispatches a real run, so wait on the
    // response: a rejected one names the reason instead of timing out on the
    // toast that never arrives.
    const dispatched = page.waitForResponse(
      (response) =>
        response.url().includes(`/${broken.number}/thread`) &&
        response.request().method() === "POST",
      { timeout: 30_000 },
    );
    await fix.click();
    const response = await dispatched;
    expect(response.status(), await response.text()).toBe(200);
    await expect(
      page.getByText(`Fix queued for ${COMPANION}#${broken.number}`),
    ).toBeVisible();
  });

  test("offers only the merge method the repository allows", async ({
    page,
  }) => {
    await setRepoMergeMethods(page, DEMO, ["rebase"]);
    const mine = await seedOpenPullRequest(page, {
      repo: DEMO,
      title: "Rebase me",
      ...approved,
    });
    const other = await seedOpenPullRequest(page, {
      repo: COMPANION,
      title: "Leave me open",
      ...approved,
    });

    await openMine(page);
    await expectStatus(card(page, mine), "Approved");

    const method = card(page, mine).getByLabel(
      `Merge method for PR #${mine.number}`,
    );
    await expect(method).toBeEnabled();
    await expect(
      method.getByRole("option", { name: "Rebase merge", exact: true }),
    ).toHaveCount(1);
    await expect(
      method.getByRole("option", { name: "Squash merge", exact: true }),
    ).toHaveCount(0);
    await expect(
      method.getByRole("option", { name: "Merge commit", exact: true }),
    ).toHaveCount(0);

    await method.selectOption("rebase");
    await card(page, mine)
      .getByRole("button", { name: "Merge", exact: true })
      .click();

    await expect(page.getByText(`Merged ${DEMO}#${mine.number}`)).toBeVisible();
    await expect(card(page, mine)).toContainText("Merged ·");
    await expect(
      card(page, mine).getByRole("button", { name: "Merge", exact: true }),
    ).toHaveCount(0);

    const merged = await readPullRequest(page, "fakeorg", "demo", mine.number);
    expect(merged.merged).toBe(true);
    expect(merged.merge_method).toBe("rebase");
    expect(
      (await readPullRequest(page, "anotherorg", "companion", other.number))
        .merged,
    ).toBe(false);
  });

  test("withholds the merge until a method is chosen on the card", async ({
    page,
  }) => {
    await setRepoMergeMethods(page, DEMO, ["squash", "rebase"]);
    const mine = await seedOpenPullRequest(page, {
      repo: DEMO,
      title: "Pick one",
      ...approved,
    });

    await openMine(page);
    await expectStatus(card(page, mine), "Approved");

    const method = card(page, mine).getByLabel(
      `Merge method for PR #${mine.number}`,
    );
    const merge = card(page, mine).getByRole("button", {
      name: "Merge",
      exact: true,
    });
    // The select only settles once the allowed methods land, and the button is
    // disabled until then for that reason rather than for want of a choice.
    await expect(method).toBeEnabled();
    await expect(merge).toBeDisabled();
    await expect(
      method.getByRole("option", { name: "Merge commit", exact: true }),
    ).toHaveCount(0);

    await method.selectOption("squash");
    await expect(merge).toBeEnabled();
    await merge.click();

    await expect(page.getByText(`Merged ${DEMO}#${mine.number}`)).toBeVisible();
    expect(
      (await readPullRequest(page, "fakeorg", "demo", mine.number))
        .merge_method,
    ).toBe("squash");
  });
});
