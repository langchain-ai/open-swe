import {
  expect,
  test,
  type APIRequestContext,
  type Locator,
  type Page,
} from "@playwright/test";

const USER = {
  login: "threads-workspace-e2e",
  email: "threads-workspace-e2e@example.com",
};
const BASE_URL = `http://127.0.0.1:${process.env.E2E_PORT ?? 2024}`;
const SAME_ORIGIN_HEADERS = { origin: BASE_URL, referer: `${BASE_URL}/` };
const WORKSPACE_QUERY = "E2E Workspace";

const THREAD_IDS = {
  attention: "71000000-0000-4000-8000-000000000001",
  error: "71000000-0000-4000-8000-000000000002",
  interrupted: "71000000-0000-4000-8000-000000000006",
  running: "71000000-0000-4000-8000-000000000003",
  ready: "71000000-0000-4000-8000-000000000004",
  done: "71000000-0000-4000-8000-000000000005",
  shared: "71000000-0000-4000-8000-000000000007",
  dailyScheduled: "73000000-0000-4000-8000-000000000001",
  dailyTest: "73000000-0000-4000-8000-000000000002",
  weeklyRunning: "73000000-0000-4000-8000-000000000003",
} as const;

const TITLES = {
  attention: "E2E Workspace Review auth failure",
  error: "E2E Workspace Repair deployment",
  interrupted: "E2E Workspace Interrupted release",
  running: "E2E Workspace Running refactor",
  ready: "E2E Workspace Ready docs",
  done: "E2E Workspace Resolved cleanup",
  shared: "E2E Workspace Teammate incident",
  dailyScheduled: "E2E Workspace Daily health scheduled run",
  dailyTest: "E2E Workspace Daily health test run",
  weeklyRunning: "E2E Workspace Weekly cleanup running",
} as const;

const SCHEDULE_IDS = {
  daily: "e2e-daily-health",
  weekly: "e2e-weekly-cleanup",
} as const;

interface ThreadSeed {
  id: string;
  metadata: Record<string, unknown>;
}

const createdThreadIds = new Set<string>();
const createdScheduleIds = new Set<string>();

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

async function loginAs(page: Page) {
  const response = await page.request.post("/control/login", { data: USER });
  expect(response.ok()).toBeTruthy();
}

function baseMetadata(
  now: number,
  title: string,
  updatedOffset: number,
  overrides: Record<string, unknown>,
): Record<string, unknown> {
  return {
    participant_logins: { [USER.login]: true },
    title,
    source: "dashboard",
    origin: "dashboard",
    thread_category: "interactive",
    trigger_kind: "user",
    repo_owner: "acme",
    repo_name: "alpha",
    base_branch: "main",
    branch_name: "open-swe/e2e-workspace",
    created_at_ms: now - 120_000,
    updated_at_ms: now - updatedOffset,
    ...overrides,
  };
}

function workspaceThreads(): Array<ThreadSeed> {
  const now = Date.now();
  return [
    {
      id: THREAD_IDS.attention,
      metadata: baseMetadata(now, TITLES.attention, 1_000, {
        source: "github",
        origin: "github",
        thread_category: "pull_request",
        latest_run_id: "e2e-run-attention",
        latest_run_status: "success",
        pr_number: 82,
        pr_url: "https://github.com/acme/alpha/pull/82",
        pr_title: TITLES.attention,
        pr_state: "draft",
        diff_stats: { files: 3, additions: 18, deletions: 4 },
      }),
    },
    {
      id: THREAD_IDS.error,
      metadata: baseMetadata(now, TITLES.error, 2_000, {
        source: "linear",
        origin: "linear",
        repo_name: "delta",
        latest_run_id: "e2e-run-error",
        latest_run_status: "error",
      }),
    },
    {
      id: THREAD_IDS.interrupted,
      metadata: baseMetadata(now, TITLES.interrupted, 2_500, {
        source: "github",
        origin: "github",
        repo_name: "epsilon",
        latest_run_id: "e2e-run-interrupted",
        latest_run_status: "interrupted",
      }),
    },
    {
      id: THREAD_IDS.running,
      metadata: baseMetadata(now, TITLES.running, 3_000, {
        source: "slack",
        origin: "slack",
        repo_name: "beta",
        latest_run_id: "e2e-run-running",
        latest_run_status: "running",
      }),
    },
    {
      id: THREAD_IDS.ready,
      metadata: baseMetadata(now, TITLES.ready, 4_000, {
        repo_name: "gamma",
        latest_run_id: "e2e-run-ready",
        latest_run_status: "success",
        last_viewed_run_id: "e2e-run-ready",
        last_viewed_at_ms: now - 3_500,
        pr_number: 84,
        pr_url: "https://github.com/acme/gamma/pull/84",
        pr_title: TITLES.ready,
        pr_state: "open",
      }),
    },
    {
      id: THREAD_IDS.done,
      metadata: baseMetadata(now, TITLES.done, 5_000, {
        source: "github",
        origin: "github",
        latest_run_id: "e2e-run-done",
        latest_run_status: "success",
        last_viewed_run_id: "e2e-run-done",
        last_viewed_at_ms: now - 4_500,
        resolved: true,
        resolved_at_ms: now - 4_000,
        pr_number: 85,
        pr_url: "https://github.com/acme/alpha/pull/85",
        pr_title: TITLES.done,
        pr_state: "merged",
      }),
    },
  ];
}

