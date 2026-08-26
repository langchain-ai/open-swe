import {
  test,
  expect,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

// The webhook reviewer stands down for a PR Open SWE opened (that PR is reviewed
// inline by the authoring thread) and still reviews everyone else's. Both halves
// run against the same reviewer graph and the same fake GitHub, so the
// stand-down is an A/B, not an untested absence.

async function reviewState(request: APIRequestContext, number: number) {
  const res = await request.get(`/control/review-state?number=${number}`);
  expect(res.ok()).toBeTruthy();
  return res.json();
}

async function deliverPullRequestOpened(
  request: APIRequestContext,
  prNumber: number,
) {
  const res = await request.post("/control/github-webhook", {
    data: { event: "pull_request", action: "opened", pr_number: prNumber },
  });
  expect(res.ok()).toBeTruthy();
  const body = await res.json();
  expect(body.status).toBe(200);
  return body;
}

async function openSwePullRequest(page: Page): Promise<number> {
  await page.goto("/mock/slack");
  await page.locator("#reset").click();
  await expect(page.locator("#thread")).toContainText("No messages yet");
  const send = await page.request.post("/mock/slack/send", {
    data: {
      text: "E2E_SELF_REVIEW please add greet() and farewell() helpers and open a PR",
    },
  });
  expect(send.ok()).toBeTruthy();
  await expect(
    page.locator(".msg.bot").filter({ hasText: "reviewed it myself" }),
  ).toBeVisible();
  return 1;
}

test.describe("auto-review stand-down", () => {
  // Two agent runs plus a reviewer run, each with a real clone and checkout.
  test.setTimeout(240_000);

  test.beforeEach(async ({ request }) => {
    await request.post("/control/review-repo-enabled", {
      data: { enabled: true },
    });
    // PR numbers restart at 1 on reset, so the durable reviewer state for those
    // numbers has to go too or a rerun re-reviews an already-published finding.
    await request.post("/control/forget-review-state", {
      data: { pr_numbers: [1, 2] },
    });
  });

  test.afterAll(async ({ request }) => {
    await request.post("/control/prepare-sandbox-repo");
  });

  test("skips a PR the authoring thread reviewed, reviews a human's", async ({
    page,
  }) => {
    const selfReviewed = await openSwePullRequest(page);

    // 1. The PR Open SWE opened: the reviewer must not touch it.
    await deliverPullRequestOpened(page.request, selfReviewed);
    // Give a dispatched run time to appear; the assertion is that none does.
    await page.waitForTimeout(8_000);
    const claimed = await reviewState(page.request, selfReviewed);
    expect(claimed.reviews).toEqual([]);
    expect(claimed.review_comments).toEqual([]);
    expect(claimed.check_runs).toEqual([]);

    // 2. A PR nobody claimed, on the same branch and repo: reviewed as usual.
    const created = await page.request.post("/control/open-pull-request", {
      data: {
        head: "add-greet",
        base: "main",
        title: "Human PR touching greet.py",
        body: "Opened by a person.",
      },
    });
    expect(created.ok()).toBeTruthy();
    const humanPr = (await created.json()).number as number;
    expect(humanPr).toBe(2);

    await deliverPullRequestOpened(page.request, humanPr);

    await expect
      .poll(
        async () =>
          (await reviewState(page.request, humanPr)).review_comments.length,
        { timeout: 120_000, intervals: [2_000] },
      )
      .toBe(1);

    const reviewed = await reviewState(page.request, humanPr);
    expect(reviewed.reviews).toHaveLength(1);
    expect(reviewed.check_runs).toHaveLength(1);
    expect(reviewed.review_comments[0].path).toBe("greet.py");
    expect(reviewed.review_comments[0].body).toContain(
      "farewell() returns a greeting",
    );

    // And it is visible where a human would see it.
    await page.goto(`/mock/github/fakeorg/demo/pull/${humanPr}`);
    await expect(page.locator("#review-comment-count")).toHaveText("1");
    await expect(
      page.locator('#pr-review-comments li[data-file="greet.py"]'),
    ).toBeVisible();

    // The self-reviewed PR is still untouched after the reviewer ran on the other.
    await page.goto(`/mock/github/fakeorg/demo/pull/${selfReviewed}`);
    await expect(page.locator("#review-comment-count")).toHaveText("0");
  });
});
