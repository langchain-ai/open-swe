import {
  expect,
  test,
  type APIRequestContext,
  type Locator,
  type Page,
  type Route,
} from "@playwright/test";

import { loginAs, SAME_ORIGIN_HEADERS, SAME_USER } from "./helpers/dashboard";

const QUICK = { timeout: 3_000 };
const PAGE_LOAD = { timeout: 10_000 };
const FAILURE_DETAIL = "E2E forced save failure";

type InstanceSettings = Record<string, unknown> & {
  fable_enabled?: boolean | null;
  pr_summaries?: boolean | null;
};

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

const failedRequestIds: Array<string> = [];

async function failWith500(route: Route) {
  failedRequestIds.push(route.request().headers()["x-request-id"] ?? "");
  await route.fulfill({ status: 500, json: { detail: FAILURE_DETAIL } });
}

function watchErrorReport(page: Page) {
  return page.waitForResponse(
    (res) =>
      res.url().endsWith("/dashboard/api/client-errors") &&
      res.request().method() === "POST",
    QUICK,
  );
}

/** The toast, the RUM-bound report, and the server log all carry the failed request's ID. */
async function expectReportedFailure(
  page: Page,
  title: string,
  report: ReturnType<typeof watchErrorReport>,
) {
  const requestId = failedRequestIds.at(-1);
  expect(requestId).toMatch(/^req_[0-9a-f-]{36}$/);
  const toast = page.locator("[data-sonner-toast]").filter({ hasText: title });
  await expect(toast).toContainText(FAILURE_DETAIL, QUICK);
  await expect(toast).toContainText(`ID ${requestId}`, QUICK);
  const response = await report;
  expect(response.status()).toBe(204);
  expect(response.request().postDataJSON()).toMatchObject({
    error_id: requestId,
    title,
    error_message: FAILURE_DETAIL,
    status: 500,
  });
}

async function readInstanceSettings(
  request: APIRequestContext,
): Promise<InstanceSettings> {
  const res = await request.get("/dashboard/api/settings");
  expect(res.ok(), await res.text()).toBeTruthy();
  return (await res.json()) as InstanceSettings;
}

async function writeInstanceSettings(
  request: APIRequestContext,
  body: InstanceSettings,
) {
  const res = await request.put("/dashboard/api/settings", {
    data: body,
    headers: SAME_ORIGIN_HEADERS,
  });
  expect(res.ok(), await res.text()).toBeTruthy();
}

function settingSwitch(page: Page, label: string): Locator {
  return page
    .locator('div[class*="py-3.5"]')
    .filter({ has: page.getByText(label, { exact: true }) })
    .getByRole("switch");
}

