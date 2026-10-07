import {
  test,
  expect,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import {
  SAME_ORIGIN_HEADERS,
  loginAs,
  seedOpenPullRequest,
} from "./helpers/dashboard";

// Human review requests in Slack, end to end, with only the LLM and the
// external SaaS boundaries faked. The agent's tool, the dashboard button, the
// card's buttons, the GitHub webhooks and the scheduler deadlines all run the
// real code; only the deadlines are fired early.

const HARNESS = `http://127.0.0.1:${process.env.E2E_PORT ?? 2024}`;
const REPO = { owner: "fakeorg", repo: "demo" };
const REVIEW_CHANNEL = "CREVIEWS01";
const ALICE = { login: "alice", email: "alice@example.com", slack: "U_ALICE" };
const BOB = { login: "bob", email: "bob@example.com", slack: "U_BOB" };
const TLDR =
  "Makes the greeting punctuation consistent and covers it with a test.";
const CORRECTED_TLDR =
  "Ends every greeting with one exclamation mark, with a test.";
const GREEN = [
  {
    id: 7001,
    name: "unit tests",
    status: "completed",
    conclusion: "success",
    required: true,
  },
];

type Block = {
  type: string;
  text?: { text: string };
  elements?: Array<{
    type?: string;
    text?: { text: string } | string;
    action_id?: string;
    value?: string;
    url?: string;
  }>;
};

type SlackMessage = {
  channel: string;
  user: string;
  text: string;
  is_bot: boolean;
  ts: string;
  thread_ts: string;
  blocks: Array<Block> | null;
  reply_broadcast: boolean;
};

type ReviewRequest = {
  id: string;
  state: string;
  detail: string;
  pr_number: number;
  thread_id: string;
  tldr: string;
  slack_channel_id: string;
  slack_thread_ts: string;
  slack_message_ts: string;
  slack_broadcast: boolean;
  reviewers: Array<{ github_login: string; assigned_by_agent: boolean }>;
  picks: Array<string>;
};

type PullRequest = {
  number: number;
  url: string;
  head_sha: string;
  state: string;
  merged: boolean;
  requested_reviewers: Array<string>;
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
  return await res.json();
}

async function reviewRequests(
  request: APIRequestContext,
): Promise<Array<ReviewRequest>> {
  const res = await request.get("/control/human-review-requests");
  if (!res.ok()) {
    throw new Error(`human review requests → ${res.status()}`);
  }
  return (await res.json()) as Array<ReviewRequest>;
}

async function latestRequest(
  request: APIRequestContext,
): Promise<ReviewRequest> {
  const found = (await reviewRequests(request)).at(-1);
  expect(found, "a review request should exist").toBeTruthy();
  return found!;
}

async function pull(
  request: APIRequestContext,
  number: number,
): Promise<PullRequest> {
  const prs = (await (
    await request.get("/mock/github/data")
  ).json()) as Array<PullRequest>;
  const found = prs.find((item) => item.number === number);
  expect(found, `pull request #${number} should exist`).toBeTruthy();
  return found!;
}

async function channelMessages(
  request: APIRequestContext,
  channel: string,
  threadTs = "",
): Promise<Array<SlackMessage>> {
  const query = new URLSearchParams({ channel, thread_ts: threadTs });
  const res = await request.get(`/mock/slack/messages?${query}`);
  return (await res.json()) as Array<SlackMessage>;
}

/** The DM telling someone Open SWE picked them, once it arrives. */
async function pickedDm(
  request: APIRequestContext,
  channel: string,
): Promise<SlackMessage> {
  let found: SlackMessage | undefined;
  await expect
    .poll(
      async () => {
        found = (await channelMessages(request, channel)).find(
          (m) => m.is_bot && m.text.includes("picked you to review"),
        );
        return found !== undefined;
      },
      { message: "the pick should be DMed", timeout: 30_000 },
    )
    .toBe(true);
  return found!;
}

function cardText(message: SlackMessage): string {
  return (message.blocks ?? [])
    .flatMap((block) => [
      block.text?.text ?? "",
      ...(block.elements ?? []).map((element) =>
        typeof element.text === "string"
          ? element.text
          : (element.text?.text ?? ""),
      ),
    ])
    .join("\n");
}

async function reviewCard(
  request: APIRequestContext,
  req: ReviewRequest,
): Promise<SlackMessage> {
  const messages = await channelMessages(
    request,
    req.slack_channel_id,
    req.slack_thread_ts || req.slack_message_ts,
  );
  const found = messages.find((m) => m.ts === req.slack_message_ts);
  expect(found, "the review card should be in Slack").toBeTruthy();
  return found!;
}

function buttons(message: SlackMessage): Array<string> {
  return (message.blocks ?? [])
    .filter((block) => block.type === "actions")
    .flatMap((block) => block.elements ?? [])
    .map((element) =>
      typeof element.text === "string" ? element.text : element.text?.text,
    )
    .filter((label): label is string => Boolean(label));
}

/** Click a card button as ``slackUser``, the way Slack delivers it. */
async function click(
  request: APIRequestContext,
  req: ReviewRequest,
  label: string,
  slackUser: string,
) {
  const message = await reviewCard(request, req);
  const action = (message.blocks ?? [])
    .filter((block) => block.type === "actions")
    .flatMap((block) => block.elements ?? [])
    .find(
      (element) =>
        (typeof element.text === "string"
          ? element.text
          : element.text?.text) === label,
    );
  expect(action, `the card should offer ${label}`).toBeTruthy();
  await control(request, "/mock/slack/action", {
    action,
    channel: message.channel,
    message_ts: message.ts,
    thread_ts: message.thread_ts,
    user: slackUser,
  });
}

/** Submit a GitHub review as ``login``, then deliver the webhook GitHub would send. */
async function approveOnGitHub(
  request: APIRequestContext,
  number: number,
  login: string,
) {
  const pr = await pull(request, number);
  const res = await request.post(
    `${HARNESS}/fake-gh/repos/${REPO.owner}/${REPO.repo}/pulls/${number}/reviews`,
    {
      headers: { Authorization: `Bearer dummy-user-oauth-token:${login}` },
      data: { event: "APPROVE", commit_id: pr.head_sha, body: "LGTM" },
    },
  );
  expect(res.ok(), await res.text()).toBeTruthy();
  await control(request, "/control/github-event", {
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
      sender: { login },
      pull_request: { number, user: { login: "octocat" } },
      review: { state: "approved", body: "LGTM", user: { login } },
    },
  });
}

