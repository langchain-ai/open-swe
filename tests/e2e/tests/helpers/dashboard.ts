import { expect, type Page } from "@playwright/test";

// Shared fixtures for the specs that drive the REAL built ui/ app (served
// same-origin from the harness). Only the LLM/GitHub/Slack/token boundaries
// are faked.
export const SAME_USER = {
  login: "alice",
  email: "alice@example.com",
  name: "Alice",
};
export const OTHER_USER = {
  login: "bob",
  email: "bob@example.com",
  name: "Bob",
};

// The dashboard's mutating routes enforce same-origin, which a browser sets for
// itself but APIRequestContext does not.
const BASE_URL = `http://127.0.0.1:${process.env.E2E_PORT ?? 2024}`;
export const SAME_ORIGIN_HEADERS = {
  origin: BASE_URL,
  referer: `${BASE_URL}/`,
};

export async function loginAs(
  page: Page,
  user: { login: string; email: string },
) {
  const res = await page.request.post("/control/login", { data: user });
  expect(res.ok()).toBeTruthy();
  await setTranscriptStreaming(page, true);
}

// Reading a thread from the transcript event log is opt-in per user while it
// rolls out, and the suite asserts that path, so signing in turns it on. Pass
// `false` to cover a user who has not opted in and reads LangGraph state.
export async function setTranscriptStreaming(page: Page, enabled: boolean) {
  const current = await page.request.get("/dashboard/api/me/preferences");
  expect(current.ok(), await current.text()).toBeTruthy();
  const preferences = (await current.json()) as Record<string, unknown>;
  const saved = await page.request.put("/dashboard/api/me/preferences", {
    headers: SAME_ORIGIN_HEADERS,
    data: { ...preferences, transcript_streaming: enabled },
  });
  expect(saved.ok(), await saved.text()).toBeTruthy();
}

// The composer is a rich-text editor, not a <textarea>: it carries the prompt
// as `aria-placeholder` plus a visible overlay, so `getByPlaceholder` (which
// only matches the `placeholder` attribute) can't see it. Assert on both hooks
// so the visible prompt text stays covered.
export function composerFor(page: Page, placeholder: RegExp) {
  return {
    editor: page.getByTestId("composer-editor"),
    prompt: page.getByText(placeholder),
  };
}

// Typing goes through real key events rather than `fill()`: the editor builds
// its state from beforeinput/keydown, and `fill()`'s single bulk insert leaves
// it out of sync with the DOM.
export async function typeIntoComposer(page: Page, text: string) {
  const editor = page.getByTestId("composer-editor");
  await editor.click();
  await editor.pressSequentially(text);
  await editor.press("Enter");
}

// A fresh user lands on the default-model dialog, whose backdrop swallows
// clicks on the composer behind it.
export async function dismissOnboardingIfShown(page: Page) {
  const profile = (await (
    await page.request.get("/dashboard/api/profile")
  ).json()) as { default_model?: string };
  const mapping = (await (
    await page.request.get("/dashboard/api/my-mapping")
  ).json()) as { slack_user_id?: string };
  const session = (await (
    await page.request.get("/dashboard/api/me")
  ).json()) as {
    slack_oauth_enabled?: boolean;
  };
  const needsOnboarding =
    !profile.default_model ||
    (session.slack_oauth_enabled && !mapping.slack_user_id);
  if (!needsOnboarding) return;
  const dismiss = page.getByRole("button", { name: "Maybe later" });
  await expect(dismiss).toBeVisible();
  await dismiss.click();
  await expect(dismiss).toBeHidden();
}

export async function setRepoPrivate(page: Page, value: boolean) {
  const res = await page.request.post("/control/repo-private", {
    data: { private: value },
  });
  expect(res.ok()).toBeTruthy();
}

export async function setPullRequestHealth(
  page: Page,
  values: Record<string, unknown>,
) {
  const res = await page.request.post("/control/pull-request-health", {
    data: { number: 1, ...values },
  });
  expect(res.ok()).toBeTruthy();
}

export type MergeMethod = "squash" | "merge" | "rebase";

