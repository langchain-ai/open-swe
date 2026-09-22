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
//   the agent implements it, opens a ready PR, starts a durable CI watch, and
//   nominates the PR for expedited review ->
//   NO approval card yet: checks have not reported ->
//   GitHub asynchronously reports a FAILING check -> the watch wakes the agent,
//   which fixes the code and pushes ->
//   GitHub reports the check GREEN with no unresolved review threads -> only now
//   does the card appear in the Slack thread ->
//   two different people with write access click Approve; each non-author click
//   becomes a real GitHub APPROVE review ->
//   the second approval merges the PR, pinned to the reviewed head SHA.
//
// Both GitHub deliveries are signed webhooks to the real /webhooks/github
// route, so the asynchronous half is genuinely asynchronous rather than a
// direct call into the watch.

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

test.describe("Expedited Slack review", () => {
  test("broken check, agent fix, two approvals, auto-merge", async ({
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

    // 2. The agent implements it, opens the PR, and starts watching its checks.
    await expect
      .poll(async () => (await botMessages(request, threadId)).length, {
        timeout: 120_000,
      })
      .toBeGreaterThan(0);
    await expect
      .poll(async () => await botMessages(request, threadId), {
        timeout: 120_000,
      })
      .toMatch(/watching its checks/i);

    const opened = await pull(request);
    expect(opened.state).toBe("open");
    expect(opened.merged).toBe(false);
    // Open SWE opens drafts when the requester's profile says so. Nobody clears
    // the flag here: nominating the PR is what marks it ready, asserted below.
    expect(opened.draft).toBe(true);

    // 3. GitHub reports the check FAILED, asynchronously. The durable watch
    //    wakes the agent, which fixes the code and pushes.
    await reportCheck(request, opened.head_sha, "failure");
    await expect
      .poll(async () => await botMessages(request, threadId), {
        timeout: 150_000,
      })
      .toMatch(/Fixed the failing check/i);

    // 4. In that same turn the agent asked for the expedited review, while the
    //    check was STILL failing. The request is accepted but parks in
    //    `waiting` with no card: the readiness gate, asserted directly.
    await expect
      .poll(async () => (await approvals(request)).at(-1)?.state, {
        timeout: 150_000,
      })
      .toBe("waiting");
    expect(await botMessages(request, threadId)).not.toMatch(
      /Expedited review requested/i,
    );
    // Nominating a draft marks it ready for review; nothing else in this test
    // touches the flag.
    expect((await pull(request)).draft).toBe(false);

    // 5. GitHub reports the check GREEN. That webhook is what moves the waiting
    //    approval to open and posts the card — no agent turn involved.
    await reportCheck(request, opened.head_sha, "success");
    await expect
      .poll(async () => (await approvals(request)).at(-1)?.state, {
        timeout: 120_000,
      })
      .toBe("open");
    await expect
      .poll(async () => await botMessages(request, threadId), {
        timeout: 60_000,
      })
      .toMatch(/Expedited review requested/i);
    expect((await approvals(request)).at(-1)?.head_sha).toBe(opened.head_sha);

    // The card carries the whole diff, which is the premise of voting from
    // Slack rather than from GitHub.
    await page.goto("/mock/slack");
    const card = page
      .locator(".msg.bot")
      .filter({ hasText: /Expedited review requested/i })
      .last();
    await expect(card).toContainText("greet.py");
    await expect(card).toContainText("def greet(name):");
    await expect(card).toContainText(/2 distinct reviewers with write access/i);
    await shootCard(page, "open", /Expedited review requested/i);

    // 6. Alice approves. One vote is not a quorum, so nothing merges — but her
    //    click has already become a real GitHub review.
    await clickApprove(page, ALICE.slack_id);
    await expect
      .poll(async () => (await approvals(request)).at(-1)?.approvers ?? [], {
        timeout: 60_000,
      })
      .toEqual(["alice"]);
    expect((await pull(request)).merged).toBe(false);
    const afterAlice = await pull(request);
    expect(
      afterAlice.reviews.filter((r) => r.state === "APPROVED"),
    ).toHaveLength(1);
    expect(afterAlice.reviews[0]?.author).toBe("alice");
    expect(afterAlice.reviews[0]?.commit_id).toBe(afterAlice.head_sha);

    // 7. Bob approves. Two distinct people is the quorum, so Open SWE merges
    //    the pull request, conditional on the SHA that was reviewed.
    await clickApprove(page, BOB.slack_id);
    await expect
      .poll(async () => (await pull(request)).merged, { timeout: 90_000 })
      .toBe(true);

    const merged = await pull(request);
    expect(merged.state).toBe("closed");
    expect(
      merged.reviews.filter((r) => r.state === "APPROVED").map((r) => r.author),
    ).toEqual(["alice", "bob"]);

    const final = (await approvals(request)).at(-1)!;
    expect(final.state).toBe("merged");
    expect(final.approvers.toSorted()).toEqual(["alice", "bob"]);
    // Every vote is attributed to a person and carries the review it produced.
    expect(final.votes.map((v) => v.decision)).toEqual(["approve", "approve"]);
    for (const vote of final.votes) {
      expect(vote.github_review_id).not.toBeNull();
    }

    // 8. The card closes out as merged, and the thread says so.
    await page.goto("/mock/slack");
    await expect(
      page.locator(".msg.bot").filter({ hasText: /Merged\./i }),
    ).toBeVisible({ timeout: 30_000 });
    await shootCard(page, "merged", /Merged\./i);
  });
});
