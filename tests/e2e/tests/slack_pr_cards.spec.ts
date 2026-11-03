import { test, expect, type Page } from "@playwright/test";

const REPO = { owner: "fakeorg", repo: "demo" };

async function control(page: Page, path: string, data: object) {
  const response = await page.request.post(path, { data });
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}

async function shoot(page: Page, name: string) {
  await page
    .locator(".pr-container")
    .screenshot({ path: test.info().outputPath(name) });
}

test.describe("Slack PR cards", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/mock/slack");
    await page.locator("#reset").click();
    await expect(page.locator("#thread")).toContainText("No messages yet");
  });

  test("a posted card follows its pull request until it stops updating", async ({
    page,
  }) => {
    const { number } = await control(page, "/control/pull-request", {
      repo: `${REPO.owner}/${REPO.repo}`,
      title: "Add retry backoff to the webhook forwarder",
      head: "retry-backoff",
      files: {
        "forwarder.py": "def forward():\n    return retry(backoff=2)\n",
      },
      check_runs: [
        { name: "lint", status: "completed", conclusion: "failure" },
        { name: "unit-tests", status: "in_progress", conclusion: null },
      ],
      review_threads: [
        {
          is_resolved: false,
          path: "forwarder.py",
          line: 2,
          author: "bob",
          body: "Should the backoff be capped?",
        },
      ],
    });

    await page
      .locator("#text")
      .fill(`<@U0BOT> E2E_PR_CARD:${number} where does this stand?`);
    await page.locator("#send").click();

    const card = page.locator(".pr-container");
    await expect(card).toBeVisible({ timeout: 60_000 });
    await expect(card).toContainText(
      "Add retry backoff to the webhook forwarder",
    );
    await expect(card).toContainText(
      new RegExp(`Open · (fakeorg/)?demo#${number}\\b`),
    );
    await expect(card).toContainText("lint failing");
    await expect(card).toContainText("1 check running");
    await expect(card).toContainText("1 unresolved comment");
    await expect(card).toContainText("Live until");
    await expect(card).not.toContainText("Ready to merge");
    await expect(card.getByRole("button", { name: "GitHub" })).toHaveAttribute(
      "href",
      `https://github.com/${REPO.owner}/${REPO.repo}/pull/${number}`,
    );
    await shoot(page, "pr-card-live.png");

    await control(page, "/control/pull-request-health", {
      number,
      check_runs: [
        { name: "lint", status: "completed", conclusion: "success" },
        { name: "unit-tests", status: "completed", conclusion: "success" },
      ],
      review_threads: [],
      reviews: [{ author: "bob", state: "APPROVED" }],
      review_decision: "APPROVED",
    });
    await control(page, "/control/github-event", {
      event: "pull_request_review",
      payload: {
        action: "submitted",
        repository: {
          name: REPO.repo,
          full_name: `${REPO.owner}/${REPO.repo}`,
          owner: { login: REPO.owner },
          private: false,
        },
        installation: { id: 42 },
        sender: { login: "bob" },
        pull_request: { number, user: { login: "alice" } },
        review: { state: "approved", body: "LGTM", user: { login: "bob" } },
      },
    });
    await expect(card).toContainText("Approved by bob", { timeout: 30_000 });
    await expect(card).toContainText("Checks passing");
    await expect(card).toContainText("Ready to merge");
    await expect(card).not.toContainText("lint failing");
    await expect(card).not.toContainText("unresolved");
    await expect(page.locator(".pr-container")).toHaveCount(1);
    await shoot(page, "pr-card-updated.png");

    await control(page, "/control/expire-event-listeners", {});
    await expect(card).toContainText("No longer updating", {
      timeout: 30_000,
    });
    await expect(card).not.toContainText("Live until");
    await expect(card).toContainText("Approved by bob");
    await shoot(page, "pr-card-expired.png");
  });
});
