import { test, expect } from "@playwright/test";
import {
  SAME_USER,
  expectTranscriptVisible,
  latestPrBody,
  loginAs,
  openMultiRepoPrThreadViaSlackLink,
  openThreadActionsMenu,
  openThreadViaSlackLink,
  setPullRequestHealth,
  threadIdFromUrl,
  waitForStateToContain,
} from "./helpers/dashboard";

test.describe("thread pull requests", () => {
  test("keeps pull requests from multiple repositories above the composer", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await openMultiRepoPrThreadViaSlackLink(page);

    const companionLink = page.getByRole("link", {
      name: "Open anotherorg/companion pull request #2",
    });
    await expect(async () => {
      await page.reload();
      await expect(companionLink).toBeVisible({ timeout: 8000 });
    }).toPass({ timeout: 60_000 });

    const strip = page.getByTestId("thread-pull-requests");
    await expect(strip).toBeVisible();
    await expect(
      page.getByRole("link", {
        name: "Open fakeorg/demo pull request #1",
      }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "Show 1 more" }),
    ).toBeVisible();

    await companionLink.hover();
    const hoverCard = page.getByTestId("pr-hover-card-anotherorg/companion-2");
    await expect(hoverCard).toBeVisible();
    await expect(hoverCard).toContainText("anotherorg/companion #2");
    await expect(hoverCard).toContainText("Add companion integration");
    await expect(hoverCard).toContainText("open-swe[bot]");
    await expect(hoverCard).toContainText("main");
    await expect(hoverCard).toContainText("add-integration");
    await expect(hoverCard).toContainText("1 file");

    await page.getByRole("button", { name: "Show 1 more" }).click();
    await expect(
      page.getByRole("link", {
        name: "Open fakeorg/demo pull request #1",
      }),
    ).toBeVisible();
    await expect(companionLink).toBeVisible();
  });

  test("shows live pull request health and submits actionable fixes", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await openThreadViaSlackLink(page);
    const threadId = threadIdFromUrl(page);
    await expectTranscriptVisible(page);

    const summary = page.getByTestId("pr-summary-fakeorg/demo-1");
    const fixButton = page.getByRole("button", { name: "Fix PR #1 issues" });
    await expect(summary).toHaveAttribute(
      "data-pr-tone",
      "text-muted-foreground",
    );
    await expect(summary).not.toHaveAttribute(
      "data-pr-tone",
      "text-success-foreground",
    );
    await expect(summary).toContainText("draft");
    await expect(fixButton).toHaveCount(0);

    await setPullRequestHealth(page, {
      draft: false,
      state: "open",
      merged: false,
      mergeable: true,
      mergeable_state: "clean",
      check_runs: [],
      statuses: [],
      review_threads: [],
    });
    await page.reload();
    await expect(summary).toHaveAttribute(
      "data-pr-tone",
      "text-success-foreground",
    );
    await expect(summary).toContainText("open");
    await expect(fixButton).toHaveCount(0);
    await page.waitForTimeout(1_000);

    await page.route("**/pull-request-status", (route) =>
      route.fulfill({ status: 502, json: { detail: "GitHub unavailable" } }),
    );
    const failedRefresh = page.waitForResponse(
      (response) =>
        response.url().endsWith("/pull-request-status") &&
        response.status() === 502,
    );
    await page.evaluate(() =>
      window.dispatchEvent(new Event("visibilitychange")),
    );
    await failedRefresh;
    await expect(summary).toHaveAttribute(
      "data-pr-tone",
      "text-muted-foreground",
      { timeout: 5_000 },
    );
    await summary.focus();
    await expect(
      page.getByTestId("pr-hover-card-fakeorg/demo-1"),
    ).toContainText("GitHub health is unavailable");
    await page.keyboard.press("Escape");
    await page.unroute("**/pull-request-status");

    await setPullRequestHealth(page, {
      mergeable: false,
      mergeable_state: "dirty",
      check_runs: [
        {
          name: "unit-tests",
          status: "completed",
          conclusion: "failure",
          details_url: "https://checks.example/unit-tests",
          required: true,
        },
        {
          name: "browser-e2e",
          status: "completed",
          conclusion: "timed_out",
          details_url: "https://checks.example/browser-e2e",
          required: false,
        },
        {
          name: "preview-deploy",
          status: "in_progress",
          conclusion: null,
        },
      ],
      statuses: [
        {
          context: "legacy/security-scan",
          state: "error",
          target_url: "https://checks.example/security",
        },
      ],
      reviews: [
        {
          author: "lead-reviewer",
          state: "CHANGES_REQUESTED",
          body: "The fallback must preserve the original exception.",
          url: "https://github.example/review/1",
        },
      ],
      review_decision: "CHANGES_REQUESTED",
      review_threads: [
        {
          path: "agent/dashboard/routes.py",
          line: 42,
          comments: [
            {
              author: "reviewer-one",
              body: "Handle the null response before reading the payload.",
              url: "https://github.example/discussion/1",
            },
            {
              author: "author-one",
              body: "I handled null but still need to retain the retry reason.",
              url: "https://github.example/discussion/1-reply",
            },
          ],
        },
        {
          author: "reviewer-two",
          body: "Add regression coverage for the retry path.",
          path: "tests/dashboard/test_routes.py",
          original_line: 88,
          url: "https://github.example/discussion/2",
        },
        {
          is_resolved: true,
          author: "reviewer-three",
          body: "This resolved comment must not be counted.",
          path: "README.md",
          line: 1,
        },
      ],
    });
    await page.reload();

    await expect(summary).toHaveAttribute("data-pr-tone", "text-destructive");
    await expect(summary).toContainText("3 checks");
    await expect(summary).toContainText("2 comments");
    await expect(summary).toContainText("Conflict");
    await expect(summary).toContainText("1 pending");
    await expect(fixButton).toBeVisible();

    await summary.focus();
    const hoverCard = page.getByTestId("pr-hover-card-fakeorg/demo-1");
    await expect(hoverCard).toBeVisible();
    const failingChecks = hoverCard.getByTestId("pr-failing-checks");
    await expect(failingChecks).toContainText("unit-tests");
    await expect(failingChecks).toContainText("browser-e2e");
    await expect(failingChecks).toContainText("legacy/security-scan");
    const unresolvedComments = hoverCard.getByTestId("pr-unresolved-comments");
    await expect(unresolvedComments).toContainText(
      "Handle the null response before reading the payload.",
    );
    await expect(unresolvedComments).toContainText(
      "Add regression coverage for the retry path.",
    );
    await expect(unresolvedComments).not.toContainText(
      "This resolved comment must not be counted.",
    );

    await page.keyboard.press("Escape");
    await fixButton.click();
    const fixPrompt = "Fresh GitHub scan:";
    await waitForStateToContain(page, threadId, fixPrompt);
    const state = await page.request.get(`/threads/${threadId}/state`);
    const stateText = JSON.stringify(await state.json());
    expect(stateText).toContain("[required] unit-tests: FAILURE");
    expect(stateText).toContain("[optional] browser-e2e: TIMED_OUT");
    expect(stateText).toContain(
      "The fallback must preserve the original exception.",
    );
    expect(stateText).toContain(
      "Handle the null response before reading the payload.",
    );
    expect(stateText).toContain(
      "I handled null but still need to retain the retry reason.",
    );
    expect(stateText).not.toContain(
      "This resolved comment must not be counted.",
    );
    await expect(page.getByText(new RegExp(fixPrompt)).first()).toBeVisible();
  });

  // Public and private need separate runs: the PR body is written at
  // open_pull_request time, so the repo's visibility has to be set before it.
  test("keeps the originating Slack thread out of public PR bodies", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await openThreadViaSlackLink(page);

    await openThreadActionsMenu(page);
    await expect(page.getByText("Open in Slack")).toBeVisible();
    await expect.poll(() => latestPrBody(page)).not.toContain("Slack thread");
  });

  test("exposes the originating Slack thread for private repos", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await openThreadViaSlackLink(page, { repoPrivate: true });

    await openThreadActionsMenu(page);
    const sourceItem = page.getByText("Open in Slack");
    await expect(sourceItem).toHaveAttribute("href", /^slack:\/\/channel\?/);
    await expect.poll(() => latestPrBody(page)).toContain("Slack thread");
  });
});
