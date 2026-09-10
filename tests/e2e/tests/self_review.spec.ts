import { test, expect, type Page } from "@playwright/test";

// The agent reviews the PR it just opened, inside the same run: the findings
// land on its own thread (Slack reply + the dashboard's Review panel) and never
// on the pull request. Only the LLM and the SaaS boundaries are faked.
const SAME_USER = { login: "alice", email: "alice@example.com" };

async function loginAs(page: Page, user: { login: string; email: string }) {
  const res = await page.request.post("/control/login", { data: user });
  expect(res.ok()).toBeTruthy();
}

async function runSelfReviewFlow(page: Page): Promise<string> {
  const send = await page.request.post("/mock/slack/send", {
    data: {
      text: "E2E_SELF_REVIEW please add greet() and farewell() helpers and open a PR",
    },
  });
  expect(send.ok()).toBeTruthy();
  const { thread_id: threadId } = await send.json();
  expect(threadId).toBeTruthy();
  return threadId;
}

test.describe("inline self-review of an Open SWE PR", () => {
  test.beforeEach(async ({ page }) => {
    // Auto-review on for this repo, so "no review on the PR" is the stand-down
    // and not the opt-in gate answering first.
    await page.request.post("/control/review-repo-enabled", {
      data: { enabled: true },
    });
    await page.request.post("/control/forget-review-state", {
      data: { pr_numbers: [1, 2] },
    });
    await page.goto("/mock/slack");
    await page.locator("#reset").click();
    await expect(page.locator("#thread")).toContainText("No messages yet");
  });

  test.afterAll(async ({ request }) => {
    // The reviewer clone shares the sandbox root; leave it as the other specs
    // expect to find it (exactly one checkout, named `repo`).
    await request.post("/control/prepare-sandbox-repo");
  });

  test("findings reach the thread, not the pull request", async ({ page }) => {
    const threadId = await runSelfReviewFlow(page);

    // The Slack reply carries the PR link and what the self-review decided.
    const reply = page
      .locator(".msg.bot")
      .filter({ hasText: "reviewed it myself" });
    await expect(reply).toBeVisible();
    await expect(reply).toContainText("farewell() returns a greeting");
    await expect(reply).toContainText("fixed in this PR");
    await expect(reply).toContainText("needs your call");
    const prLink = reply.locator('a[href*="/pull/"]');
    await expect(prLink).toBeVisible();

    // Nothing was published to the PR: no review, no inline comments.
    await prLink.click();
    await expect(page.locator("#pr-title")).toContainText("Add greet() helper");
    await expect(page.locator("#review-count")).toHaveText("0");
    await expect(page.locator("#review-comment-count")).toHaveText("0");

    const state = await (
      await page.request.get("/control/review-state?number=1")
    ).json();
    expect(state.reviews).toEqual([]);
    expect(state.review_comments).toEqual([]);

    // The findings are attached to the thread, with the dispositions the agent set.
    await loginAs(page, SAME_USER);
    const inline = await (
      await page.request.get(`/dashboard/api/inline-review/${threadId}`)
    ).json();
    expect(inline.reviews).toHaveLength(1);
    const [review] = inline.reviews;
    expect(review.prNumber).toBe(1);
    expect(review.status).toBe("complete");
    expect(
      review.findings.map((f: { title: string; disposition: string }) => [
        f.title,
        f.disposition,
      ]),
    ).toEqual([
      ["farewell() returns a greeting", "fixed"],
      ["Salutation is not localisable", "deferred"],
    ]);
  });

  test("the fix the review asked for is actually pushed", async ({ page }) => {
    await runSelfReviewFlow(page);
    await expect(
      page.locator(".msg.bot").filter({ hasText: "reviewed it myself" }),
    ).toBeVisible();

    // The obvious defect was fixed on the branch, not just annotated: the PR
    // diff the fake GitHub serves comes straight from the pushed branch.
    const diff = await (
      await page.request.get("/fake-gh/repos/fakeorg/demo/pulls/1", {
        headers: { accept: "application/vnd.github.diff" },
      })
    ).text();
    expect(diff).toContain('return f"Goodbye, {name}!"');
  });

  test("the dashboard shows the findings in the Review panel", async ({
    page,
  }) => {
    const threadId = await runSelfReviewFlow(page);
    await expect(
      page.locator(".msg.bot").filter({ hasText: "reviewed it myself" }),
    ).toBeVisible();

    await loginAs(page, SAME_USER);
    await page.goto(`/agents/${threadId}`);

    // The transcript points at the panel rather than dumping the findings.
    const card = page.getByTestId("self-review-card");
    await expect(card).toBeVisible();
    await expect(card).toContainText("Self-review of PR #1");
    await expect(card).toContainText("waiting on you");

    await card.click();
    await expect(
      page.getByRole("button", { name: "Review", exact: true }),
    ).toBeVisible();

    const panel = page.getByTestId("self-review-panel");
    await expect(panel).toContainText("PR #1");
    await expect(
      panel.getByText("farewell() returns a greeting"),
    ).toBeVisible();
    await expect(
      panel.getByText("Fixed in this PR", { exact: true }),
    ).toBeVisible();
    await expect(
      panel.getByText("Needs your call", { exact: true }),
    ).toBeVisible();
    await expect(panel.getByText("greet.py:6")).toBeVisible();
  });
});
