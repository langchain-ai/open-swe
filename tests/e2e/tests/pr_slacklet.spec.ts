import {
  test,
  expect,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

import { SAME_USER, loginAs } from "./helpers/dashboard";

// A pull request slacklet, end to end, with only the LLM and the external SaaS
// boundaries faked:
//
//   someone asks Open SWE in mock Slack to follow a draft PR ->
//   the agent calls the real `start_slacklet` for a `pull_request`, which posts
//   one message and logs command #1 `attach` ->
//   GitHub changes and says so through signed webhooks; each delivery makes the
//   backend re-read the PR and append only the commands that changed it, while
//   the slacklet message is edited in place and the log stays out of Slack ->
//   the web UI shows the same blocks and the command log ->
//   a follow-up in the thread wakes the agent, which answers from the commands
//   queued for it, not from the edited message.

const REPO = { owner: "fakeorg", repo: "demo" };
const REPOSITORY = {
  name: REPO.repo,
  full_name: `${REPO.owner}/${REPO.repo}`,
  owner: { login: REPO.owner },
  private: false,
};

async function control(
  request: APIRequestContext,
  path: string,
  data: unknown,
): Promise<unknown> {
  const res = await request.post(path, { data });
  if (!res.ok()) {
    throw new Error(`POST ${path} → ${res.status()}: ${await res.text()}`);
  }
  return res.json();
}

/** GitHub changes the pull request, then tells Open SWE with a signed webhook. */
async function github(
  request: APIRequestContext,
  number: number,
  change: Record<string, unknown>,
  event: string,
  payload: Record<string, unknown>,
): Promise<void> {
  await control(request, "/control/pull-request-health", { number, ...change });
  await control(request, "/control/github-event", {
    event,
    payload: { ...payload, repository: REPOSITORY, installation: { id: 42 } },
  });
}

function slacklet(page: Page) {
  return page.locator(".msg.bot").filter({ hasText: "Pull request slacklet" });
}

test.describe("Pull request slacklet", () => {
  test("stays in step with GitHub through an append-only command log", async ({
    page,
    request,
  }) => {
    test.setTimeout(240_000);
    await request.post("/control/reset");

    const seeded = (await control(request, "/control/pull-request", {
      repo: `${REPO.owner}/${REPO.repo}`,
      title: "Add a greeting helper",
      head: "greeting-helper",
      draft: true,
      author: "alice",
      check_runs: [
        { id: 1, name: "unit tests", status: "in_progress", conclusion: null },
      ],
    })) as { number: number; head_sha: string };
    const number = seeded.number;
    const prUrl = `https://github.com/${REPO.owner}/${REPO.repo}/pull/${number}`;
    const pullRequest = { number };

    // 1. Someone asks Open SWE to follow the PR; the agent attaches a slacklet.
    await page.goto("/mock/slack");
    await page
      .locator("#text")
      .fill(`<@U0BOT> keep this thread posted on ${prUrl} E2E_SLACKLET`);
    const sent = page.waitForResponse(
      (response) =>
        response.url().endsWith("/mock/slack/send") &&
        response.request().method() === "POST",
    );
    await page.locator("#send").click();
    const { thread_id: threadId } = (await (await sent).json()) as {
      thread_id: string;
    };
    expect(threadId).toBeTruthy();
    const asked = (
      (await (await request.get("/mock/slack/messages")).json()) as Array<{
        text: string;
        ts: string;
      }>
    ).find((message) => message.text.includes("E2E_SLACKLET"));
    const threadTs = asked?.ts ?? "";
    expect(threadTs).toBeTruthy();

    await expect(slacklet(page)).toBeVisible({ timeout: 120_000 });
    await expect(slacklet(page)).toContainText("Add a greeting helper");
    await expect(slacklet(page)).toContainText("Draft");
    await expect(slacklet(page)).toContainText("CI running");
    await expect(slacklet(page)).toContainText("state after #1 attach");

    // 2. The author marks it ready for review.
    await github(request, number, { draft: false }, "pull_request", {
      action: "ready_for_review",
      pull_request: pullRequest,
    });
    await expect(slacklet(page)).toContainText("state after #2 set_status");
    await expect(slacklet(page)).toContainText("Open");

    // 3. CI finishes green on the first head.
    await github(
      request,
      number,
      {
        check_runs: [
          {
            id: 1,
            name: "unit tests",
            status: "completed",
            conclusion: "success",
          },
        ],
      },
      "check_run",
      {
        action: "completed",
        check_run: {
          id: 1,
          name: "unit tests",
          status: "completed",
          conclusion: "success",
          head_sha: seeded.head_sha,
        },
      },
    );
    await expect(slacklet(page)).toContainText("state after #3 set_ci");
    await expect(slacklet(page)).toContainText("CI passing");

    // 4. Bob asks for changes.
    await github(
      request,
      number,
      {
        reviews: [
          {
            id: 501,
            user: { login: "bob" },
            state: "CHANGES_REQUESTED",
            commit_id: seeded.head_sha,
            body: "Please handle empty names.",
          },
        ],
      },
      "pull_request_review",
      {
        action: "submitted",
        review: {
          id: 501,
          state: "changes_requested",
          user: { id: 2002, login: "bob" },
        },
        pull_request: pullRequest,
      },
    );
    await expect(slacklet(page)).toContainText("state after #4 set_review");
    await expect(slacklet(page)).toContainText("Reviews: 🔁 bob");

    // 5. Alice pushes a fix: a new head, and CI starts over.
    const newHead = "f".repeat(8) + seeded.head_sha.slice(8);
    await github(
      request,
      number,
      {
        head_sha: newHead,
        check_runs: [
          {
            id: 2,
            name: "unit tests",
            status: "in_progress",
            conclusion: null,
          },
          { id: 3, name: "lint", status: "in_progress", conclusion: null },
        ],
      },
      "pull_request",
      { action: "synchronize", pull_request: pullRequest },
    );
    await expect(slacklet(page)).toContainText("state after #5 set_head");
    await expect(slacklet(page)).toContainText("CI running");

    // 6. The same delivery again, as GitHub retries: nothing changed, so no command.
    await github(request, number, {}, "pull_request", {
      action: "synchronize",
      pull_request: pullRequest,
    });

    // 7. Lint fails on the new head.
    await github(
      request,
      number,
      {
        check_runs: [
          {
            id: 2,
            name: "unit tests",
            status: "completed",
            conclusion: "success",
          },
          { id: 3, name: "lint", status: "completed", conclusion: "failure" },
        ],
      },
      "check_run",
      {
        action: "completed",
        check_run: {
          id: 3,
          name: "lint",
          status: "completed",
          conclusion: "failure",
          head_sha: newHead,
        },
      },
    );
    await expect(slacklet(page)).toContainText("state after #6 set_ci");
    await expect(slacklet(page)).toContainText("CI failing: lint");

    // The command log stays out of Slack: the thread holds the request and the one slacklet.
    const thread = (await (
      await request.get(
        `/mock/slack/messages?thread_ts=${encodeURIComponent(threadTs)}`,
      )
    ).json()) as Array<{ is_bot: boolean }>;
    expect(thread.filter((message) => message.is_bot)).toHaveLength(1);
    await expect(slacklet(page)).toHaveCount(1);

    // 8. The web UI shows the same message and the same command log.
    await loginAs(page, SAME_USER);
    await page.goto(`/agents/${threadId}`);
    const card = page.getByTestId("slacklet-card");
    await expect(card).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("slacklet-message")).toContainText(
      "Add a greeting helper",
    );
    await expect(page.getByTestId("slacklet-message")).toContainText(
      "CI failing: lint",
    );
    await expect(
      page.getByTestId("slacklet-commands").locator("li"),
    ).toHaveText([
      /#1 attach/,
      /#2 set_status/,
      /#3 set_ci/,
      /#4 set_review/,
      /#5 set_head/,
      /#6 set_ci/,
    ]);

    // 9. A follow-up wakes the agent, which answers from the commands queued for it.
    await control(request, "/mock/slack/send", {
      text: "<@U0BOT> what changed on the PR? E2E_SLACKLET_STATUS",
      mention_bot: true,
      thread_ts: threadTs,
    });
    await page.goto("/mock/slack");
    await expect(
      page.locator(".msg.bot").filter({ hasText: "The slacklet sent me" }),
    ).toContainText(
      "5 commands since I attached it: #2 set_status, #3 set_ci, #4 set_review, #5 set_head, #6 set_ci. It now says open, CI failing on fffffff.",
      { timeout: 120_000 },
    );
  });
});