function resolvedOverflowThreads(): Array<ThreadSeed> {
  const now = Date.now();
  return Array.from({ length: 21 }, (_, index) => {
    const number = index + 1;
    return {
      id: `74000000-0000-4000-8000-${String(number).padStart(12, "0")}`,
      metadata: baseMetadata(
        now,
        `E2E Workspace Resolved overflow ${String(number).padStart(2, "0")}`,
        100 + index * 100,
        {
          latest_run_id: `e2e-run-resolved-overflow-${number}`,
          latest_run_status: "success",
          last_viewed_run_id: `e2e-run-resolved-overflow-${number}`,
          last_viewed_at_ms: now - (50 + index * 100),
          resolved: true,
          resolved_at_ms: now - (25 + index * 100),
        },
      ),
    };
  });
}

function automationThreads(): Array<ThreadSeed> {
  const now = Date.now();
  return [
    {
      id: THREAD_IDS.dailyScheduled,
      metadata: baseMetadata(now, TITLES.dailyScheduled, 500, {
        source: "schedule",
        origin: "schedule",
        thread_category: "automation",
        trigger_kind: "schedule",
        schedule_id: SCHEDULE_IDS.daily,
        schedule_name: "E2E Daily Health",
        automation_action_posted_at: "2026-08-21T12:00:00+00:00",
        latest_run_id: "e2e-run-daily-scheduled",
        latest_run_status: "success",
      }),
    },
    {
      id: THREAD_IDS.dailyTest,
      metadata: baseMetadata(now, TITLES.dailyTest, 1_500, {
        source: "schedule",
        origin: "schedule",
        thread_category: "automation",
        trigger_kind: "schedule_test",
        schedule_test: true,
        schedule_id: SCHEDULE_IDS.daily,
        schedule_name: "E2E Daily Health",
        latest_run_id: "e2e-run-daily-test",
        latest_run_status: "error",
      }),
    },
    {
      id: THREAD_IDS.weeklyRunning,
      metadata: baseMetadata(now, TITLES.weeklyRunning, 2_500, {
        source: "schedule",
        origin: "schedule",
        thread_category: "automation",
        trigger_kind: "schedule",
        schedule_id: SCHEDULE_IDS.weekly,
        schedule_name: "E2E Weekly Cleanup",
        repo_name: "beta",
        latest_run_id: "e2e-run-weekly-running",
        latest_run_status: "running",
      }),
    },
  ];
}

function paginationThreads(): Array<ThreadSeed> {
  const now = Date.now();
  return Array.from({ length: 26 }, (_, index) => {
    const number = index + 1;
    return {
      id: `72000000-0000-4000-8000-${String(number).padStart(12, "0")}`,
      metadata: baseMetadata(
        now,
        `E2E Pagination thread ${String(number).padStart(2, "0")}`,
        number * 1_000,
        {
          latest_run_id: `e2e-pagination-run-${number}`,
          latest_run_status: "success",
          last_viewed_run_id: `e2e-pagination-run-${number}`,
          last_viewed_at_ms: now - number * 1_000,
        },
      ),
    };
  });
}

// Earlier specs leave their own threads behind for this user, and the sidebar
// counts every one of them — so start from an empty workspace.
async function purgeParticipantThreads(request: APIRequestContext) {
  for (const owner of [{ participant_logins: { [USER.login]: true } }]) {
    for (let page = 0; page < 20; page += 1) {
      const searchResponse = await request.post("/threads/search", {
        data: { metadata: owner, limit: 100, offset: 0 },
      });
      expect(searchResponse.ok(), await searchResponse.text()).toBeTruthy();
      const threads = (await searchResponse.json()) as Array<{
        thread_id: string;
      }>;
      if (threads.length === 0) break;
      for (const thread of threads) {
        const response = await request.delete(`/threads/${thread.thread_id}`);
        expect([200, 204, 404]).toContain(response.status());
      }
    }
  }
}

