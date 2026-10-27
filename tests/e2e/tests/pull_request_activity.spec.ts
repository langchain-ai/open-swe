import { test, expect, type APIResponse, type Page } from "@playwright/test";

import {
  SAME_USER,
  composerFor,
  dismissOnboardingIfShown,
  expectTranscriptVisible,
  loginAs,
  openThreadViaSlackLink,
  setFollowUpBehavior,
  threadIdFromUrl,
  typeIntoComposer,
  waitForStateToContain,
} from "./helpers/dashboard";

test.use({ colorScheme: "dark" });

const COMMENT = "Rename greet() to say_hello() before this lands.";

async function controlJson<T>(response: Promise<APIResponse>): Promise<T> {
  const answered = await response;
  expect(answered.ok(), await answered.text()).toBeTruthy();
  return (await answered.json()) as T;
}

async function threadRuns(page: Page, threadId: string) {
  return controlJson<{ runs: number; idle: boolean }>(
    page.request.get(`/control/thread-idle?thread_id=${threadId}`),
  );
}

async function queuedCount(page: Page, threadId: string) {
  return (
    await controlJson<{ queued_count: number }>(
      page.request.get(`/control/queued?thread_id=${threadId}`),
    )
  ).queued_count;
}

// What a person does to a pull request in the dashboard reaches the agent
// threads linked to it as their input, for the thread's next run, without
// starting one.
test("dashboard pull request actions wait for the PR thread's next run", async ({
  page,
}) => {
  await loginAs(page, SAME_USER);
  await setFollowUpBehavior(page, "steer");
  await openThreadViaSlackLink(page);
  const threadId = threadIdFromUrl(page);
  await expectTranscriptVisible(page);
  await expect
    .poll(async () => (await threadRuns(page, threadId)).idle, {
      timeout: 30_000,
    })
    .toBe(true);
  const { runs } = await threadRuns(page, threadId);

  await page.goto("/agents/reviews/fakeorg/demo/1");
  await page.getByRole("tab", { name: /Discussion/ }).click();
  const conversation = page.getByRole("region", { name: "Conversation" });
  await conversation
    .getByRole("textbox", { name: "Comment on this pull request" })
    .fill(COMMENT);
  await conversation
    .getByRole("button", { name: "Comment", exact: true })
    .click();
  await expect(conversation.getByText(COMMENT)).toBeVisible();

  const markReady = page
    .locator("header")
    .getByRole("button", { name: "Mark ready", exact: true });
  await markReady.click();
  await expect(markReady).toHaveCount(0);

  // GitHub's own webhook wakes the thread that opened the PR for the comment,
  // so only the action GitHub does not tell it about waits on its queue.
  await expect.poll(() => queuedCount(page, threadId)).toBe(1);
  await page.waitForTimeout(3_000);
  expect(await queuedCount(page, threadId)).toBe(1);
  expect(await threadRuns(page, threadId)).toEqual({ runs, idle: true });

  await page.goto(`/agents/${threadId}`);
  await dismissOnboardingIfShown(page);
  const queued = page.getByTestId("queued-message");
  await expect(queued).toHaveCount(1);
  await expect(queued).toContainText(
    "@alice marked https://github.com/fakeorg/demo/pull/1 ready for review",
  );
  await queued.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: test.info().outputPath("queued-pull-request-activity.png"),
  });

  // The next run, started as any other would be, reads it before its first model call.
  await typeIntoComposer(page, "Anything else before merge?");
  await waitForStateToContain(
    page,
    threadId,
    'action=\\"pull-request-mark-ready\\"',
  );
  await expect.poll(() => queuedCount(page, threadId)).toBe(0);
  await expect(
    composerFor(page, /Add a follow up|Send the first message/).prompt,
  ).toBeVisible({ timeout: 60_000 });
  await page.reload();
  const delivered = page
    .getByTestId("user-message")
    .filter({ hasText: "@alice marked" });
  await expect(delivered).toBeVisible({ timeout: 30_000 });
  await delivered.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: test.info().outputPath("delivered-pull-request-activity.png"),
  });
});