export interface FakeCheckRun {
  name: string;
  status: "queued" | "in_progress" | "completed";
  conclusion?:
    | "success"
    | "failure"
    | "timed_out"
    | "action_required"
    | "startup_failure"
    | "cancelled"
    | "stale"
    | "skipped"
    | "neutral"
    | null;
  details_url?: string;
  required?: boolean;
}

export interface FakeCommitStatus {
  context: string;
  state: "pending" | "success" | "failure" | "error";
  target_url?: string;
  required?: boolean;
}

export interface FakeReview {
  id?: number;
  author: string;
  state: "APPROVED" | "CHANGES_REQUESTED" | "COMMENTED" | "DISMISSED";
  body?: string;
  url?: string;
}

export interface FakeReviewThread {
  path: string;
  line?: number;
  original_line?: number;
  is_resolved?: boolean;
  is_outdated?: boolean;
  author?: string;
  body?: string;
  url?: string;
  comments?: Array<{ author: string; body: string; url?: string }>;
}

export interface SeedPullRequestOptions {
  repo?: string;
  title?: string;
  body?: string;
  author?: string;
  head?: string;
  base?: string;
  draft?: boolean;
  created_at?: string;
  updated_at?: string;
  mergeable?: boolean;
  mergeable_state?: "clean" | "dirty" | "blocked" | "behind" | "unknown";
  check_runs?: FakeCheckRun[];
  statuses?: FakeCommitStatus[];
  reviews?: FakeReview[];
  review_threads?: FakeReviewThread[];
  review_decision?: "APPROVED" | "CHANGES_REQUESTED" | "REVIEW_REQUIRED";
}

export interface SeededPullRequest {
  number: number;
  repo: string;
  head_sha: string;
}

// Put an open PR in the fake GitHub store so the PR search ("Pull Requests →
// Mine") returns it. `author` defaults to SAME_USER's login, the identity
// `loginAs(page, SAME_USER)` signs in as.
export async function seedOpenPullRequest(
  page: Page,
  options: SeedPullRequestOptions = {},
): Promise<SeededPullRequest> {
  const res = await page.request.post("/control/pull-request", {
    data: { author: SAME_USER.login, ...options },
  });
  expect(res.ok()).toBeTruthy();
  const payload = (await res.json()) as SeededPullRequest;
  return payload;
}

export async function setRepoMergeMethods(
  page: Page,
  repo: string,
  methods: MergeMethod[],
) {
  const res = await page.request.post("/control/repo-merge-methods", {
    data: { repo, methods },
  });
  expect(res.ok()).toBeTruthy();
}

export interface MockPullRequest {
  number: number;
  repo: string;
  title: string;
  head: string;
  head_sha: string;
  base: string;
  state: "open" | "closed";
  draft: boolean;
  merged: boolean;
  merge_method: MergeMethod | "" | null;
  mergeable: boolean;
  mergeable_state: string;
  author: string;
  body: string;
  created_at: string;
  updated_at: string;
  url: string;
}

// Read one PR straight out of the fake GitHub store, so a spec can assert what
// the dashboard's merge/close actually did server-side.
export async function readPullRequest(
  page: Page,
  owner: string,
  repo: string,
  number: number,
): Promise<MockPullRequest> {
  const res = await page.request.get("/mock/github/data");
  expect(res.ok()).toBeTruthy();
  const pulls = (await res.json()) as MockPullRequest[];
  const match = pulls.find(
    (pull) => pull.repo === `${owner}/${repo}` && pull.number === number,
  );
  expect(match, `no fake PR ${owner}/${repo}#${number}`).toBeDefined();
  return match as MockPullRequest;
}