async function seedThreads(
  request: APIRequestContext,
  threads: Array<ThreadSeed>,
) {
  await purgeParticipantThreads(request);
  for (const thread of threads) {
    const resetResponse = await request.delete(`/threads/${thread.id}`);
    expect([200, 204, 404]).toContain(resetResponse.status());
    const response = await request.post("/threads", {
      data: {
        thread_id: thread.id,
        if_exists: "raise",
        metadata: thread.metadata,
      },
    });
    expect(response.ok(), await response.text()).toBeTruthy();
    createdThreadIds.add(thread.id);
  }
}

async function seedSchedules(request: APIRequestContext) {
  const now = new Date().toISOString();
  const schedules = [
    {
      id: SCHEDULE_IDS.daily,
      name: "E2E Daily Health",
      prompt: "Check repository health.",
      schedule: "0 9 * * 1-5",
      repo: null,
      slack_channel_id: null,
      slack_notification_mode: "always",
      model: null,
      effort: null,
      enabled: true,
      cron_id: "e2e-cron-daily-health",
      created_by: USER.login,
      user_email: USER.email,
      created_at: now,
      updated_at: now,
    },
    {
      id: SCHEDULE_IDS.weekly,
      name: "E2E Weekly Cleanup",
      prompt: "Clean up stale work.",
      schedule: "0 10 * * 1",
      repo: { owner: "acme", name: "beta" },
      slack_channel_id: null,
      slack_notification_mode: "on_action",
      model: null,
      effort: null,
      enabled: false,
      cron_id: "e2e-cron-weekly-cleanup",
      created_by: USER.login,
      user_email: USER.email,
      created_at: now,
      updated_at: now,
    },
  ];

  for (const schedule of schedules) {
    const response = await request.put("/store/items", {
      data: {
        namespace: ["agent_schedules"],
        key: schedule.id,
        value: schedule,
      },
    });
    expect(response.ok(), await response.text()).toBeTruthy();
    createdScheduleIds.add(schedule.id);
  }
}

async function deleteScheduleThreads(
  request: APIRequestContext,
  scheduleId: string,
) {
  for (;;) {
    const searchResponse = await request.post("/threads/search", {
      data: { metadata: { schedule_id: scheduleId }, limit: 100, offset: 0 },
    });
    expect(searchResponse.ok(), await searchResponse.text()).toBeTruthy();
    const threads = (await searchResponse.json()) as Array<{
      thread_id: string;
    }>;
    if (threads.length === 0) return;
    for (const thread of threads) {
      const response = await request.delete(`/threads/${thread.thread_id}`);
      expect([200, 204, 404]).toContain(response.status());
    }
  }
}

async function cleanupFixtures(request: APIRequestContext) {
  for (const threadId of createdThreadIds) {
    const pinResponse = await request.delete("/store/items", {
      data: { namespace: ["thread_pins", USER.login], key: threadId },
    });
    expect([200, 204, 404]).toContain(pinResponse.status());
    const response = await request.delete(`/threads/${threadId}`);
    expect([200, 204, 404]).toContain(response.status());
  }
  for (const scheduleId of createdScheduleIds) {
    const response = await request.delete("/store/items", {
      data: { namespace: ["agent_schedules"], key: scheduleId },
    });
    expect([200, 204, 404]).toContain(response.status());
  }
  createdThreadIds.clear();
  createdScheduleIds.clear();
}

function waitForThreadsPage(page: Page, expected: Record<string, string>) {
  return page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      response.request().method() === "GET" &&
      url.pathname === "/dashboard/api/threads/page" &&
      Object.entries(expected).every(
        ([key, value]) => url.searchParams.get(key) === value,
      )
    );
  });
}

function boardColumn(main: Locator, name: string): Locator {
  return main.locator(`section:has(h2:text-is("${name}"))`);
}

function sidebarGroup(sidebar: Locator, name: string): Locator {
  return sidebar.locator(`div:has(> button > span:text-is("${name}"))`);
}

function sourceFilter(main: Locator): Locator {
  return main.locator('select:has(option[value="github"])');
}

function statusFilter(main: Locator): Locator {
  return main.locator('select:has(option[value="finished"])');
}

function triFilter(main: Locator, label: string): Locator {
  return main.getByText(label, { exact: true }).locator("..");
}

async function expectBoardOrder(main: Locator, expected: Array<string>) {
  await expect
    .poll(() => main.getByRole("heading", { level: 2 }).allTextContents())
    .toEqual(expected);
}

test.afterEach(async ({ request }) => {
  await cleanupFixtures(request);
});

