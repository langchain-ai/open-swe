import {
  test,
  expect,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

import {
  SAME_ORIGIN_HEADERS,
  SAME_USER,
  dismissOnboardingIfShown,
  loginAs,
} from "./helpers/dashboard";

// Goal mode against the real agent loop, with the scripted model in fake_llm.py
// (`_goal_step`): the run keeps going until it reports, rests on a wait without
// being sent back, and picks up another reviewer's comment on its PR by itself.

interface Goal {
  state: string;
  reason: string | null;
  waits: Array<{ kind: string }>;
}

async function control<T>(
  request: APIRequestContext,
  path: string,
  data?: unknown,
): Promise<T> {
  const response = await request.post(path, data === undefined ? {} : { data });
  expect(response.ok(), await response.text()).toBeTruthy();
  return (await response.json()) as T;
}

async function setGoalMode(request: APIRequestContext, on: boolean) {
  await control(request, "/control/user-preferences", {
    login: SAME_USER.login,
    preferences: { goal_mode: on },
  });
}

async function send(page: Page, text: string): Promise<string> {
  const { thread_id: threadId } = await control<{ thread_id: string }>(
    page.request,
    "/mock/slack/send",
    { text },
  );
  expect(threadId).toBeTruthy();
  return threadId;
}

async function goal(page: Page, threadId: string): Promise<Goal | null> {
  const response = await page.request.get(
    `/dashboard/api/threads/${threadId}/goal`,
  );
  expect(response.ok(), await response.text()).toBeTruthy();
  return (await response.json()) as Goal | null;
}

async function idle(
  page: Page,
  threadId: string,
): Promise<{ runs: number; idle: boolean }> {
  const response = await page.request.get(
    `/control/thread-idle?thread_id=${threadId}`,
  );
  expect(response.ok()).toBeTruthy();
  return (await response.json()) as { runs: number; idle: boolean };
}

function botReply(page: Page, text: string | RegExp) {
  return page.locator(".msg.bot").filter({ hasText: text });
}

test.describe("Goal mode (real agent loop, scripted model)", () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page, SAME_USER);
    await page.goto("/mock/slack");
    await page.locator("#reset").click();
    await setGoalMode(page.request, true);
  });

  test.afterEach(async ({ request }, testInfo) => {
    await setGoalMode(request, false);
    if (testInfo.annotations.some((note) => note.type === "cleanup")) {
      await control(request, "/control/reset-default-workspace");
    }
  });

  test("keeps working until it reports, saying nothing in between", async ({
    page,
  }) => {
    test.setTimeout(150_000);
    const threadId = await send(
      page,
      "<@U0BOT> E2E_GOAL_NUDGE fix the greeting",
    );

    await expect(
      botReply(
        page,
        "Goal complete: reported after being sent back. Nudged 1 times.",
      ),
    ).toBeVisible({ timeout: 90_000 });
    // The progress note is replaced by the report, so the thread holds one reply.
    await expect(page.locator(".msg.bot")).toHaveCount(1);
    await expect
      .poll(async () => (await goal(page, threadId))?.state)
      .toBe("waiting_on_user");
    expect((await goal(page, threadId))?.reason).toBe("ready");

    await page.goto(`/agents/${threadId}`);
    await dismissOnboardingIfShown(page);
    await expect(page.getByText("Ready for you")).toBeVisible({
      timeout: 30_000,
    });
  });

  test("pauses after three turns that stop without progress or a wait", async ({
    page,
  }) => {
    test.setTimeout(150_000);
    const threadId = await send(
      page,
      "<@U0BOT> E2E_GOAL_STALL think about the greeting",
    );

    await expect
      .poll(async () => (await goal(page, threadId))?.reason, {
        timeout: 90_000,
      })
      .toBe("no_progress");
    expect((await goal(page, threadId))?.state).toBe("paused");
    // The run's last word may replace the progress note, but nothing claims completion.
    await expect(page.locator(".msg.bot")).toHaveCount(1);
    await expect(botReply(page, "Goal complete")).toHaveCount(0);

    await page.goto(`/agents/${threadId}`);
    await dismissOnboardingIfShown(page);
    await expect(page.getByText("Goal paused")).toBeVisible({
      timeout: 30_000,
    });
  });

  test("rests on a wait without being sent back, then resumes when it fires", async ({
    page,
  }) => {
    test.setTimeout(180_000);
    const threadId = await send(
      page,
      "<@U0BOT> E2E_GOAL_WAIT ship it once the check passes",
    );

    await expect
      .poll(async () => (await goal(page, threadId))?.state, {
        timeout: 90_000,
      })
      .toBe("waiting");
    await expect.poll(async () => (await idle(page, threadId)).idle).toBe(true);
    const waiting = await goal(page, threadId);
    expect(waiting?.waits.map((wait) => wait.kind)).toContain("monitor");
    expect((await idle(page, threadId)).runs).toBe(1);
    await expect(page.locator(".msg.bot")).toHaveText([
      /Working on it in Goal mode\./,
    ]);

    await control(page.request, "/control/goal-check");
    await expect
      .poll(
        async () => {
          await page.request.post(
            `/control/monitors/sync?thread_id=${threadId}`,
          );
          return botReply(page, "Goal complete: the check passed.").count();
        },
        { timeout: 120_000, intervals: [2_000] },
      )
      .toBe(1);
    await expect(botReply(page, "Nudged 0 times.")).toBeVisible();
    await expect
      .poll(async () => (await goal(page, threadId))?.state)
      .toBe("waiting_on_user");
  });

  test("fixes another reviewer's comment on its PR without being asked", async ({
    page,
  }) => {
    test.setTimeout(180_000);
    // Listening on a PR needs its repository bound to the thread's workspace.
    const bound = await page.request.put("/dashboard/api/workspaces/default", {
      headers: SAME_ORIGIN_HEADERS,
      data: { repos: ["fakeorg/demo"] },
    });
    expect(bound.ok(), await bound.text()).toBeTruthy();
    test
      .info()
      .annotations.push({ type: "cleanup", description: "default workspace" });
    const seeded = await control<{ number: number; repo: string }>(
      page.request,
      "/control/pull-request",
      {
        repo: "fakeorg/demo",
        head: "goal-mode-greeting",
        title: "Fix the greeting",
      },
    );
    const pull = await (
      await page.request.get(
        `/fake-gh/repos/${seeded.repo}/pulls/${seeded.number}`,
      )
    ).json();
    const prUrl = `https://github.com/${seeded.repo}/pull/${seeded.number}`;
    // Slack replies point PR links at the PR's review page.
    const reviewed = `${seeded.repo}/(pull/)?${seeded.number}`;
    const ready = new RegExp(`Goal complete: \\S*${reviewed} is ready\\.`);
    const fixed = new RegExp(
      `fixed the bug devin-ai-integration\\[bot\\] found on \\S*${reviewed}\\.`,
    );
    const threadId = await send(
      page,
      `<@U0BOT> E2E_GOAL_REVIEWER get ${prUrl} ready for review`,
    );

    await expect(botReply(page, ready)).toBeVisible({ timeout: 90_000 });
    await expect.poll(async () => (await idle(page, threadId)).idle).toBe(true);

    await control(page.request, "/control/github-event", {
      event: "pull_request_review_comment",
      payload: {
        action: "created",
        repository: {
          name: "demo",
          full_name: seeded.repo,
          owner: { login: "fakeorg" },
          private: false,
        },
        installation: { id: 42 },
        pull_request: pull,
        comment: {
          id: 7001,
          body: "🔴 `greet` drops the name it was given.",
          path: "greet.py",
          line: 2,
          user: { login: "devin-ai-integration[bot]", type: "Bot" },
          html_url: `${prUrl}#discussion_r7001`,
        },
        sender: { login: "devin-ai-integration[bot]", type: "Bot" },
      },
    });

    await expect(botReply(page, fixed)).toBeVisible({ timeout: 120_000 });
    expect((await idle(page, threadId)).runs).toBeGreaterThan(1);
  });
});
