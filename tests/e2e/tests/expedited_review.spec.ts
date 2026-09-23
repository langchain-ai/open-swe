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
//   the agent implements it, opens a draft PR as that user, starts a durable CI
//   watch, and posts the card right away. The draft's card offers only "Mark
//   ready for review", to the PR's author ->
//   the author marks it ready; the card switches to Approve / Reject ->
//   GitHub reports a FAILING check -> the watch wakes the agent, which pushes a
//   test-only fix the card never drew ->
//   the other person approves: the card collapses to who approved, and the agent
//   is woken, tries to merge, and is told the new head's checks are failing ->
//   GitHub reports the check GREEN -> the watch wakes the agent, which merges:
//   the approval becomes a GitHub APPROVE review on the new head, the PR gets a
//   comment linking the card, and the card becomes "Expedited review: merged".

const PEOPLE = [
  { login: "alice", slack_id: "U_ALICE" },
  { login: "bob", slack_id: "U_BOB" },
];
const PR_NUMBER = 1;
const REPO = { owner: "fakeorg", repo: "demo" };

type Approval = {
  id: string;
  state: string;
  detail: string;
  head_sha: string;
  pr_number: number;
  awaiting_ready: boolean;
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
  author: string;
  head_sha: string;
  reviews: Array<{ author: string; state: string; commit_id: string }>;
  issue_comments: Array<{ body: string }>;
};

