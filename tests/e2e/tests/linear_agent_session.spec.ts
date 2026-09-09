import { test, expect, type APIRequestContext } from "@playwright/test";

// The Linear Agents API path, driven through the fake Linear + fake GitHub:
//   Linear delegates an issue -> the real agent picks it up, mirrors its
//   progress into the agent session as activities, implements the change,
//   opens a PR on the fake GitHub, and the platform's run-completion webhook
//   closes the session with a terminal `response`.
// Only the LLM and the Linear/GitHub HTTP boundaries are faked.

type LinearActivity = {
  id: string;
  type: string;
  body: string;
  action: string;
  parameter: string;
  ephemeral: boolean;
};

type LinearSession = {
  id: string;
  status: string;
  external_link: string | null;
  activities: LinearActivity[];
};

type LinearComment = {
  id: string;
  body: string;
  author: string;
  bot: boolean;
  reactions: string[];
};

type LinearIssue = {
  id: string;
  identifier: string;
  title: string;
  comments: LinearComment[];
  sessions: LinearSession[];
};

type LinearData = {
  issues: LinearIssue[];
  token_requests: Array<Record<string, string>>;
};

type WebhookAck = {
  status: string;
  reason: string;
  webhook_status: number;
};

type DelegateResult = WebhookAck & {
  session_id: string;
  issue_id: string;
  thread_id: string;
};

type CommentResult = WebhookAck & {
  comment_id: string;
  issue_id: string;
  thread_id: string;
};

// A ticket the scripted model implements: it clones, edits, pushes and opens a PR.
const TASK = "E2E_LINEAR";
// A ticket the scripted model only acknowledges — for the delivery-handling
// specs, which care about the webhook's answer, not about a whole run.
const ACK_TASK = "E2E_LINEAR_ACK";

const ackDescription = `Confirm you picked this up; no code changes needed. ${ACK_TASK}`;

async function linearData(request: APIRequestContext): Promise<LinearData> {
  const res = await request.get("/mock/linear/data");
  expect(res.ok()).toBeTruthy();
  return (await res.json()) as LinearData;
}

function findSession(data: LinearData, sessionId: string): LinearSession | undefined {
  return data.issues
    .flatMap((issue) => issue.sessions)
    .find((session) => session.id === sessionId);
}

async function sessionState(
  request: APIRequestContext,
  sessionId: string,
): Promise<LinearSession> {
  const session = findSession(await linearData(request), sessionId);
  return session ?? { id: sessionId, status: "", external_link: null, activities: [] };
}

async function issueState(
  request: APIRequestContext,
  issueId: string,
): Promise<LinearIssue | undefined> {
  return (await linearData(request)).issues.find((issue) => issue.id === issueId);
}

async function delegate(
  request: APIRequestContext,
  data: Record<string, unknown> = {},
): Promise<DelegateResult> {
  const res = await request.post("/control/linear/delegate", { data });
  return (await res.json()) as DelegateResult;
}

// A delivery that never started a run leaves no thread behind, so the platform
// answers 404 rather than an empty list.
async function runCount(request: APIRequestContext, threadId: string): Promise<number> {
  const res = await request.get(`/threads/${threadId}/runs`);
  if (!res.ok()) return 0;
  return ((await res.json()) as unknown[]).length;
}

async function activityTypes(
  request: APIRequestContext,
  sessionId: string,
): Promise<string[]> {
  return (await sessionState(request, sessionId)).activities.map((activity) => activity.type);
}