test.describe("optimistic settings saves", () => {
  let original: InstanceSettings | null = null;

  test.beforeEach(async ({ page }) => {
    await loginAs(page, SAME_USER);
    original = await readInstanceSettings(page.request);
    await writeInstanceSettings(page.request, {
      ...original,
      fable_enabled: false,
      pr_summaries: false,
    });
  });

  test.afterEach(async ({ page }) => {
    if (original) await writeInstanceSettings(page.request, original);
    original = null;
  });

  test("two sections saved back to back both land", async ({ page }) => {
    const firstPutGate = deferred();
    const puts: Array<InstanceSettings> = [];
    await page.route("**/dashboard/api/settings", async (route) => {
      if (route.request().method() !== "PUT") return route.continue();
      puts.push(route.request().postDataJSON() as InstanceSettings);
      if (puts.length === 1) await firstPutGate.promise;
      await route.continue();
    });

    await page.goto("/admin");
    const fable = settingSwitch(page, "Allow Fable models");
    const summaries = settingSwitch(page, "PR Summaries");
    await expect(fable).not.toBeChecked();
    await expect(summaries).not.toBeChecked();

    await fable.click();
    await expect(fable).toBeChecked(QUICK);
    await expect.poll(() => puts.length, QUICK).toBe(1);

    await summaries.click();
    await expect(summaries).toBeChecked(QUICK);
    await expect(fable).toBeChecked(QUICK);
    await expect(fable).toBeEnabled(QUICK);
    await expect(summaries).toBeEnabled(QUICK);
    expect(puts).toHaveLength(1);

    firstPutGate.resolve();
    await expect.poll(() => puts.length, QUICK).toBe(2);
    expect(puts[1]).toMatchObject({ fable_enabled: true, pr_summaries: true });

    await expect
      .poll(async () => {
        const saved = await readInstanceSettings(page.request);
        return [saved.fable_enabled, saved.pr_summaries];
      }, QUICK)
      .toEqual([true, true]);
    await page.reload();
    await expect(settingSwitch(page, "Allow Fable models")).toBeChecked(
      PAGE_LOAD,
    );
    await expect(settingSwitch(page, "PR Summaries")).toBeChecked(QUICK);
  });

  test("a failed save reverts only its own switch", async ({ page }) => {
    const firstPutGate = deferred();
    const puts: Array<InstanceSettings> = [];
    await page.route("**/dashboard/api/settings", async (route) => {
      if (route.request().method() !== "PUT") return route.continue();
      puts.push(route.request().postDataJSON() as InstanceSettings);
      if (puts.length === 1) {
        await firstPutGate.promise;
        return failWith500(route);
      }
      await route.continue();
    });

    await page.goto("/admin");
    const fable = settingSwitch(page, "Allow Fable models");
    const summaries = settingSwitch(page, "PR Summaries");
    await expect(fable).not.toBeChecked();

    await fable.click();
    await expect(fable).toBeChecked(QUICK);
    await expect.poll(() => puts.length, QUICK).toBe(1);
    await summaries.click();
    await expect(summaries).toBeChecked(QUICK);

    const report = watchErrorReport(page);
    firstPutGate.resolve();
    await expect(fable).not.toBeChecked(QUICK);
    await expectReportedFailure(page, "Couldn't save settings", report);
    await expect(summaries).toBeChecked(QUICK);
    await expect.poll(() => puts.length, QUICK).toBe(2);
    expect(puts[1]).toMatchObject({
      fable_enabled: false,
      pr_summaries: true,
    });

    await expect
      .poll(async () => {
        const saved = await readInstanceSettings(page.request);
        return [saved.fable_enabled, saved.pr_summaries];
      }, QUICK)
      .toEqual([false, true]);
    await page.reload();
    await expect(settingSwitch(page, "Allow Fable models")).toBeEnabled(
      PAGE_LOAD,
    );
    await expect(settingSwitch(page, "Allow Fable models")).not.toBeChecked(
      QUICK,
    );
    await expect(settingSwitch(page, "PR Summaries")).toBeChecked(QUICK);
  });
});

const THREAD_ID = "76000000-0000-4000-8000-000000000001";
const THREAD_TITLE = "E2E Optimistic seeded thread";

async function removeFixtureThread(request: APIRequestContext) {
  const pin = await request.delete("/store/items", {
    data: { namespace: ["thread_pins", SAME_USER.login], key: THREAD_ID },
  });
  expect([200, 204, 404]).toContain(pin.status());
  const thread = await request.delete(`/threads/${THREAD_ID}`);
  expect([200, 204, 404]).toContain(thread.status());
}

async function seedThread(request: APIRequestContext) {
  await removeFixtureThread(request);
  const now = Date.now();
  const res = await request.post("/threads", {
    data: {
      thread_id: THREAD_ID,
      if_exists: "raise",
      metadata: {
        participant_logins: { [SAME_USER.login]: true },
        title: THREAD_TITLE,
        source: "dashboard",
        origin: "dashboard",
        thread_category: "interactive",
        trigger_kind: "user",
        repo_owner: "acme",
        repo_name: "alpha",
        base_branch: "main",
        branch_name: "open-swe/e2e-optimistic",
        created_at_ms: now - 60_000,
        updated_at_ms: now,
        latest_run_id: "e2e-run-optimistic",
        latest_run_status: "success",
      },
    },
  });
  expect(res.ok(), await res.text()).toBeTruthy();
}

function sidebarSection(page: Page, name: string): Locator {
  return page
    .locator("aside")
    .getByRole("button", { name, exact: true })
    .locator("..")
    .locator("..");
}

async function holdOnce(
  page: Page,
  matches: (method: string, path: string) => boolean,
  answer: (route: Route) => Promise<void>,
) {
  const gate = deferred();
  const seen = { count: 0 };
  const settled = deferred();
  await page.route("**/dashboard/api/threads/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (!matches(request.method(), path) || seen.count > 0) {
      return route.continue();
    }
    seen.count += 1;
    await gate.promise;
    await answer(route);
    settled.resolve();
  });
  return { release: gate.resolve, seen, settled: settled.promise };
}

