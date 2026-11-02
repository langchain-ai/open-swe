import { test, expect, type APIResponse } from "@playwright/test";

import {
  SAME_USER,
  composerFor,
  dismissOnboardingIfShown,
  loginAs,
  setFollowUpBehavior,
  typeIntoComposer,
} from "./helpers/dashboard";

// Mirrors `INBOX_FOLLOW_UP` in fake_llm.py, which the scripted model looks for.
const FOLLOW_UP = "Please also note the inbox follow-up.";

async function controlJson<T>(response: Promise<APIResponse>): Promise<T> {
  const answered = await response;
  expect(answered.ok(), await answered.text()).toBeTruthy();
  return (await answered.json()) as T;
}

// While a run works, what is sent to its thread waits in the thread's inbox, and
// the run takes it before its next model call. The scripted model starts a
// monitor, then holds a model call on the harness's gate; meanwhile the spec
// steers the run with a dashboard follow-up and reads the monitor's event, as
// the per-minute poll would. Released, the run's next call must see both, in
// the same run.
test("a busy run takes a queued follow-up and a monitor event before its next model call", async ({
  page,
}) => {
  await loginAs(page, SAME_USER);
  // The follow-up steers the live run; an earlier spec may have left queueing on.
  await setFollowUpBehavior(page, "steer");
  await page.goto("/mock/slack");
  await page.locator("#reset").click();

  const { thread_id: threadId } = await controlJson<{ thread_id: string }>(
    page.request.post("/mock/slack/send", {
      data: { text: "<@U0BOT> E2E_INBOX watch the build log" },
    }),
  );
  await expect
    .poll(
      async () =>
        (
          await controlJson<{ waiting: boolean }>(
            page.request.get("/control/inbox-gate"),
          )
        ).waiting,
      { timeout: 60_000 },
    )
    .toBe(true);

  await page.goto(`/agents/${threadId}`);
  await dismissOnboardingIfShown(page);
  await expect(
    composerFor(page, /Send a message to steer the run/).prompt,
  ).toBeVisible({
    timeout: 30_000,
  });
  await typeIntoComposer(page, FOLLOW_UP);
  await expect
    .poll(
      async () =>
        (
          await controlJson<{ queued_count: number }>(
            page.request.get(`/control/queued?thread_id=${threadId}`),
          )
        ).queued_count,
      { timeout: 30_000 },
    )
    .toBe(1);
  await expect
    .poll(
      async () =>
        (
          await controlJson<{ events: number }>(
            page.request.post(`/control/monitors/sync?thread_id=${threadId}`),
          )
        ).events,
      { timeout: 30_000 },
    )
    .toBeGreaterThan(0);

  await controlJson(page.request.post("/control/inbox-gate"));

  await page.goto("/mock/slack");
  await expect(
    page
      .locator(".msg.bot")
      .filter({ hasText: "Inbox check: follow-up seen, monitor events 1." }),
  ).toBeVisible({ timeout: 60_000 });
  const { runs } = await controlJson<{ runs: number }>(
    page.request.get(`/control/thread-idle?thread_id=${threadId}`),
  );
  expect(runs).toBe(1);
});
