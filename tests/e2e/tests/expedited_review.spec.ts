import {
  test,
  expect,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

// The whole expedited-review path, end to end, with only the LLM and the
// external SaaS boundaries faked:
//
//   a user asks for a one-line change in mock Slack ->
//   the agent implements it, opens the PR, starts a durable CI watch, and posts
//   the approval card right away, before any check has reported ->
//   Alice approves: the vote is recorded, nothing reaches GitHub ->
//   GitHub reports a FAILING check -> the watch wakes the agent, which pushes a
//   test-only fix the card never drew, so Alice's vote still counts ->
//   Bob approves: the card has two approvals and wakes the agent, which tries to
//   merge and is told checks are still running ->
//   GitHub reports the check GREEN -> the watch wakes the agent, which merges:
//   each non-author approval becomes a GitHub APPROVE review on the new head,
//   and the merge is pinned to that head.

const ALICE = { login: "alice", slack_id: "U_ALICE" };
const BOB = { login: "bob", slack_id: "U_BOB" };
const PR_NUMBER = 1;
const REPO = { owner: "fakeorg", repo: "demo" };

type Approval = {
  id: string;
  state: string;
  detail: string;
  head_sha: string;
  pr_number: number;
  approvers: Array<string>;
  votes: Array<{
    github_login: string;
    decision: string;
    github_review_id: number | null;
    github_review_sha: string;
  }>;
};

type PullRequest = {
  number: number;
  state: string;
  draft: boolean;
  merged: boolean;
  head_sha: string;
  reviews: Array<{ author: string; state: string; commit_id: string }>;
};

/** POST a control endpoint, failing with the server's own words if it refuses. */
async function control(
  request: APIRequestContext,
  path: string,
  data: unknown,
): Promise<unknown> {
  const res = await request.post(path, { data });
  if (!res.ok()) {
    throw new Error(`POST ${path} → ${res.status()}: ${await res.text()}`);
  }
  return await res.json();
}

async function approvals(request: APIRequestContext): Promise<Array<Approval>> {
  const res = await request.get("/control/expedited-approvals");
  if (!res.ok()) {
    throw new Error(
      `GET /control/expedited-approvals → ${res.status()}: ${await res.text()}`,
    );
  }
  return (await res.json()) as Array<Approval>;
}

async function pull(request: APIRequestContext): Promise<PullRequest> {
  const res = await request.get("/mock/github/data");
  const prs = (await res.json()) as Array<PullRequest>;
  const found = prs.find((item) => item.number === PR_NUMBER);
  expect(found, "the agent should have opened a pull request").toBeTruthy();
  return found!;
}

async function botMessages(
  request: APIRequestContext,
  threadId: string,
): Promise<string> {
  const res = await request.get("/mock/slack/messages");
  const msgs = (await res.json()) as Array<{ text: string; is_bot: boolean }>;
  return msgs
    .filter((m) => m.is_bot && m.text.includes(threadId))
    .map((m) => m.text)
    .join("\n");
}

/** Report the head check as `conclusion`, then let GitHub say so out of band. */
async function reportCheck(
  request: APIRequestContext,
  headSha: string,
  conclusion: "failure" | "success",
) {
  await control(request, "/control/pull-request-health", {
    number: PR_NUMBER,
    check_runs: [
      {
        id: 9001,
        name: "unit tests",
        status: "completed",
        conclusion,
        head_sha: headSha,
        details_url: "https://ci.example.com/runs/9001",
      },
    ],
  });

  await control(request, "/control/github-event", {
    event: "check_run",
    payload: {
      action: "completed",
      repository: {
        name: REPO.repo,
        full_name: `${REPO.owner}/${REPO.repo}`,
        owner: { login: REPO.owner },
        private: false,
      },
      installation: { id: 42 },
      check_run: {
        id: 9001,
        name: "unit tests",
        status: "completed",
        conclusion,
        head_sha: headSha,
        details_url: "https://ci.example.com/runs/9001",
        check_suite: { head_branch: "add-greet" },
      },
    },
  });
}

async function shootCard(page: Page, name: string, matcher: RegExp) {
  const card = page.locator(".msg.bot").filter({ hasText: matcher }).last();
  await expect(card).toBeVisible({ timeout: 30_000 });
  await card.screenshot({ path: `test-results/expedited-review-${name}.png` });
}

async function clickApprove(page: Page, slackUserId: string) {
  await page.goto("/mock/slack");
  await page.locator("#user").selectOption(slackUserId);
  const card = page
    .locator(".msg.bot")
    .filter({ hasText: /Expedited review requested/i });
  await expect(card).toBeVisible({ timeout: 30_000 });
  await card.getByRole("button", { name: "Approve" }).click();
}

async function threadRuns(
  request: APIRequestContext,
  threadId: string,
): Promise<{ runs: number; idle: boolean }> {
  const res = await request.get(
    `/control/thread-idle?thread_id=${encodeURIComponent(threadId)}`,
  );
  return (await res.json()) as { runs: number; idle: boolean };
}

function approvedReviews(pr: PullRequest) {
  return pr.reviews.filter((r) => r.state === "APPROVED");
}

test.describe("Expedited Slack review", () => {
  test("card first, votes recorded, test-only fix, merge on green", async ({
    page,
    request,
  }) => {
    test.setTimeout(300_000);

    // 0. An admin turns the experimental feature on, and both reviewers have
    //    write access on the repository.
    await request.post("/control/reset");
    await control(request, "/control/team-settings", {
      expedited_review_enabled: true,
    });
    for (const person of [ALICE, BOB]) {
      await control(request, "/control/collaborator-permission", {
        login: person.login,
        permission: "write",
      });
    }

    // 1. The user asks for the change in Slack.
    const send = await request.post("/mock/slack/send", {
      data: {
        text: "<@U0BOT> fix the greeting punctuation and get it merged E2E_EXPEDITE",
        mention_bot: true,
      },
    });
    const { thread_id: threadId } = (await send.json()) as {
      thread_id: string;
    };
    expect(threadId).toBeTruthy();

    // 2. The agent opens the PR, starts watching its checks, and posts the
    //    card in the same turn, before any check has reported.
    await expect
      .poll(async () => await botMessages(request, threadId), {
        timeout: 120_000,
      })
      .toMatch(/watching its checks/i);
    const opened = await pull(request);
    expect(opened.state).toBe("open");
    expect(opened.merged).toBe(false);
    // Posting the card is what marks the draft ready for review.
    expect(opened.draft).toBe(false);

    const posted = (await approvals(request)).at(-1);
    expect(posted?.state).toBe("open");
    expect(posted?.head_sha).toBe(opened.head_sha);
    expect(await botMessages(request, threadId)).toMatch(
      /Expedited review requested/i,
    );

    // The card carries the whole diff, which is the premise of voting from
    // Slack rather than from GitHub. Production renders it to a PNG and shows
    // that; the text fallback only appears when rendering or upload failed, so
    // asserting the image is what keeps this test on the real path.
    await page.goto("/mock/slack");
    const card = page
      .locator(".msg.bot")
      .filter({ hasText: /Expedited review requested/i })
      .last();
    const diff = card.locator("img.block-image");
    await expect(diff).toBeVisible();
    await expect(diff).toHaveAttribute("alt", /greet\.py/);
    expect(
      await diff.evaluate((img: HTMLImageElement) => img.naturalWidth),
      "the diff PNG should have rendered, uploaded and decoded",
    ).toBeGreaterThan(0);
    await shootCard(page, "open", /Expedited review requested/i);

    // 3. Alice approves. The vote is recorded and nothing reaches GitHub.
    await clickApprove(page, ALICE.slack_id);
    await expect
      .poll(async () => (await approvals(request)).at(-1)?.approvers ?? [], {
        timeout: 60_000,
      })
      .toEqual(["alice"]);
    expect(approvedReviews(await pull(request))).toHaveLength(0);

    // 4. GitHub reports the check FAILED. The watch wakes the agent, which
    //    pushes a test-only fix. The card never drew that file, so the card
    //    stays open and Alice's vote still stands.
    await reportCheck(request, opened.head_sha, "failure");
    await expect
      .poll(async () => (await pull(request)).head_sha, { timeout: 150_000 })
      .not.toBe(opened.head_sha);
    const fixed = await pull(request);
    await expect
      .poll(async () => await botMessages(request, threadId), {
        timeout: 60_000,
      })
      .toMatch(/Fixed the failing check/i);
    expect((await approvals(request)).at(-1)?.state).toBe("open");

    // 5. Bob approves. Two approvals wake the agent, which tries to merge and
    //    is told the new head's checks have not reported: still no GitHub write.
    await expect
      .poll(async () => (await threadRuns(request, threadId)).idle, {
        timeout: 60_000,
      })
      .toBe(true);
    const runsBeforeQuorum = (await threadRuns(request, threadId)).runs;
    await clickApprove(page, BOB.slack_id);
    await expect
      .poll(async () => (await approvals(request)).at(-1)?.approvers ?? [], {
        timeout: 60_000,
      })
      .toEqual(["alice", "bob"]);
    await expect
      .poll(
        async () => {
          const { runs, idle } = await threadRuns(request, threadId);
          return runs > runsBeforeQuorum && idle;
        },
        { timeout: 90_000 },
      )
      .toBe(true);
    expect((await pull(request)).merged).toBe(false);
    expect(approvedReviews(await pull(request))).toHaveLength(0);
    expect((await approvals(request)).at(-1)?.state).toBe("open");

    // 6. GitHub reports the new head GREEN. The watch's tick wakes the agent,
    //    which merges on the recorded approvals.
    await reportCheck(request, fixed.head_sha, "success");
    expect(
      await control(request, "/control/baby-sit-tick", { number: PR_NUMBER }),
    ).toEqual({ status: "stopped" });
    await expect
      .poll(async () => (await pull(request)).merged, { timeout: 120_000 })
      .toBe(true);

    const merged = await pull(request);
    expect(merged.state).toBe("closed");
    const reviews = approvedReviews(merged);
    expect(reviews.map((r) => r.author).toSorted()).toEqual(["alice", "bob"]);
    for (const review of reviews) {
      expect(review.commit_id).toBe(fixed.head_sha);
    }

    const final = (await approvals(request)).at(-1)!;
    expect(final.state).toBe("merged");
    // Every vote is attributed to a person and carries the review it produced.
    expect(final.votes.map((v) => v.decision)).toEqual(["approve", "approve"]);
    for (const vote of final.votes) {
      expect(vote.github_review_id).not.toBeNull();
      expect(vote.github_review_sha).toBe(fixed.head_sha);
    }

    // 7. The card closes out as merged.
    await page.goto("/mock/slack");
    await expect(
      page.locator(".msg.bot").filter({ hasText: /Merged\./i }),
    ).toBeVisible({ timeout: 30_000 });
    await shootCard(page, "merged", /Merged\./i);
  });
});