/** POST a control endpoint, failing with the server's own words if it refuses. */
async function control(
  request: APIRequestContext,
  path: string,
  data: unknown,
): Promise<void> {
  const res = await request.post(path, { data });
  if (!res.ok()) {
    throw new Error(`POST ${path} → ${res.status()}: ${await res.text()}`);
  }
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

async function latest(request: APIRequestContext): Promise<Approval> {
  const found = (await approvals(request)).at(-1);
  expect(found, "the agent should have posted a card").toBeTruthy();
  return found!;
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

function card(page: Page) {
  return page
    .locator(".msg.bot")
    .filter({ hasText: /Expedited review/i })
    .last();
}

async function shootCard(page: Page, name: string) {
  await expect(card(page)).toBeVisible({ timeout: 30_000 });
  await card(page).screenshot({
    path: `test-results/expedited-review-${name}.png`,
  });
}

async function clickAs(page: Page, slackUserId: string, button: string) {
  await page.goto("/mock/slack");
  await page.locator("#user").selectOption(slackUserId);
  await expect(card(page)).toBeVisible({ timeout: 30_000 });
  await card(page).getByRole("button", { name: button }).click();
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
  test("draft marked ready by its author, one other approval, merge on green", async ({
    page,
    request,
  }) => {
    test.setTimeout(300_000);

    // 0. An admin turns the experimental feature on, and both people have write
    //    access on the repository.
    await request.post("/control/reset");
    await control(request, "/control/team-settings", {
      expedited_review_enabled: true,
    });
    for (const person of PEOPLE) {
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

    // 2. The agent opens a draft PR as the requester, starts watching its
    //    checks, and posts the card in the same turn. Nobody undrafts it for
    //    them: the card waits for the author.
    await expect
      .poll(async () => await botMessages(request, threadId), {
        timeout: 120_000,
      })
      .toMatch(/watching its checks/i);
    const opened = await pull(request);
    expect(opened.state).toBe("open");
    expect(opened.draft).toBe(true);
    const author = PEOPLE.find((person) => person.login === opened.author);
    expect(
      author,
      `the PR author ${opened.author} should be a test user`,
    ).toBeTruthy();
    const reviewer = PEOPLE.find((person) => person !== author)!;

    const posted = await latest(request);
    expect(posted.state).toBe("open");
    expect(posted.awaiting_ready).toBe(true);
    expect(posted.head_sha).toBe(opened.head_sha);

    // The card carries the whole diff as a rendered PNG. The text fallback only
    // appears when rendering or upload failed, so asserting the image keeps this
    // test on the real path.
    await page.goto("/mock/slack");
    await expect(card(page)).toContainText(/Draft\./);
    await expect(
      card(page).getByRole("button", { name: "Approve" }),
    ).toHaveCount(0);
    const diff = card(page).locator("img.block-image");
    await expect(diff).toBeVisible();
    await expect(diff).toHaveAttribute("alt", /greet\.py/);
    expect(
      await diff.evaluate((img: HTMLImageElement) => img.naturalWidth),
      "the diff PNG should have rendered, uploaded and decoded",
    ).toBeGreaterThan(0);
    await shootCard(page, "draft");

    // 3. Only the author can mark it ready. Their click undrafts the PR and
    //    opens the card for approval; it is not an approval.
    await clickAs(page, author!.slack_id, "Mark ready for review");
    await expect
      .poll(async () => (await latest(request)).awaiting_ready, {
        timeout: 60_000,
      })
      .toBe(false);
    expect((await pull(request)).draft).toBe(false);
    expect((await latest(request)).approvers).toEqual([]);
    await page.goto("/mock/slack");
    await expect(
      card(page).getByRole("button", { name: "Approve" }),
    ).toBeVisible();

    // 4. GitHub reports the check FAILED. The watch wakes the agent, which
    //    pushes a test-only fix the card never drew, so the card stays open.
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
    expect((await latest(request)).state).toBe("open");

    // 5. The other person approves. That one approval completes the card: the
    //    diff and buttons go, and the agent is woken, tries to merge, and is
    //    told the new head's checks are not green. Nothing reaches GitHub.
    await expect
      .poll(async () => (await threadRuns(request, threadId)).idle, {
        timeout: 60_000,
      })
      .toBe(true);
    const runsBeforeApproval = (await threadRuns(request, threadId)).runs;
    await clickAs(page, reviewer.slack_id, "Approve");
    await expect
      .poll(async () => (await latest(request)).approvers, { timeout: 60_000 })
      .toEqual([reviewer.login]);
    await page.goto("/mock/slack");
    // A voter who clicked from Slack is mentioned by their Slack identity.
    await expect(card(page)).toContainText(
      new RegExp(`Approved by (<@${reviewer.slack_id}>|@${reviewer.slack_id})`),
    );
    await expect(card(page).getByRole("button")).toHaveCount(0);
    await expect(card(page).locator("img.block-image")).toHaveCount(0);
    await shootCard(page, "approved");
    await expect
      .poll(
        async () => {
          const { runs, idle } = await threadRuns(request, threadId);
          return runs > runsBeforeApproval && idle;
        },
        { timeout: 90_000 },
      )
      .toBe(true);
    expect((await pull(request)).merged).toBe(false);
    expect(approvedReviews(await pull(request))).toHaveLength(0);
    expect((await latest(request)).state).toBe("open");

    // 6. GitHub reports the new head GREEN. That webhook wakes the agent at
    //    once, which merges on the recorded approval.
    await reportCheck(request, fixed.head_sha, "success");
    await expect
      .poll(async () => (await pull(request)).merged, { timeout: 120_000 })
      .toBe(true);

    const merged = await pull(request);
    expect(merged.state).toBe("closed");
    const reviews = approvedReviews(merged);
    expect(reviews.map((r) => r.author)).toEqual([reviewer.login]);
    expect(reviews[0]!.commit_id).toBe(fixed.head_sha);

    // The PR links back to the Slack card that approved it, once.
    const links = merged.issue_comments.filter((c) =>
      c.body.includes("expedited review"),
    );
    expect(links).toHaveLength(1);
    expect(links[0]!.body).toContain(`@${reviewer.login}`);
    expect(links[0]!.body).toContain("/mock/slack");

    const final = await latest(request);
    expect(final.state).toBe("merged");
    expect(final.votes.map((v) => v.decision)).toEqual(["approve"]);
    expect(final.votes[0]!.github_review_id).not.toBeNull();
    expect(final.votes[0]!.github_review_sha).toBe(fixed.head_sha);

    // 7. The card is reduced to the outcome and the PR.
    await page.goto("/mock/slack");
    await expect(card(page)).toContainText("Expedited review: merged");
    await expect(card(page)).not.toContainText("Approved by");
    await shootCard(page, "merged");
  });
});