// Hold the fake run open long enough to load its busy composer and queue a
// follow-up. It may finish while the UI observes the next server refresh.
export async function openRunningThreadViaSlackLink(page: Page) {
  await page.goto("/mock/slack");
  await page.locator("#reset").click();
  await expect(page.locator("#thread")).toContainText("No messages yet");
  await page
    .locator("#text")
    .fill("<@U0BOT> E2E_BUSY_HOLD:8 please add a greet() helper and open a PR");
  await page.locator("#send").click();

  const webLink = page.locator('.msg.bot a[href*="/agents/"]').first();
  await expect(webLink).toBeVisible();
  await webLink.click();
  await expect(page).toHaveURL(/\/agents\//);
}

// Run the Slack flow so a thread + PR exist, then click the bot's real
// "Open in Web" link, landing on the actual dashboard app.
export async function openThreadViaSlackLink(
  page: Page,
  options: { repoPrivate?: boolean; message?: string } = {},
) {
  await page.goto("/mock/slack");
  await page.locator("#reset").click();
  if (options.repoPrivate) {
    await setRepoPrivate(page, true);
  }
  await expect(page.locator("#thread")).toContainText("No messages yet");
  await page
    .locator("#text")
    .fill(
      options.message ?? "<@U0BOT> please add a greet() helper and open a PR",
    );
  await page.locator("#send").click();
  await expect(
    page.locator(".msg.bot").filter({ hasText: "Add greet() helper" }),
  ).toBeVisible({ timeout: 60_000 });

  const webLink = page.locator('.msg.bot a[href*="/agents/"]').first();
  await expect(webLink).toBeVisible();
  await webLink.click();
  await expect(page).toHaveURL(/\/agents\//);
}

// The SDK hydrates an idle thread's transcript from getState on load, which can
// briefly lag; a reload re-fetches it. Retry until the PR link renders.
export async function openMultiRepoPrThreadViaSlackLink(page: Page) {
  await page.goto("/mock/slack");
  await page.locator("#reset").click();
  await page
    .locator("#text")
    .fill(
      "<@U0BOT> E2E_MULTI_PR open related pull requests in both repositories",
    );
  await page.locator("#send").click();
  await expect(
    page.locator(".msg.bot").filter({ hasText: "anotherorg/companion" }),
  ).toBeVisible({ timeout: 60_000 });

  const webLink = page.locator('.msg.bot a[href*="/agents/"]').first();
  await expect(webLink).toBeVisible();
  await webLink.click();
  await expect(page).toHaveURL(/\/agents\//);
}

export function threadIdFromUrl(page: Page): string {
  const id = new URL(page.url()).pathname.split("/").pop() ?? "";
  expect(id).not.toBe("");
  return id;
}

export async function expectTranscriptVisible(page: Page) {
  await expect(async () => {
    await page.reload();
    await expect(
      page.getByRole("link", { name: "Add greet() helper" }).first(),
    ).toBeVisible({ timeout: 8000 });
  }).toPass({ timeout: 60000 });
}

export async function waitForThreadIdle(page: Page, threadId: string) {
  await expect
    .poll(
      async () => {
        const res = await page.request.get(
          `/dashboard/api/threads/${threadId}?mark_viewed=false`,
        );
        if (!res.ok()) return "unknown";
        return ((await res.json()) as { status?: string }).status ?? "unknown";
      },
      { timeout: 30_000, intervals: [500] },
    )
    .not.toBe("running");
}

export async function waitForThreadNotBusy(page: Page, threadId: string) {
  await expect
    .poll(
      async () => {
        const res = await page.request.get(`/threads/${threadId}`);
        if (!res.ok()) return "unknown";
        return ((await res.json()) as { status?: string }).status ?? "unknown";
      },
      { timeout: 30_000, intervals: [500] },
    )
    .not.toBe("busy");
}

export async function threadState(
  page: Page,
  threadId: string,
): Promise<string> {
  const res = await page.request.get(
    `/dashboard/api/threads/${threadId}/state`,
  );
  if (!res.ok()) return "";
  return JSON.stringify(await res.json());
}

export async function waitForStateToContain(
  page: Page,
  threadId: string,
  text: string,
) {
  await expect
    .poll(() => threadState(page, threadId), {
      timeout: 60_000,
      intervals: [500],
    })
    .toContain(text);
}

export async function latestPrBody(page: Page): Promise<string> {
  const res = await page.request.get("/mock/github/data");
  expect(res.ok()).toBeTruthy();
  const prs = (await res.json()) as Array<{ body?: string }>;
  expect(prs.length).toBeGreaterThan(0);
  return prs[prs.length - 1]?.body ?? "";
}

export async function openThreadActionsMenu(page: Page) {
  const threadId = threadIdFromUrl(page);
  await page
    .locator(`[data-sidebar-frame] a[href="/agents/${threadId}"]`)
    .click({ button: "right" });
}
