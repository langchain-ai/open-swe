import {
  expect,
  test,
  type APIRequestContext,
  type Page,
  type Playwright,
} from "@playwright/test";

// The HITL PR-media flow end to end: a pending upload request lands on the
// thread page, only the thread owner (Alice) can approve it, approving runs
// the upload against fake GitHub exactly once, and replay conflicts. Bob can
// read but never decide.

const ALICE = { login: "alice", email: "alice@example.com" };
const BOB = { login: "bob", email: "bob@example.com" };

const harness = `http://127.0.0.1:${process.env.E2E_PORT ?? 2024}`;
const SAME_ORIGIN_HEADERS = { origin: harness, referer: `${harness}/` };

// A tiny valid PNG (1x1 transparent pixel).
const PNG_BASE64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

async function seedOwnedThread(
  api: APIRequestContext,
  ownerLogin: string,
): Promise<string> {
  const threadId = crypto.randomUUID();
  const seed = await api.post(`${harness}/threads`, {
    data: {
      thread_id: threadId,
      metadata: {
        title: "Media approval fixture",
        source: "dashboard",
        owner_login: ownerLogin,
        visibility: "public",
        graph_id: "agent",
      },
    },
  });
  expect(seed.ok()).toBeTruthy();
  return threadId;
}

async function loginApi(
  playwright: Playwright,
  user: { login: string; email: string },
): Promise<APIRequestContext> {
  const api = await playwright.request.newContext({ baseURL: harness });
  const res = await api.post("/control/login", { data: user });
  expect(res.ok()).toBeTruthy();
  return api;
}

async function createMediaRequest(
  api: APIRequestContext,
  threadId: string,
): Promise<{ fingerprint: string; pullNumber: number }> {
  // A distinct target PR per test keeps the content-addressed fingerprint
  // unique: identical bytes + repo + PR would legitimately dedupe. The fake
  // GitHub lives on the harness origin only, so this must not go through the
  // UI server.
  const prRes = await api.post(`${harness}/fake-gh/repos/fakeorg/demo/pulls`, {
    data: {
      head: `fixture-${crypto.randomUUID().slice(0, 8)}`,
      base: "main",
      title: `Media target ${crypto.randomUUID().slice(0, 8)}`,
      body: "",
      draft: false,
    },
  });
  expect(prRes.status(), await prRes.text()).toBe(201);
  const pullNumber = (await prRes.json()).number as number;
  expect(pullNumber).toBeGreaterThan(0);

  const create = await api.post(
    `${harness}/dashboard/api/pr-media/${threadId}/requests`,
    {
      headers: SAME_ORIGIN_HEADERS,
      data: {
        owner: "fakeorg",
        repo: "demo",
        pull_number: pullNumber,
        file_name: `pixel-${pullNumber}.png`,
        media_base64: PNG_BASE64,
      },
    },
  );
  expect(create.ok(), await create.text()).toBeTruthy();
  const fingerprint = ((await create.json()).request.fingerprint ??
    "") as string;
  expect(fingerprint).toMatch(/^[0-9a-f]{64}$/);
  return { fingerprint, pullNumber };
}

async function pixelUploads(
  api: APIRequestContext,
  pullNumber: number,
): Promise<Array<{ name: string }>> {
  const res = await api.get(`${harness}/control/media-uploads`);
  const list = (await res.json()).uploads as Array<{ name: string }>;
  return list.filter((u) => u.name === `pixel-${pullNumber}.png`);
}

test.beforeEach(async ({ playwright }) => {
  const api = await playwright.request.newContext({ baseURL: harness });
  await api.post("/control/reset");
  await api.dispose();
});

test("owner approves the exact media request from the thread page", async ({
  page,
  playwright,
}) => {
  const api = await loginApi(playwright, ALICE);
  const threadId = await seedOwnedThread(api, ALICE.login);
  const { pullNumber } = await createMediaRequest(api, threadId);

  await page.request.post("/control/login", { data: ALICE });
  await page.goto(`/agents/${threadId}`);
  const card = page.getByTestId("media-approval-card");
  await expect(card).toBeVisible();
  await expect(card).toContainText(`Approve attaching pixel-${pullNumber}.png`);
  await expect(card).toContainText(`fakeorg/demo#${pullNumber}`);
  await expect(page.getByTestId("media-approval-preview")).toBeVisible();

  await page.getByTestId("media-approve").click();
  await expect(card).toBeHidden({ timeout: 30_000 });

  expect(await pixelUploads(api, pullNumber)).toHaveLength(1);
});

test("screenshot: the approval card as Alice, denied as Bob", async ({
  page,
  playwright,
  browser,
}, testInfo) => {
  const api = await loginApi(playwright, ALICE);
  const threadId = await seedOwnedThread(api, ALICE.login);
  const { pullNumber } = await createMediaRequest(api, threadId);

  await page.request.post("/control/login", { data: ALICE });
  await page.goto(`/agents/${threadId}`);
  const card = page.getByTestId("media-approval-card");
  await expect(card).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("media-approval-alice.png"),
    fullPage: false,
  });

  // Bob reads the same public thread but has no decision to take.
  const bobContext = await browser.newContext();
  const bobPage = await bobContext.newPage();
  await bobPage.request.post(`${harness}/control/login`, { data: BOB });
  await bobPage.goto(
    `http://127.0.0.1:${process.env.E2E_UI_PORT ?? 3100}/agents/${threadId}`,
  );
  await bobPage.waitForTimeout(3000);
  await bobPage.screenshot({
    path: testInfo.outputPath("media-approval-bob.png"),
    fullPage: false,
  });
  await bobContext.close();

  await card.screenshot({
    path: testInfo.outputPath("media-approval-card.png"),
  });
});

test("non-owner cannot claim the approval through the API", async ({
  playwright,
}) => {
  const api = await loginApi(playwright, ALICE);
  const threadId = await seedOwnedThread(api, ALICE.login);
  const { fingerprint, pullNumber } = await createMediaRequest(api, threadId);

  const asBob = await loginApi(playwright, BOB);
  const denied = await asBob.post(
    `${harness}/dashboard/api/pr-media/${threadId}/${fingerprint}/approve`,
    { headers: SAME_ORIGIN_HEADERS },
  );
  expect(denied.status()).toBe(404);
  expect(await pixelUploads(api, pullNumber)).toHaveLength(0);
});

test("replaying an approval after the decision conflicts without re-uploading", async ({
  playwright,
}) => {
  const api = await loginApi(playwright, ALICE);
  const threadId = await seedOwnedThread(api, ALICE.login);
  const { fingerprint, pullNumber } = await createMediaRequest(api, threadId);

  const first = await api.post(
    `${harness}/dashboard/api/pr-media/${threadId}/${fingerprint}/approve`,
    { headers: SAME_ORIGIN_HEADERS },
  );
  expect(first.ok(), await first.text()).toBeTruthy();
  expect((await first.json()).request.status).toBe("completed");

  const again = await api.post(
    `${harness}/dashboard/api/pr-media/${threadId}/${fingerprint}/approve`,
    { headers: SAME_ORIGIN_HEADERS },
  );
  expect(again.status()).toBe(409);
  expect(await pixelUploads(api, pullNumber)).toHaveLength(1);
});
