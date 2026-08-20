import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

const USER = {
  login: "thread-tools-e2e",
  email: "thread-tools-e2e@example.com",
};
const TARGET_THREAD_ID = "75000000-0000-4000-8000-000000000001";
const TARGET_TITLE = "E2E Thread Tools Target";
const BASE_URL = `http://127.0.0.1:${process.env.E2E_PORT ?? 2024}`;
const SAME_ORIGIN_HEADERS = { origin: BASE_URL, referer: `${BASE_URL}/` };

async function loginAs(page: Page) {
  const response = await page.request.post("/control/login", { data: USER });
  expect(response.ok()).toBeTruthy();
}

async function saveDefaultModel(page: Page) {
  const optionsResponse = await page.request.get("/dashboard/api/options");
  expect(optionsResponse.ok()).toBeTruthy();
  const options = (await optionsResponse.json()) as {
    default_agent_model: string;
    default_agent_reasoning_effort: string;
  };
  const response = await page.request.put("/dashboard/api/profile", {
    headers: SAME_ORIGIN_HEADERS,
    data: {
      default_model: options.default_agent_model,
      reasoning_effort: options.default_agent_reasoning_effort,
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
}

async function purgeOwnedThreads(request: APIRequestContext) {
  for (;;) {
    const searchResponse = await request.post("/threads/search", {
      data: { metadata: { github_login: USER.login }, limit: 100, offset: 0 },
    });
    expect(searchResponse.ok(), await searchResponse.text()).toBeTruthy();
    const threads = (await searchResponse.json()) as Array<{
      thread_id: string;
    }>;
    if (threads.length === 0) return;
    for (const thread of threads) {
      const deleteResponse = await request.delete(
        `/threads/${thread.thread_id}`,
      );
      expect([200, 204, 404]).toContain(deleteResponse.status());
    }
  }
}

async function seedTargetThread(request: APIRequestContext) {
  await purgeOwnedThreads(request);
  const deleteResponse = await request.delete(`/threads/${TARGET_THREAD_ID}`);
  expect([200, 204, 404]).toContain(deleteResponse.status());
  const now = Date.now();
  const response = await request.post("/threads", {
    data: {
      thread_id: TARGET_THREAD_ID,
      if_exists: "raise",
      metadata: {
        github_login: USER.login,
        triggering_user_email: USER.email,
        title: TARGET_TITLE,
        source: "dashboard",
        origin: "dashboard",
        thread_category: "interactive",
        trigger_kind: "user",
        repo_owner: "fakeorg",
        repo_name: "demo",
        base_branch: "main",
        branch_name: "open-swe/e2e-thread-tools-target",
        created_at_ms: now - 60_000,
        updated_at_ms: now - 30_000,
        latest_run_id: "e2e-thread-tools-target-run",
        latest_run_status: "success",
      },
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
}

async function typeIntoComposer(page: Page, text: string) {
  const editor = page.getByTestId("composer-editor");
  await expect(editor).toBeVisible();
  await editor.click();
  await editor.pressSequentially(text);
  await editor.press("Enter");
}

async function successfulThreadTools(
  request: APIRequestContext,
  threadId: string,
): Promise<Record<string, boolean>> {
  const response = await request.get(`/threads/${threadId}/state`);
  if (!response.ok()) return {};
  const state = (await response.json()) as {
    values?: {
      messages?: Array<{
        type?: string;
        name?: string;
        content?: unknown;
      }>;
    };
  };
  const successes: Record<string, boolean> = {};
  for (const message of state.values?.messages ?? []) {
    if (message.type !== "tool" || !message.name) continue;
    let payload = message.content;
    if (typeof payload === "string") {
      try {
        payload = JSON.parse(payload);
      } catch {
        payload = null;
      }
    }
    if (!payload || typeof payload !== "object") continue;
    const record = payload as Record<string, unknown>;
    if (record.success !== true) {
      successes[message.name] = false;
      continue;
    }
    if (message.name === "list_threads") {
      const items = Array.isArray(record.items) ? record.items : [];
      successes[message.name] = items.some(
        (item) =>
          item &&
          typeof item === "object" &&
          (item as { id?: unknown }).id === TARGET_THREAD_ID,
      );
    } else {
      const thread = record.thread;
      successes[message.name] =
        Boolean(thread) &&
        typeof thread === "object" &&
        (thread as { id?: unknown }).id === TARGET_THREAD_ID;
      if (message.name === "manage_thread") {
        successes[message.name] =
          successes[message.name] &&
          (thread as { resolved?: unknown }).resolved === true;
      }
    }
  }
  return successes;
}

function threadsUrl(resolved: boolean) {
  return `/agents/threads?resolved=${resolved}&q=${encodeURIComponent(TARGET_TITLE)}&layout=list&group=none`;
}

test.afterEach(async ({ request }) => {
  await purgeOwnedThreads(request);
});

test("agent thread tools update the real threads UI", async ({ page }) => {
  await loginAs(page);
  await saveDefaultModel(page);
  await seedTargetThread(page.request);

  await page.goto(threadsUrl(false));
  const activeMain = page.locator("main");
  const activeTarget = activeMain
    .getByRole("link", { name: new RegExp(TARGET_TITLE) })
    .first();
  await expect(activeTarget).toBeVisible();
  const activeRow = activeTarget.locator("..");
  await expect(
    activeRow.getByRole("button", { name: "Resolve thread" }),
  ).toBeVisible();
  await expect(activeRow).not.toContainText("Resolved");

  await page.goto("/agents");
  await typeIntoComposer(
    page,
    `E2E_THREAD_TOOLS:${TARGET_THREAD_ID} inspect and resolve the seeded fixture thread`,
  );
  await expect(page).toHaveURL(/\/agents\/[0-9a-f-]+$/);
  const controllerThreadId =
    new URL(page.url()).pathname.split("/").pop() ?? "";
  expect(controllerThreadId).not.toBe("");

  await expect(
    page.getByText("Resolved the target thread through the thread tools."),
  ).toBeVisible({ timeout: 60_000 });
  const worked = page.getByRole("button", {
    name: /^Worked(?: for .+| · \d+ actions?)?$/,
  });
  await expect(worked).toBeVisible();
  await worked.click();
  await expect(
    page.getByRole("button", { name: "Manage thread" }),
  ).toBeVisible();
  await page.getByRole("button", { name: /previous tool calls$/ }).click();
  await expect(
    page.getByRole("button", { name: "List threads" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Get thread" })).toBeVisible();
  await expect
    .poll(() => successfulThreadTools(page.request, controllerThreadId), {
      timeout: 30_000,
    })
    .toEqual({
      list_threads: true,
      get_thread: true,
      manage_thread: true,
    });

  await page.goto(threadsUrl(true));
  const resolvedMain = page.locator("main");
  const resolvedTarget = resolvedMain
    .getByRole("link", { name: new RegExp(TARGET_TITLE) })
    .first();
  await expect(resolvedTarget).toBeVisible();
  const resolvedRow = resolvedTarget.locator("..");
  await expect(resolvedRow).toContainText("Resolved");
  await expect(
    resolvedRow.getByRole("button", { name: "Reopen thread" }),
  ).toBeVisible();
});