test.describe("optimistic thread edits", () => {
  test.beforeEach(async ({ page }) => {
    await seedThread(page.request);
    await loginAs(page, SAME_USER);
  });

  test.afterEach(async ({ page }) => {
    await removeFixtureThread(page.request);
  });

  const isPin = (method: string, path: string) =>
    method === "POST" && path === `/dashboard/api/threads/${THREAD_ID}/pin`;
  const isRename = (method: string, path: string) =>
    method === "PATCH" && path === `/dashboard/api/threads/${THREAD_ID}`;

  async function pinFromSidebar(page: Page) {
    const row = page
      .locator("aside")
      .getByRole("link", { name: THREAD_TITLE })
      .first();
    await expect(row).toBeVisible();
    await row.hover();
    await row.getByRole("button", { name: "Pin thread" }).click();
  }

  test("pinning shows at once and persists", async ({ page }) => {
    const pin = await holdOnce(page, isPin, (route) => route.continue());
    await page.goto(`/agents/${THREAD_ID}`);
    await pinFromSidebar(page);

    const pinned = sidebarSection(page, "Pinned");
    await expect(pinned.getByRole("link", { name: THREAD_TITLE })).toBeVisible(
      QUICK,
    );
    expect(pin.seen.count).toBe(1);

    pin.release();
    await pin.settled;
    await page.reload();
    await expect(
      sidebarSection(page, "Pinned").getByRole("link", { name: THREAD_TITLE }),
    ).toBeVisible(PAGE_LOAD);
  });

  test("a failed pin reverts", async ({ page }) => {
    const pin = await holdOnce(page, isPin, failWith500);
    await page.goto(`/agents/${THREAD_ID}`);
    await pinFromSidebar(page);

    const pinned = sidebarSection(page, "Pinned");
    await expect(pinned.getByRole("link", { name: THREAD_TITLE })).toBeVisible(
      QUICK,
    );

    const report = watchErrorReport(page);
    pin.release();
    await pin.settled;
    await expect(sidebarSection(page, "Pinned")).toHaveCount(0, QUICK);
    await expectReportedFailure(page, "Couldn't pin or unpin thread", report);
    await page.reload();
    await expect(
      page.locator("aside").getByRole("link", { name: THREAD_TITLE }).first(),
    ).toBeVisible(PAGE_LOAD);
    await expect(sidebarSection(page, "Pinned")).toHaveCount(0, QUICK);
  });

  async function renameTo(page: Page, next: string) {
    const titleButton = page.getByRole("button", { name: "Rename thread" });
    await expect(titleButton).toHaveText(THREAD_TITLE);
    await titleButton.click();
    const input = page.getByRole("textbox", { name: "Thread title" });
    await input.fill(next);
    await input.press("Enter");
  }

  test("renaming shows the new title at once and persists", async ({
    page,
  }) => {
    const next = "E2E Optimistic renamed thread";
    const rename = await holdOnce(page, isRename, (route) => route.continue());
    await page.goto(`/agents/${THREAD_ID}`);
    await renameTo(page, next);

    const titleButton = page.getByRole("button", { name: "Rename thread" });
    await expect(titleButton).toHaveText(next, QUICK);
    await expect(
      page.locator("aside").getByRole("link", { name: next }).first(),
    ).toBeVisible(QUICK);
    expect(rename.seen.count).toBe(1);

    rename.release();
    await rename.settled;
    await page.reload();
    await expect(
      page.getByRole("button", { name: "Rename thread" }),
    ).toHaveText(next, PAGE_LOAD);
    await expect(
      page.locator("aside").getByRole("link", { name: next }).first(),
    ).toBeVisible(PAGE_LOAD);
  });

  test("a failed rename restores the old title", async ({ page }) => {
    const next = "E2E Optimistic failed rename";
    const rename = await holdOnce(page, isRename, failWith500);
    await page.goto(`/agents/${THREAD_ID}`);
    await renameTo(page, next);

    const titleButton = page.getByRole("button", { name: "Rename thread" });
    await expect(titleButton).toHaveText(next, QUICK);
    await expect(
      page.locator("aside").getByRole("link", { name: next }).first(),
    ).toBeVisible(QUICK);

    const report = watchErrorReport(page);
    rename.release();
    await rename.settled;
    await expect(titleButton).toHaveText(THREAD_TITLE, QUICK);
    await expectReportedFailure(page, "Couldn't rename thread", report);
    await expect(
      page.locator("aside").getByRole("link", { name: THREAD_TITLE }).first(),
    ).toBeVisible(QUICK);
    await expect(
      page.locator("aside").getByRole("link", { name: next }),
    ).toHaveCount(0, QUICK);
  });
});