test.describe("Linear agent sessions", () => {
  test.beforeEach(async ({ request }) => {
    await request.post("/control/reset");
  });

  test("a delegated issue is acknowledged with a thought, using an app-actor token", async ({
    request,
  }) => {
    const opened = await delegate(request, { issue_description: ackDescription });
    expect(opened.status).toBe("accepted");

    // Linear expects an activity within ten seconds of delegating.
    await expect
      .poll(() => activityTypes(request, opened.session_id), { timeout: 10_000 })
      .not.toHaveLength(0);
    expect((await activityTypes(request, opened.session_id))[0]).toBe("thought");

    // The Linear calls behind that went out as the app, not as a person.
    const { token_requests: tokenRequests } = await linearData(request);
    expect(tokenRequests.length).toBeGreaterThan(0);
    expect(tokenRequests[0]).toMatchObject({
      grant_type: "client_credentials",
      actor: "app",
      client_id: "linear-e2e-client",
    });

    // Let the run settle so it cannot overlap the next spec's sandbox.
    await expect
      .poll(async () => (await sessionState(request, opened.session_id)).status, {
        timeout: 90_000,
      })
      .toBe("complete");
  });

  test("delegated issue → implements → opens a PR → completes the session", async ({
    page,
    request,
  }) => {
    test.setTimeout(180_000);
    const opened = await delegate(request);
    expect(opened.status).toBe("accepted");

    await expect
      .poll(async () => (await sessionState(request, opened.session_id)).status, {
        timeout: 120_000,
      })
      .toBe("complete");

    const session = await sessionState(request, opened.session_id);
    expect(session.activities[0].type).toBe("thought");

    // Tool progress is mirrored as ephemeral action activities.
    const actions = session.activities.filter((activity) => activity.type === "action");
    expect(actions.length).toBeGreaterThan(0);
    expect(actions.every((activity) => activity.ephemeral)).toBe(true);
    expect(actions.every((activity) => activity.action.length > 0)).toBe(true);

    // The terminal response is the run-completion webhook's work: the agent's
    // last words, the PR it opened, and a link back into Open SWE Web.
    const responses = session.activities.filter((activity) => activity.type === "response");
    expect(responses).toHaveLength(1);
    const response = responses[0];
    expect(response.ephemeral).toBe(false);
    expect(response.body).toContain("/pull/");
    expect(response.body).toContain(`/agents/${opened.thread_id}`);

    // The session's external link is the dashboard thread it now owns.
    expect(session.external_link).not.toBeNull();
    expect(session.external_link).toMatch(
      new RegExp(`/agents/${opened.thread_id}$`),
    );

    // What the fake Linear stored is what a person sees on the issue.
    await page.goto("/mock/linear");
    const rendered = page
      .getByTestId("linear-activity")
      .filter({ has: page.getByTestId("linear-activity-type").getByText("response") });
    await expect(rendered).toHaveCount(1);
    await expect(rendered).toContainText("greet()");
    // The body names the PR twice: the agent's own words, then the completion
    // webhook's "Pull request:" line.
    const prLink = rendered.locator('a[href*="/pull/"]').first();
    await expect(prLink).toBeVisible();
    await expect(page.getByTestId("linear-session-status")).toHaveText("complete");

    // And the PR really exists on the fake GitHub.
    const pulls = (await (await request.get("/mock/github/data")).json()) as Array<{
      title: string;
      head: string;
      url: string;
    }>;
    expect(pulls).toHaveLength(1);
    expect(pulls[0].head).toBe("add-greet");
    expect(await prLink.getAttribute("href")).toBe(pulls[0].url);
  });

  test("a redelivered agent session event starts no second run", async ({ request }) => {
    const deliveryId = `e2e-linear-${Date.now()}`;
    const first = await delegate(request, {
      delivery_id: deliveryId,
      issue_description: ackDescription,
    });
    expect(first.status).toBe("accepted");

    const second = await delegate(request, {
      delivery_id: deliveryId,
      issue_description: ackDescription,
    });
    expect(second).toMatchObject({ status: "ignored", reason: "duplicate delivery" });

    await expect
      .poll(() => activityTypes(request, first.session_id), { timeout: 10_000 })
      .not.toHaveLength(0);
    expect(await activityTypes(request, second.session_id)).toHaveLength(0);
    expect(await runCount(request, second.thread_id)).toBe(0);

    await expect
      .poll(async () => (await sessionState(request, first.session_id)).status, {
        timeout: 90_000,
      })
      .toBe("complete");
    expect(await runCount(request, first.thread_id)).toBe(1);
  });

  test("a stale delivery is rejected", async ({ request }) => {
    const stale = await delegate(request, {
      webhook_timestamp: Date.now() - 5 * 60 * 1000,
      issue_description: ackDescription,
    });
    expect(stale.webhook_status).toBe(401);
    expect(await activityTypes(request, stale.session_id)).toHaveLength(0);
    expect(await runCount(request, stale.thread_id)).toBe(0);
  });

  test("a delivery with a bad signature is rejected", async ({ request }) => {
    const res = await request.post("/webhooks/linear", {
      headers: {
        "Content-Type": "application/json",
        "Linear-Signature": "0".repeat(64),
        "Linear-Delivery": `e2e-linear-unsigned-${Date.now()}`,
      },
      data: {
        type: "AgentSessionEvent",
        action: "created",
        webhookTimestamp: Date.now(),
        agentSession: { id: "forged-session", status: "pending", issue: { id: "forged-issue" } },
      },
    });
    expect(res.status()).toBe(401);
  });

  test("a prompted follow-up runs again in the same session", async ({ request }) => {
    test.setTimeout(240_000);
    const opened = await delegate(request);
    expect(opened.status).toBe("accepted");
    await expect
      .poll(async () => (await sessionState(request, opened.session_id)).status, {
        timeout: 120_000,
      })
      .toBe("complete");
    const firstResponses = (await activityTypes(request, opened.session_id)).filter(
      (type) => type === "response",
    ).length;
    expect(firstResponses).toBe(1);
    const activitiesBefore = (await sessionState(request, opened.session_id)).activities.length;

    const prompted = (await (
      await request.post("/control/linear/prompt", {
        data: { session_id: opened.session_id, body: "Thanks — please also add a farewell()." },
      })
    ).json()) as WebhookAck;
    expect(prompted.status).toBe("accepted");

    // The follow-up is acknowledged with its own thought before any work.
    await expect
      .poll(
        async () =>
          (await sessionState(request, opened.session_id)).activities
            .slice(activitiesBefore)
            .map((activity) => activity.type),
        { timeout: 15_000 },
      )
      .toContain("thought");

    await expect
      .poll(
        async () =>
          (await activityTypes(request, opened.session_id)).filter((type) => type === "response")
            .length,
        { timeout: 120_000 },
      )
      .toBe(2);
    expect(await runCount(request, opened.thread_id)).toBe(2);
    expect((await sessionState(request, opened.session_id)).status).toBe("complete");
  });

  test("an @open-swe comment starts a run and answers on the issue", async ({ request }) => {
    test.setTimeout(180_000);
    const mentioned = (await (
      await request.post("/control/linear/comment", { data: {} })
    ).json()) as CommentResult;
    expect(mentioned.status).toBe("accepted");

    // The comment trigger predates agent sessions: it answers with a comment.
    await expect
      .poll(
        async () => {
          const issue = await issueState(request, mentioned.issue_id);
          return (issue?.comments ?? []).map((comment) => comment.body).join("\n");
        },
        { timeout: 120_000 },
      )
      .toContain("/pull/");

    const issues = (await linearData(request)).issues;
    expect(issues).toHaveLength(1);
    const issue = issues[0];
    expect(issue.sessions).toHaveLength(0);
    const trigger = issue.comments[0];
    expect(trigger.body).toContain("@open-swe");
    expect(trigger.reactions).toContain("👀");
    const answer = issue.comments.find((comment) => comment.body.includes("/pull/"));
    expect(answer?.bot).toBe(true);
    expect(await runCount(request, mentioned.thread_id)).toBe(1);
  });
});