async function setReviewChannel(request: APIRequestContext) {
  await control(request, "/control/repo-file", {
    repo: `${REPO.owner}/${REPO.repo}`,
    files: {
      ".open-swe/settings.json": JSON.stringify({
        reviewChannel: REVIEW_CHANNEL,
      }),
    },
  });
}

async function grantWrite(request: APIRequestContext) {
  for (const person of [ALICE, BOB]) {
    await control(request, "/control/collaborator-permission", {
      login: person.login,
      permission: "write",
    });
  }
}

async function shootCard(page: Page, name: string) {
  await page.goto("/mock/slack");
  await page.locator(`[data-channel-id="${REVIEW_CHANNEL}"]`).click();
  const card = page
    .locator(".msg.bot")
    .filter({ hasText: /Review request/i })
    .last();
  await expect(card).toBeVisible({ timeout: 30_000 });
  // The mock UI re-renders every poll, so a screenshot can land on a detached node.
  await expect(async () => {
    await card.screenshot({
      path: `test-results/human-review-${name}.png`,
      timeout: 2_000,
    });
  }).toPass({ timeout: 15_000 });
}

test.describe("Human review in Slack", () => {
  test.beforeEach(async ({ request }) => {
    await request.post("/control/reset");
    await grantWrite(request);
  });

  // Preferences live on the users row, which a reset keeps.
  test.afterEach(async ({ request }) => {
    await control(request, "/control/user-preferences", {
      login: BOB.login,
      preferences: { concierge_mode: false },
    });
  });

  test("the agent posts a card in the review channel; two sign up; it merges two hours after one approval", async ({
    page,
    request,
  }) => {
    test.setTimeout(240_000);
    await setReviewChannel(request);
    const seeded = await seedOpenPullRequest(page, {
      repo: `${REPO.owner}/${REPO.repo}`,
      title: "Tidy the greeting",
      author: "octocat",
      body: `## Summary\n\n${"The greeting ended with two exclamation marks in one place and one in another. ".repeat(6)}\n\n## Test plan\n\n- [x] unit tests`,
      check_runs: GREEN,
    });
    const pr = await pull(request, seeded.number);

    // 1. Asked from another channel, the card is a new post in the review channel
    //    and the asking thread is told where it went.
    const asked = (await control(request, "/mock/slack/send", {
      text: `<@U0BOT> get ${pr.url} reviewed by a human E2E_HUMAN_REVIEW`,
      mention_bot: true,
    })) as { thread_ts: string };
    await expect
      .poll(async () => (await reviewRequests(request)).length, {
        timeout: 90_000,
      })
      .toBe(1);
    await expect
      .poll(async () =>
        (await channelMessages(request, "C_DEMO", asked.thread_ts)).map(
          (m) => m.text,
        ),
      )
      .toContain(`Review requested in <#${REVIEW_CHANNEL}>.`);
    const posted = await latestRequest(request);
    expect(posted.slack_channel_id).toBe(REVIEW_CHANNEL);
    expect(posted.slack_thread_ts).toBe("");
    expect(posted.tldr).toBe(TLDR);

    const card = await reviewCard(request, posted);
    expect(card.thread_ts).toBe(card.ts);
    expect(card.reply_broadcast).toBe(false);
    const text = cardText(card);
    expect(text).toContain(`fakeorg/demo#${seeded.number}`);
    expect(text).toContain("Tidy the greeting");
    expect(text).toContain(TLDR);
    expect(text).toContain(`Requested by <@${ALICE.slack}>`);
    expect(text).not.toContain("Reviewers");
    expect(text).not.toContain("Merges on its own");
    expect(buttons(card)).toEqual(["I'll review", "Dismiss"]);
    const review = (card.blocks ?? [])
      .flatMap((block) => block.elements ?? [])
      .find((element) => element.action_id === "open_swe_option_select_review");
    expect(review?.url).toBe(
      `https://github.com/${REPO.owner}/${REPO.repo}/pull/${seeded.number}`,
    );
    await shootCard(page, "open");

    // Asked in the same thread, the agent corrects the card's summary in place.
    await control(request, "/mock/slack/send", {
      thread_ts: asked.thread_ts,
      text: `<@U0BOT> that summary is wrong, fix it E2E_HUMAN_REVIEW_RESUMMARIZE`,
      mention_bot: true,
    });
    await expect
      .poll(async () => (await latestRequest(request)).tldr, {
        timeout: 60_000,
      })
      .toBe(CORRECTED_TLDR);
    expect(await reviewRequests(request)).toHaveLength(1);
    await expect
      .poll(async () => cardText(await reviewCard(request, posted)))
      .toContain(CORRECTED_TLDR);

    // 2. Alice and Bob both sign up; each becomes a requested reviewer on GitHub.
    await click(request, posted, "I'll review", ALICE.slack);
    await click(request, posted, "I'll review", BOB.slack);
    await expect
      .poll(
        async () =>
          (await latestRequest(request)).reviewers.map((r) => r.github_login),
        { timeout: 30_000 },
      )
      .toEqual(["alice", "bob"]);
    await expect
      .poll(
        async () => (await pull(request, seeded.number)).requested_reviewers,
      )
      .toEqual(["alice", "bob"]);
    await expect
      .poll(async () => cardText(await reviewCard(request, posted)))
      .toMatch(/<@U_ALICE>.*reviewing[\s\S]*<@U_BOB>.*reviewing/);

    // 3. Alice approves. With Bob still reviewing, it waits for him.
    await approveOnGitHub(request, seeded.number, ALICE.login);
    await expect
      .poll(async () => (await latestRequest(request)).detail, {
        timeout: 30_000,
      })
      .toBe("approval from @bob");
    expect((await pull(request, seeded.number)).merged).toBe(false);
    await expect
      .poll(async () => cardText(await reviewCard(request, posted)))
      .toContain("approved");
    await shootCard(page, "one-approval");

    // 4. Two hours after the request, one approval is enough.
    await control(request, "/control/human-review-deadline", {
      request_id: posted.id,
      step: "auto_merge",
      hours: 2,
    });
    expect((await pull(request, seeded.number)).merged).toBe(true);
    expect((await latestRequest(request)).state).toBe("merged");
    expect(cardText(await reviewCard(request, posted))).toContain(
      "Review request: merged",
    );
    await shootCard(page, "merged");
  });

  test("refused while unreviewable; a request from the review channel's thread is broadcast and leaves the channel when dismissed", async ({
    page,
    request,
  }) => {
    test.setTimeout(180_000);
    await loginAs(page, ALICE);
    const seeded = await seedOpenPullRequest(page, {
      repo: `${REPO.owner}/${REPO.repo}`,
      title: "Say goodbye politely",
      author: "octocat",
      body: "Adds a farewell helper.",
      mergeable_state: "blocked",
      check_runs: [
        {
          id: 7002,
          name: "unit tests",
          status: "completed",
          conclusion: "failure",
          required: true,
        },
      ],
    });
    const url = `/dashboard/api/repos/${REPO.owner}/${REPO.repo}/pulls/${seeded.number}/human-review`;
    const ask = () =>
      page.request.post(url, { headers: SAME_ORIGIN_HEADERS, data: {} });

    // 1. The dashboard refuses a failing required check, a conflict, and a
    //    repository with no review channel, each with the reason.
    let refused = await ask();
    expect(refused.status()).toBe(409);
    expect(await refused.text()).toContain("required checks are failing");

    await control(request, "/control/pull-request-health", {
      number: seeded.number,
      mergeable: false,
      mergeable_state: "dirty",
      check_runs: GREEN,
    });
    refused = await ask();
    expect(refused.status()).toBe(409);
    expect(await refused.text()).toContain("merge conflicts");

    await control(request, "/control/pull-request-health", {
      number: seeded.number,
      mergeable: true,
      mergeable_state: "clean",
    });
    refused = await ask();
    expect(refused.status()).toBe(409);
    expect(await refused.text()).toContain("has no review channel");
    expect(await reviewRequests(request)).toEqual([]);

    // 2. Asked from a thread in the channel the agent is told to use, the card
    //    is a reply in that thread that is also sent to the channel.
    const pr = await pull(request, seeded.number);
    const sent = (await control(request, "/mock/slack/send", {
      channel: REVIEW_CHANNEL,
      text: `<@U0BOT> can someone review ${pr.url} E2E_HUMAN_REVIEW_HERE`,
      mention_bot: true,
    })) as { thread_ts?: string };
    await expect
      .poll(async () => (await reviewRequests(request)).length, {
        timeout: 90_000,
      })
      .toBe(1);
    const posted = await latestRequest(request);
    expect(posted.slack_channel_id).toBe(REVIEW_CHANNEL);
    expect(posted.slack_thread_ts).not.toBe("");
    if (sent.thread_ts) expect(posted.slack_thread_ts).toBe(sent.thread_ts);
    expect(posted.slack_broadcast).toBe(true);
    expect(posted.tldr).toBe(TLDR);
    const broadcast = await reviewCard(request, posted);
    expect(broadcast.reply_broadcast).toBe(true);
    await shootCard(page, "thread-broadcast");

    // 3. Signing up from a card posted in a thread and sent to the channel works
    //    like one posted at the top of the channel.
    await click(request, posted, "I'll review", ALICE.slack);
    await expect
      .poll(async () => (await latestRequest(request)).reviewers, {
        timeout: 30_000,
      })
      .toEqual([{ github_login: "alice", assigned_by_agent: false }]);
    await expect
      .poll(
        async () => (await pull(request, seeded.number)).requested_reviewers,
      )
      .toEqual(["alice"]);
    await expect
      .poll(async () => cardText(await reviewCard(request, posted)))
      .toMatch(/<@U_ALICE>.*reviewing/);

    // 4. Anyone may dismiss it. The channel copy goes; the thread keeps the
    //    closed card.
    await click(request, posted, "Dismiss", BOB.slack);
    await expect
      .poll(async () => (await latestRequest(request)).state, {
        timeout: 30_000,
      })
      .toBe("cancelled");
    const closed = await latestRequest(request);
    expect(closed.slack_broadcast).toBe(false);
    expect(closed.slack_message_ts).not.toBe(posted.slack_message_ts);
    const thread = await channelMessages(
      request,
      REVIEW_CHANNEL,
      posted.slack_thread_ts,
    );
    expect(thread.find((m) => m.ts === posted.slack_message_ts)).toBeFalsy();
    const final = await reviewCard(request, closed);
    expect(final.reply_broadcast).toBe(false);
    expect(cardText(final)).toContain(
      `Review request: dismissed by <@${BOB.slack}>`,
    );
  });

  test("approved pull requests do not offer Slack review requests", async ({
    page,
  }) => {
    await loginAs(page, ALICE);
    await seedOpenPullRequest(page, {
      repo: `${REPO.owner}/${REPO.repo}`,
      title: "Already approved",
      author: ALICE.login,
      reviews: [{ author: BOB.login, state: "APPROVED" }],
      check_runs: GREEN,
    });
    await page.goto("/agents/reviews");
    const row = page
      .getByRole("listitem")
      .filter({ hasText: `Already approved` });
    await expect(row).toBeVisible();
    await expect(
      row.getByRole("button", { name: "Request review in Slack" }),
    ).toHaveCount(0);
  });

  test("requested from the dashboard; nobody signs up, so the agent picks a reviewer, tags and DMs them, and it merges on their approval", async ({
    page,
    request,
  }) => {
    test.setTimeout(240_000);
    await setReviewChannel(request);

    // Bob keeps his bot DM as one concierge conversation, which already exists.
    await control(request, "/control/user-preferences", {
      login: BOB.login,
      preferences: { concierge_mode: true },
    });
    const dm = (await control(request, "/mock/slack/send", {
      channel: "D_BOB",
      channel_type: "im",
      user: BOB.slack,
      mention_bot: false,
      text: "hello E2E_HELLO",
    })) as { thread_id: string; thread_ts: string };
    expect(dm.thread_ts, "Bob's DM should run in concierge mode").toBe("0");
    expect(dm.thread_id).toBeTruthy();
    await expect
      .poll(
        async () =>
          (
            (await (
              await request.get(
                `/control/thread-idle?thread_id=${encodeURIComponent(dm.thread_id)}`,
              )
            ).json()) as { idle: boolean; runs: number }
          ).idle,
        { timeout: 60_000 },
      )
      .toBe(true);

    // 1. Alice asks from the dashboard.
    await loginAs(page, ALICE);
    const seeded = await seedOpenPullRequest(page, {
      repo: `${REPO.owner}/${REPO.repo}`,
      title: "Greet by name",
      author: ALICE.login,
      body: `<!-- template -->\nUses the name in the greeting. ${"It also trims the whitespace around it. ".repeat(10)}`,
      check_runs: GREEN,
    });
    await page.goto("/agents/reviews");
    const row = page
      .getByRole("listitem")
      .filter({ hasText: new RegExp(`#${seeded.number}(?!\\d)`) });
    await row.getByRole("button", { name: "Request review in Slack" }).click();
    await expect(page.getByText(/Asked Slack to review/)).toBeVisible({
      timeout: 30_000,
    });
    const posted = await latestRequest(request);
    expect(posted.slack_channel_id).toBe(REVIEW_CHANNEL);
    expect(posted.thread_id).toBe("");
    // With no summary from an agent, the card shows the description's start, cut with an ellipsis.
    expect(posted.tldr).toMatch(
      /^Uses the name in the greeting\. It also trims.*…$/,
    );
    expect(posted.tldr.length).toBeLessThanOrEqual(280);
    // Alice wrote the PR and asked for the review, so the card names her once.
    expect(cardText(await reviewCard(request, posted))).not.toContain(
      "Requested by",
    );

    // 2. The auto-assignment timeout passes with nobody signed up: the agent owning
    //    the card's thread is woken and picks Bob, who has yet to accept.
    await control(request, "/control/human-review-deadline", {
      request_id: posted.id,
      step: "unclaimed",
      hours: 2,
    });
    await expect
      .poll(async () => (await latestRequest(request)).picks, {
        timeout: 90_000,
      })
      .toEqual(["bob"]);
    expect((await latestRequest(request)).reviewers).toEqual([]);
    await expect
      .poll(
        async () => (await pull(request, seeded.number)).requested_reviewers,
      )
      .toEqual(["bob"]);
    await expect
      .poll(async () => cardText(await reviewCard(request, posted)))
      .toContain("waiting for them to accept");

    // Tagged in the card's thread...
    const replies = await channelMessages(
      request,
      REVIEW_CHANNEL,
      posted.slack_message_ts,
    );
    expect(
      replies.some(
        (m) =>
          m.is_bot &&
          m.text.includes(`<@${BOB.slack}>`) &&
          m.text.includes("Open SWE picked you"),
      ),
    ).toBe(true);
    // ...and messaged in his DM, which is his concierge conversation.
    const picked = await pickedDm(request, "D_BOB");
    expect(picked.thread_ts).toBe(picked.ts);
    const state = await request.get(`/threads/${dm.thread_id}/state`);
    expect(JSON.stringify(await state.json())).toContain(
      "picked you to review",
    );
    await shootCard(page, "picked");

    // 3. Bob accepts from his DM and becomes the reviewer.
    const accept = (picked.blocks ?? [])
      .filter((block) => block.type === "actions")
      .flatMap((block) => block.elements ?? [])
      .find(
        (element) =>
          (typeof element.text === "string"
            ? element.text
            : element.text?.text) === "Accept",
      );
    expect(accept, "the DM should offer Accept").toBeTruthy();
    await control(request, "/mock/slack/action", {
      action: accept,
      channel: "D_BOB",
      message_ts: picked.ts,
      thread_ts: picked.thread_ts,
      user: BOB.slack,
    });
    await expect
      .poll(async () => (await latestRequest(request)).reviewers, {
        timeout: 30_000,
      })
      .toEqual([{ github_login: "bob", assigned_by_agent: true }]);
    expect((await latestRequest(request)).picks).toEqual([]);
    await expect
      .poll(async () => cardText(await reviewCard(request, posted)))
      .toContain("picked by Open SWE");
    await shootCard(page, "assigned");

    // 4. Bob is the only reviewer, so his approval merges it at once.
    await approveOnGitHub(request, seeded.number, BOB.login);
    await expect
      .poll(async () => (await pull(request, seeded.number)).merged, {
        timeout: 30_000,
      })
      .toBe(true);
    expect((await latestRequest(request)).state).toBe("merged");
  });

  test("nobody signs up, so Open SWE picks the code owner without an agent; they accept from their DM and their approval merges it", async ({
    page,
    request,
  }) => {
    test.setTimeout(120_000);
    await setReviewChannel(request);
    // Alice owns everything but wrote the PR; Bob owns the directory it changes.
    await control(request, "/control/repo-file", {
      repo: `${REPO.owner}/${REPO.repo}`,
      files: { ".github/CODEOWNERS": "* @alice\n/greeting/ @bob\n" },
    });

    // 1. Alice asks from the dashboard.
    await loginAs(page, ALICE);
    const seeded = await seedOpenPullRequest(page, {
      repo: `${REPO.owner}/${REPO.repo}`,
      title: "Greet in French",
      author: ALICE.login,
      body: "Says bonjour.",
      files: { "greeting/hello.py": 'print("Bonjour")\n' },
      check_runs: GREEN,
    });
    await page.goto("/agents/reviews");
    await page
      .getByRole("listitem")
      .filter({ hasText: new RegExp(`#${seeded.number}(?!\\d)`) })
      .getByRole("button", { name: "Request review in Slack" })
      .click();
    await expect(page.getByText(/Asked Slack to review/)).toBeVisible({
      timeout: 30_000,
    });
    const posted = await latestRequest(request);

    // 2. Nobody signs up: Open SWE suggests Bob from CODEOWNERS, and the agent owning
    //    the card's thread picks him with Open SWE's reason.
    await control(request, "/control/human-review-deadline", {
      request_id: posted.id,
      step: "unclaimed",
      hours: 2,
    });
    await expect
      .poll(async () => (await latestRequest(request)).picks, {
        timeout: 90_000,
      })
      .toEqual(["bob"]);
    expect((await latestRequest(request)).reviewers).toEqual([]);
    await expect
      .poll(
        async () => (await pull(request, seeded.number)).requested_reviewers,
      )
      .toEqual(["bob"]);
    await expect
      .poll(async () => cardText(await reviewCard(request, posted)))
      .toContain("waiting for them to accept");
    const replies = await channelMessages(
      request,
      REVIEW_CHANNEL,
      posted.slack_message_ts,
    );
    expect(
      replies.some(
        (m) =>
          m.is_bot &&
          m.text.includes(`<@${BOB.slack}>`) &&
          m.text.includes("You own 1 of the 1 changed file"),
      ),
      "the pick should say why Bob was chosen",
    ).toBe(true);

    // 3. Bob accepts from his DM.
    const picked = await pickedDm(request, "D_BOB");
    const accept = (picked.blocks ?? [])
      .filter((block) => block.type === "actions")
      .flatMap((block) => block.elements ?? [])
      .find(
        (element) =>
          (typeof element.text === "string"
            ? element.text
            : element.text?.text) === "Accept",
      );
    expect(accept, "the DM should offer Accept").toBeTruthy();
    await control(request, "/mock/slack/action", {
      action: accept,
      channel: "D_BOB",
      message_ts: picked.ts,
      thread_ts: picked.thread_ts,
      user: BOB.slack,
    });
    await expect
      .poll(async () => (await latestRequest(request)).reviewers, {
        timeout: 30_000,
      })
      .toEqual([{ github_login: "bob", assigned_by_agent: true }]);
    expect((await latestRequest(request)).picks).toEqual([]);

    // 4. Bob is the only reviewer, so his approval merges it.
    await approveOnGitHub(request, seeded.number, BOB.login);
    await expect
      .poll(async () => (await pull(request, seeded.number)).merged, {
        timeout: 30_000,
      })
      .toBe(true);
    expect((await latestRequest(request)).state).toBe("merged");
  });

  test("the agent dismisses the review request its thread posted", async ({
    page,
    request,
  }) => {
    test.setTimeout(180_000);
    await setReviewChannel(request);
    const seeded = await seedOpenPullRequest(page, {
      repo: `${REPO.owner}/${REPO.repo}`,
      title: "Tidy the greeting",
      author: "octocat",
      body: "Ends every greeting with one exclamation mark.",
      check_runs: GREEN,
    });
    const pr = await pull(request, seeded.number);
    const asked = (await control(request, "/mock/slack/send", {
      text: `<@U0BOT> get ${pr.url} reviewed by a human E2E_HUMAN_REVIEW`,
      mention_bot: true,
    })) as { thread_ts: string };
    await expect
      .poll(
        async () =>
          (await reviewRequests(request)).at(-1)?.slack_message_ts ?? "",
        { timeout: 90_000 },
      )
      .not.toBe("");
    const posted = await latestRequest(request);

    await control(request, "/mock/slack/send", {
      thread_ts: asked.thread_ts,
      text: "<@U0BOT> take that review request down E2E_HUMAN_REVIEW_DISMISS",
      mention_bot: true,
    });
    await expect
      .poll(async () => (await latestRequest(request)).state, {
        timeout: 60_000,
      })
      .toBe("cancelled");
    expect(cardText(await reviewCard(request, posted))).toContain(
      "Review request: dismissed by Open SWE: posted with the wrong summary",
    );
  });

  test("the oswe MCP tools' endpoints post a card with their summary, re-summarize it, and dismiss it", async ({
    page,
    request,
  }) => {
    test.setTimeout(120_000);
    await loginAs(page, ALICE);
    await setReviewChannel(request);
    const seeded = await seedOpenPullRequest(page, {
      repo: `${REPO.owner}/${REPO.repo}`,
      title: "Tidy the greeting",
      author: "octocat",
      body: "Ends every greeting with one exclamation mark.",
      check_runs: GREEN,
    });
    const url = `/dashboard/api/repos/${REPO.owner}/${REPO.repo}/pulls/${seeded.number}/human-review`;

    const asked = await page.request.post(url, {
      headers: SAME_ORIGIN_HEADERS,
      data: { inline_summary: TLDR },
    });
    expect(asked.status()).toBe(200);
    const posted = await latestRequest(request);
    expect(posted.tldr).toBe(TLDR);
    expect(cardText(await reviewCard(request, posted))).toContain(TLDR);

    const again = await page.request.post(url, {
      headers: SAME_ORIGIN_HEADERS,
      data: { inline_summary: CORRECTED_TLDR },
    });
    expect(await again.json()).toMatchObject({
      reused: true,
      summary_updated: true,
    });
    expect(cardText(await reviewCard(request, posted))).toContain(
      CORRECTED_TLDR,
    );

    const dismissed = await page.request.post(`${url}/dismiss`, {
      headers: SAME_ORIGIN_HEADERS,
      data: { reason: "wrong channel" },
    });
    expect(await dismissed.json()).toEqual({ request_id: posted.id });
    expect((await latestRequest(request)).state).toBe("cancelled");
    expect(cardText(await reviewCard(request, posted))).toContain(
      `Review request: dismissed by <@${ALICE.slack}>: wrong channel`,
    );
  });
});