test.describe("threads workspace", () => {
  test("pins and unpins a non-owned thread across sidebar views", async ({
    page,
    request,
  }) => {
    const thread = {
      id: THREAD_IDS.shared,
      metadata: {
        ...baseMetadata(Date.now(), TITLES.shared, 1_000, {
          github_login: "teammate",
          triggering_user_email: "teammate@example.com",
          source: "slack",
          origin: "slack",
          latest_run_id: "e2e-run-shared",
          latest_run_status: "success",
        }),
      },
    };
    await seedThreads(request, [thread]);
    await loginAs(page);
    await page.goto(`/agents/${THREAD_IDS.shared}`);

    const row = page.getByRole("link", { name: TITLES.shared }).first();
    await expect(row).toBeVisible();
    await row.click({ button: "right" });
    await page.getByText("Pin thread", { exact: true }).click();

    const pinned = sidebarGroup(page.locator("aside"), "Pinned");
    await expect(
      pinned.getByRole("link", { name: TITLES.shared }),
    ).toBeVisible();
    await page.getByRole("link", { name: "Kanban" }).click();
    await expect(
      pinned.getByRole("link", { name: TITLES.shared }),
    ).toBeVisible();

    await pinned
      .getByRole("link", { name: TITLES.shared })
      .click({ button: "right" });
    await page.getByText("Unpin thread", { exact: true }).click();
    await expect(pinned).toHaveCount(0);
  });

  test("does not flash new-thread onboarding while a thread route loads", async ({
    page,
    request,
  }) => {
    const threadId = "75000000-0000-4000-8000-000000000001";
    const title = "E2E Workspace Pending thread";
    await seedThreads(request, [
      {
        id: threadId,
        metadata: baseMetadata(Date.now(), title, 1_000, {
          latest_run_id: "e2e-run-pending-thread",
          latest_run_status: "success",
        }),
      },
    ]);
    await loginAs(page);

    const profileGate = deferred();
    const profileStarted = deferred();
    const profileFinished = deferred();
    await page.route("**/dashboard/api/profile", async (route) => {
      profileStarted.resolve();
      await profileGate.promise;
      await route.fulfill({ json: {} });
      profileFinished.resolve();
    });

    const threadChunkGate = deferred();
    const threadChunkStarted = deferred();
    await page.route(
      /\/assets\/_threadId-(?!pendingComponent-)[^/]+\.js$/,
      async (route) => {
        threadChunkStarted.resolve();
        await threadChunkGate.promise;
        await route.continue();
      },
    );

    await page.goto("/agents");
    await expect(
      page.getByText("Ask Open SWE to build, fix bugs, explore"),
    ).toBeVisible();
    await profileStarted.promise;

    await page.evaluate(() => {
      const seen = { value: false };
      (window as unknown as Record<string, unknown>).__newThreadDialogSeen =
        seen;
      const detect = () => {
        if (document.body.textContent?.includes("Choose your default model")) {
          seen.value = true;
        }
      };
      new MutationObserver(detect).observe(document.body, {
        childList: true,
        subtree: true,
      });
    });

    await page.getByRole("link", { name: title }).click();
    await threadChunkStarted.promise;
    profileGate.resolve();
    await profileFinished.promise;
    await page.evaluate(
      () =>
        new Promise<void>((resolve) => {
          requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
        }),
    );
    threadChunkGate.resolve();

    await expect(page).toHaveURL(`/agents/${threadId}`);
    await expect(
      page.getByText("This thread has no messages yet."),
    ).toBeVisible();
    const flashed = await page.evaluate(
      () =>
        (
          (window as unknown as Record<string, unknown>)
            .__newThreadDialogSeen as { value: boolean }
        ).value,
    );
    expect(flashed).toBe(false);
  });

  test("uses real thread metadata for focus and alternate groupings", async ({
    page,
    request,
  }) => {
    await seedThreads(request, [...workspaceThreads(), ...automationThreads()]);
    await loginAs(page);

    const initialResponse = waitForThreadsPage(page, { scope: "interactive" });
    await page.goto("/agents/threads");
    expect((await initialResponse).ok()).toBeTruthy();

    const main = page.getByRole("main").last();
    await expect(
      main.getByRole("heading", { name: "Threads", level: 1 }),
    ).toBeVisible();

    const searchResponse = waitForThreadsPage(page, {
      scope: "interactive",
      q: WORKSPACE_QUERY,
      limit: "100",
      offset: "0",
    });
    await main.getByPlaceholder("Search by title...").fill(WORKSPACE_QUERY);
    await main.getByRole("button", { name: "Search" }).click();
    expect((await searchResponse).ok()).toBeTruthy();

    const attention = boardColumn(main, "Needs attention");
    const progress = boardColumn(main, "In progress");
    const ready = boardColumn(main, "Ready");
    const done = boardColumn(main, "Done");

    await expect(attention).toContainText(TITLES.attention);
    await expect(attention).toContainText(TITLES.error);
    await expect(attention).toContainText(TITLES.interrupted);
    await expect(attention).toContainText("PR #82 · draft");
    await expect(attention).toContainText("+18 −4");
    await expect(progress).toContainText(TITLES.running);
    await expect(ready).toContainText(TITLES.ready);
    await expect(done).toContainText(TITLES.done);
    await expect(main).not.toContainText(TITLES.dailyScheduled);

    const grouping = main.getByLabel("Group by");
    await grouping.selectOption("status");
    await expect(page).toHaveURL(/group=status/);
    await expect(boardColumn(main, "Finished")).toContainText(TITLES.ready);
    await expect(boardColumn(main, "Running")).toContainText(TITLES.running);
    await expect(boardColumn(main, "Interrupted")).toContainText(
      TITLES.interrupted,
    );
    await expect(boardColumn(main, "Error")).toContainText(TITLES.error);

    await grouping.selectOption("source");
    await expect(boardColumn(main, "GitHub")).toContainText(TITLES.attention);
    await expect(boardColumn(main, "GitHub")).toContainText(TITLES.interrupted);
    await expect(boardColumn(main, "Slack")).toContainText(TITLES.running);
    await expect(boardColumn(main, "Linear")).toContainText(TITLES.error);
    await expect(boardColumn(main, "Dashboard")).toContainText(TITLES.ready);

    await grouping.selectOption("repo");
    await expect(boardColumn(main, "acme/alpha")).toContainText(
      TITLES.attention,
    );
    await expect(boardColumn(main, "acme/beta")).toContainText(TITLES.running);
    await expect(boardColumn(main, "acme/delta")).toContainText(TITLES.error);
    await expect(boardColumn(main, "acme/epsilon")).toContainText(
      TITLES.interrupted,
    );
    await expect(boardColumn(main, "acme/gamma")).toContainText(TITLES.ready);

    await grouping.selectOption("pr");
    await expect(boardColumn(main, "Draft")).toContainText(TITLES.attention);
    await expect(boardColumn(main, "Open")).toContainText(TITLES.ready);
    await expect(boardColumn(main, "Merged")).toContainText(TITLES.done);
    await expect(boardColumn(main, "No pull request")).toContainText(
      TITLES.running,
    );

    const sourceResponse = waitForThreadsPage(page, {
      scope: "interactive",
      q: WORKSPACE_QUERY,
      source: "github",
    });
    await sourceFilter(main).selectOption("github");
    expect((await sourceResponse).ok()).toBeTruthy();
    await expect(main).toContainText(TITLES.attention);
    await expect(main).toContainText(TITLES.interrupted);
    await expect(main).not.toContainText(TITLES.running);

    const resolvedResponse = waitForThreadsPage(page, {
      scope: "interactive",
      q: WORKSPACE_QUERY,
      source: "github",
      resolved: "false",
    });
    await triFilter(main, "Resolved")
      .getByRole("button", { name: "No", exact: true })
      .click();
    expect((await resolvedResponse).ok()).toBeTruthy();
    await expect(main).not.toContainText(TITLES.done);

    const statusResponse = waitForThreadsPage(page, {
      scope: "interactive",
      q: WORKSPACE_QUERY,
      source: "github",
      resolved: "false",
      status: "finished",
    });
    await statusFilter(main).selectOption("finished");
    expect((await statusResponse).ok()).toBeTruthy();
    await expect(main).toContainText(TITLES.attention);
    await expect(main).not.toContainText(TITLES.interrupted);
    await expect(main).not.toContainText(TITLES.error);

    const resetSourceResponse = waitForThreadsPage(page, {
      scope: "interactive",
      q: WORKSPACE_QUERY,
      resolved: "false",
      status: "finished",
    });
    await sourceFilter(main).selectOption("any");
    expect((await resetSourceResponse).ok()).toBeTruthy();

    const resetStatusResponse = waitForThreadsPage(page, {
      scope: "interactive",
      q: WORKSPACE_QUERY,
      resolved: "false",
    });
    await statusFilter(main).selectOption("any");
    expect((await resetStatusResponse).ok()).toBeTruthy();

    await triFilter(main, "Resolved")
      .getByRole("button", { name: "Any", exact: true })
      .click();
    await expect
      .poll(() => new URL(page.url()).searchParams.has("resolved"))
      .toBe(false);

    const viewedResponse = waitForThreadsPage(page, {
      scope: "interactive",
      q: WORKSPACE_QUERY,
      viewed: "true",
    });
    await triFilter(main, "Viewed")
      .getByRole("button", { name: "Yes", exact: true })
      .click();
    expect((await viewedResponse).ok()).toBeTruthy();
    await expect(main).toContainText(TITLES.ready);
    await expect(main).toContainText(TITLES.done);
    await expect(main).not.toContainText(TITLES.attention);
    await expect(main).not.toContainText(TITLES.running);
  });

  test("shows a recency-sorted project list in the sidebar", async ({
    page,
    request,
  }, testInfo) => {
    await seedThreads(request, [
      ...workspaceThreads(),
      ...resolvedOverflowThreads(),
    ]);
    await loginAs(page);
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto("/agents/threads");

    const sidebar = page.locator("[data-sidebar-frame]");
    const workspaceLinks = sidebar.locator(
      `a[href^="/agents/"]:has-text("${WORKSPACE_QUERY}")`,
    );

    await expect
      .poll(() =>
        workspaceLinks.evaluateAll((links) =>
          links.map((link) => link.getAttribute("href")),
        ),
      )
      .toEqual([
        `/agents/${THREAD_IDS.attention}`,
        `/agents/${THREAD_IDS.error}`,
        `/agents/${THREAD_IDS.interrupted}`,
        `/agents/${THREAD_IDS.running}`,
        `/agents/${THREAD_IDS.ready}`,
      ]);

    const betaGroup = sidebar.getByRole("button", {
      name: "beta",
      exact: true,
    });
    await expect(
      sidebar.getByRole("button", { name: "alpha", exact: true }),
    ).toBeVisible();
    await expect(betaGroup).toBeVisible();
    await expect(sidebar.getByText("acme/alpha", { exact: true })).toHaveCount(
      0,
    );

    // Collapsing a project hides only that project's threads.
    await betaGroup.click();
    await expect(betaGroup).toHaveAttribute("aria-expanded", "false");
    await expect(workspaceLinks).toHaveCount(4);
    await expect(
      workspaceLinks.filter({ hasText: TITLES.running }),
    ).toHaveCount(0);
    await betaGroup.click();
    await expect(workspaceLinks).toHaveCount(5);

    await sidebar.getByRole("button", { name: "Projects options" }).click();
    await page
      .getByRole("menuitemcheckbox", { name: "Show archived", exact: true })
      .click();
    await page.keyboard.press("Escape");

    for (const group of ["Needs attention", "In progress", "Ready", "Done"]) {
      await expect(
        sidebar.getByRole("button", { name: group, exact: true }),
      ).toHaveCount(0);
    }
    await expect(sidebar).toContainText("E2E Workspace Resolved overflow 01");
    const loadMore = sidebar.getByRole("button", {
      name: "Load more archived cloud threads",
    });
    await loadMore.click();
    await expect(sidebar).toContainText("E2E Workspace Resolved overflow 20");
    await loadMore.click();
    await expect(sidebar).toContainText("E2E Workspace Resolved overflow 21");
    await expect(sidebar).toContainText(TITLES.done);
    await expect(loadMore).toHaveCount(0);

    const screenshotPath = testInfo.outputPath("unified-thread-sidebar.png");
    await sidebar.screenshot({ path: screenshotPath });
    await testInfo.attach("unified-thread-sidebar", {
      path: screenshotPath,
      contentType: "image/png",
    });
  });

  test("persists layout and column order and resolves threads", async ({
    page,
    request,
  }) => {
    await seedThreads(request, workspaceThreads());
    await loginAs(page);

    const pageResponse = waitForThreadsPage(page, {
      scope: "interactive",
      q: WORKSPACE_QUERY,
    });
    await page.goto(
      `/agents/threads?q=${encodeURIComponent(WORKSPACE_QUERY)}&group=focus`,
    );
    expect((await pageResponse).ok()).toBeTruthy();

    const main = page.getByRole("main").last();
    await main.getByRole("button", { name: "List" }).click();
    await main.getByLabel("Group by").selectOption("source");
    await expect
      .poll(() => {
        const url = new URL(page.url());
        return {
          layout: url.searchParams.get("layout"),
          group: url.searchParams.get("group"),
        };
      })
      .toEqual({ layout: "list", group: "source" });
    await expect(main.locator("article")).toHaveCount(0);

    await page.reload();
    await expect(main.locator("article")).toHaveCount(0);
    const slackGroup = main.locator(
      'section:has(> div > span:text-is("Slack"))',
    );
    const githubGroup = main.locator(
      'section:has(> div > span:text-is("GitHub"))',
    );
    await expect(slackGroup).toContainText(TITLES.running);
    await expect(githubGroup).toContainText(TITLES.attention);
    await expect(githubGroup).toContainText(TITLES.interrupted);
    await expect(slackGroup).not.toContainText(TITLES.attention);

    await main.getByRole("button", { name: "Board" }).click();
    await expect
      .poll(() => new URL(page.url()).searchParams.get("layout"))
      .toBe("board");
    await expect(main.locator("article").first()).toBeVisible();
    await main.getByLabel("Group by").selectOption("focus");
    await expect
      .poll(() => new URL(page.url()).searchParams.get("group"))
      .toBe("focus");
    await expectBoardOrder(main, [
      "Needs attention",
      "In progress",
      "Ready",
      "Done",
    ]);
    await main
      .getByRole("button", { name: "Move Needs attention right" })
      .click();

    await expect
      .poll(() => new URL(page.url()).searchParams.get("order"))
      .toBe("progress|attention|ready|done");
    await expectBoardOrder(main, [
      "In progress",
      "Needs attention",
      "Ready",
      "Done",
    ]);

    const doneHeader = boardColumn(main, "Done").locator('[draggable="true"]');
    await doneHeader.dragTo(boardColumn(main, "Needs attention"));
    await expectBoardOrder(main, [
      "In progress",
      "Done",
      "Needs attention",
      "Ready",
    ]);
    await expect
      .poll(() => new URL(page.url()).searchParams.get("order"))
      .toBe("progress|done|attention|ready");

    await page.reload();
    await expectBoardOrder(main, [
      "In progress",
      "Done",
      "Needs attention",
      "Ready",
    ]);
    expect(
      await page.evaluate(() =>
        window.localStorage.getItem("open-swe:thread-board-order:focus"),
      ),
    ).toBe("progress|done|attention|ready");

    await page.goto(
      `/agents/threads?q=${encodeURIComponent(WORKSPACE_QUERY)}&layout=board&group=focus`,
    );
    await expectBoardOrder(main, [
      "In progress",
      "Done",
      "Needs attention",
      "Ready",
    ]);

    const readyCard = boardColumn(main, "Ready")
      .locator("article")
      .filter({ hasText: TITLES.ready });
    const resolveResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname ===
          `/dashboard/api/threads/${THREAD_IDS.ready}/resolve`,
    );
    await readyCard.getByRole("button", { name: "Resolve thread" }).click();
    expect((await resolveResponse).ok()).toBeTruthy();
    await expect(boardColumn(main, "Done")).toContainText(TITLES.ready);
    await expect(boardColumn(main, "Ready")).toHaveCount(0);

    const resolvedCard = boardColumn(main, "Done")
      .locator("article")
      .filter({ hasText: TITLES.ready });
    const reopenResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname ===
          `/dashboard/api/threads/${THREAD_IDS.ready}/resolve`,
    );
    await resolvedCard.getByRole("button", { name: "Reopen thread" }).click();
    expect((await reopenResponse).ok()).toBeTruthy();
    await expect(boardColumn(main, "Ready")).toContainText(TITLES.ready);
  });

  test("paginates the real list endpoint", async ({ page, request }) => {
    await seedThreads(request, paginationThreads());
    await loginAs(page);

    const firstPageResponse = waitForThreadsPage(page, {
      scope: "interactive",
      q: "E2E Pagination",
      limit: "25",
      offset: "0",
    });
    await page.goto(
      "/agents/threads?layout=list&group=none&q=E2E%20Pagination",
    );
    expect((await firstPageResponse).ok()).toBeTruthy();

    const main = page.getByRole("main").last();
    await expect(main.getByText("1–25+", { exact: true })).toBeVisible();
    await expect(main).toContainText("E2E Pagination thread 01");
    await expect(main).not.toContainText("E2E Pagination thread 26");

    const secondPageResponse = waitForThreadsPage(page, {
      scope: "interactive",
      q: "E2E Pagination",
      limit: "25",
      offset: "25",
    });
    await main.getByRole("button", { name: "Next" }).click();
    expect((await secondPageResponse).ok()).toBeTruthy();
    await expect(main.getByText("Page 2", { exact: true })).toBeVisible();
    await expect(main.getByText("26–26", { exact: true })).toBeVisible();
    await expect(main).toContainText("E2E Pagination thread 26");
    await expect(main.getByRole("button", { name: "Next" })).toBeDisabled();

    await main.getByRole("button", { name: "Prev" }).click();
    await expect(main.getByText("Page 1", { exact: true })).toBeVisible();
    await expect(main).toContainText("E2E Pagination thread 01");
  });
});

test.describe("automation run history", () => {
  test("retries failures and scopes global and per-automation runs", async ({
    page,
    request,
  }) => {
    await deleteScheduleThreads(request, SCHEDULE_IDS.daily);
    await deleteScheduleThreads(request, SCHEDULE_IDS.weekly);
    await seedThreads(request, [...workspaceThreads(), ...automationThreads()]);
    await seedSchedules(request);
    await loginAs(page);

    const triggerResponse = await page.request.post(
      `/dashboard/api/schedules/${SCHEDULE_IDS.daily}/trigger`,
      { headers: SAME_ORIGIN_HEADERS },
    );
    expect(triggerResponse.ok(), await triggerResponse.text()).toBeTruthy();
    const triggered = (await triggerResponse.json()) as {
      status: string;
      thread_id: string;
      run_id: string;
    };
    expect(triggered.status).toBe("started");
    createdThreadIds.add(triggered.thread_id);

    const producedHistoryResponse = await page.request.get(
      `/dashboard/api/threads/page?scope=automation&automation_id=${SCHEDULE_IDS.daily}&limit=100&offset=0`,
    );
    expect(
      producedHistoryResponse.ok(),
      await producedHistoryResponse.text(),
    ).toBeTruthy();
    const producedHistory = (await producedHistoryResponse.json()) as {
      items: Array<{
        id: string;
        title: string;
        triggerKind: string;
        automationId: string;
      }>;
    };
    expect(producedHistory.items).toContainEqual(
      expect.objectContaining({
        id: triggered.thread_id,
        title: "Test: E2E Daily Health",
        triggerKind: "schedule_test",
        automationId: SCHEDULE_IDS.daily,
      }),
    );

    let failAutomationRuns = true;
    await page.route("**/dashboard/api/threads/page?*", async (route) => {
      const url = new URL(route.request().url());
      if (
        failAutomationRuns &&
        url.searchParams.get("scope") === "automation"
      ) {
        await route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify({ detail: "E2E transient failure" }),
        });
        return;
      }
      await route.continue();
    });

    await page.goto("/agents/automations?tab=runs");
    const automations = page
      .getByRole("heading", { name: "Automations", level: 1 })
      .locator("..");
    await expect(
      automations.getByText("Automation runs could not be loaded."),
    ).toBeVisible({ timeout: 20_000 });

    failAutomationRuns = false;
    const retryResponse = waitForThreadsPage(page, {
      scope: "automation",
      limit: "100",
      offset: "0",
    });
    await automations.getByRole("button", { name: "Retry" }).click();
    expect((await retryResponse).ok()).toBeTruthy();

    const daily = automations.locator(
      'section:has(h2:text-is("E2E Daily Health"))',
    );
    const weekly = automations.locator(
      'section:has(h2:text-is("E2E Weekly Cleanup"))',
    );
    await expect(daily.getByRole("link")).toHaveCount(3);
    await expect(weekly.getByRole("link")).toHaveCount(1);
    const producedRun = daily.locator(
      `a[href="/agents/${triggered.thread_id}"]`,
    );
    await expect(producedRun).toContainText("Test: E2E Daily Health");
    await expect(producedRun).toContainText("Test run");

    const scheduledRun = daily.getByRole("link").filter({
      hasText: TITLES.dailyScheduled,
    });
    const testRun = daily.getByRole("link").filter({
      hasText: TITLES.dailyTest,
    });
    await expect(scheduledRun).toContainText("Finished");
    await expect(scheduledRun).toContainText("Scheduled run");
    await expect(scheduledRun).toContainText("Posted to Slack");
    await expect(scheduledRun).toContainText("acme/alpha");
    await expect(testRun).toContainText("Error");
    await expect(testRun).not.toContainText("Posted to Slack");
    await expect(testRun).toContainText("Test run");
    await expect(weekly).toContainText("Running");
    await expect(automations).not.toContainText(TITLES.attention);

    await automations.getByRole("button", { name: "Overview" }).click();
    const scheduleLink = automations.getByRole("link", {
      name: /E2E Daily Health/,
    });
    await expect(scheduleLink).toBeVisible();

    const recentRunsResponse = waitForThreadsPage(page, {
      scope: "automation",
      automation_id: SCHEDULE_IDS.daily,
      limit: "10",
      offset: "0",
    });
    await scheduleLink.click();
    expect((await recentRunsResponse).ok()).toBeTruthy();
    await expect(page).toHaveURL(
      new RegExp(`/agents/automations/${SCHEDULE_IDS.daily}$`),
    );
    await expect(
      page.getByRole("heading", { name: "Recent runs", level: 2 }),
    ).toBeVisible();
    await expect(page.getByText(TITLES.dailyScheduled)).toBeVisible();
    await expect(page.getByText(TITLES.dailyTest)).toBeVisible();
    await expect(page.getByText(TITLES.weeklyRunning)).toHaveCount(0);
    const recentProducedRun = page.locator(
      `a[href="/agents/${triggered.thread_id}"]`,
    );
    await expect(recentProducedRun).toContainText("Test: E2E Daily Health");

    await recentProducedRun.click();
    await expect(page).toHaveURL(new RegExp(`/agents/${triggered.thread_id}$`));
  });
});
