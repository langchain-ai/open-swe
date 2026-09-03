import { test, expect, type Page } from "@playwright/test";
import {
  SAME_USER,
  expectTranscriptVisible,
  loginAs,
  openRunningThreadViaSlackLink,
  openThreadViaSlackLink,
  threadIdFromUrl,
  typeIntoComposer,
  waitForStateToContain,
  waitForThreadIdle,
  waitForThreadNotBusy,
} from "./helpers/dashboard";

// Rendering a finished Slack thread in the real dashboard. Driving the whole
// Slack → implement → PR flow once per assertion dominated the suite, so the
// tests below share one fixture thread and run in order — the two read-only
// ones first, the one that posts a follow-up last.
test.describe("finished transcript (shared fixture thread)", () => {
  test.describe.configure({ mode: "serial" });

  let page: Page;
  let threadId: string;

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
    await loginAs(page, SAME_USER);
    await openThreadViaSlackLink(page);
    threadId = threadIdFromUrl(page);
    await waitForThreadIdle(page, threadId);
  });

  test.afterAll(async () => {
    await page.close();
  });

  // A cold load of a finished thread must hydrate from `getState()` alone. The
  // event stream is blocked so run replay can't stand in for that read: a
  // long-finished run has no replay left, which is what makes a broken hydrate
  // surface as a permanently empty transcript.
  test("a cold load renders the transcript without run replay", async () => {
    await page.route("**/stream/events", (route) => route.abort());
    await page.goto(`/agents/${threadId}`);
    await expect(
      page.getByRole("link", { name: "Add greet() helper" }).first(),
    ).toBeVisible();
    await expect(
      page.getByText("This thread has no messages yet."),
    ).toHaveCount(0);
    await page.unroute("**/stream/events");
  });

  test("folds the agent's work and expands an Edit into an inline diff", async () => {
    await page.goto(`/agents/${threadId}`);
    await expectTranscriptVisible(page);

    const worked = page.getByRole("button", {
      name: /^Worked(?: for .+)? · \d+ actions?$/,
    });
    const acknowledgement = page.getByText("On it!", { exact: true });
    const edit = page.getByRole("button", { name: "Edited greet.py" });

    // Folded: the acknowledgement shows, the individual tool calls do not.
    await expect(worked).toBeVisible();
    await expect(acknowledgement).toBeVisible();
    await expect(edit).toHaveCount(0);

    await worked.click();
    await expect(edit).toBeVisible();
    await expect(acknowledgement).toBeVisible();
    expect(
      await acknowledgement.evaluate(
        (message, entry) =>
          Boolean(
            message.compareDocumentPosition(entry) &
            Node.DOCUMENT_POSITION_FOLLOWING,
          ),
        await edit.elementHandle(),
      ),
    ).toBe(true);

    await expect(edit).toHaveAttribute("aria-expanded", "false");
    await edit.click();
    await expect(edit).toHaveAttribute("aria-expanded", "true");

    const inlineDiff = edit.locator("[data-diff]");
    await expect(inlineDiff).toBeVisible();
    await expect(
      inlineDiff.locator('[data-line][data-line-type="change-deletion"]'),
    ).toContainText('return "Hello!"');
    await expect(
      inlineDiff.locator('[data-line][data-line-type="change-addition"]'),
    ).toContainText('return f"Hello, {name}!"');
    await expect(inlineDiff).toHaveAttribute("data-disable-line-numbers");
    await expect(inlineDiff).not.toContainText("normalize");
  });

  // The queued card is optimistic, so a regression shows up as a flash the DOM
  // holds for only the length of one request — too short for a locator poll.
  // This one posts a follow-up, so it runs last against the shared thread.
  test("never flashes a queued card when no run is in progress", async () => {
    await page.goto(`/agents/${threadId}`);
    await waitForThreadIdle(page, threadId);
    // The dashboard's status can report a finished run before LangGraph drops
    // the thread out of `busy`, and `busy` is the exact condition the queue
    // endpoint accepts on. Wait for it, or the send legitimately queues.
    await waitForThreadNotBusy(page, threadId);

    await page.evaluate(() => {
      const seen = { value: false };
      (window as unknown as Record<string, unknown>).__queuedCardSeen = seen;
      new MutationObserver(() => {
        if (document.querySelector('[data-testid="queued-message"]'))
          seen.value = true;
      }).observe(document.body, { childList: true, subtree: true });
    });

    await typeIntoComposer(page, "Can you also add a docstring?");
    await expect(
      page.getByText(/anything else you'd like changed/),
    ).toBeVisible();

    const flashed = await page.evaluate(
      () =>
        (
          (window as unknown as Record<string, unknown>).__queuedCardSeen as {
            value: boolean;
          }
        ).value,
    );
    expect(flashed).toBe(false);
  });
});

test.describe("transcript rendering", () => {
  test("renders Slack mrkdwn and identifies the Slack sender", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await openThreadViaSlackLink(page, {
      message:
        "<@U0BOT> please add a greet() helper and open a PR; review *important* R&amp;D `<https://example.com/code|code docs>` <https://example.com/slack-docs|Slack docs>",
    });
    await expectTranscriptVisible(page);

    const slackMessage = page
      .locator('[data-message-surface="slack"]')
      .filter({ hasText: "Slack docs" })
      .first();
    await expect(
      slackMessage.getByRole("img", { name: "Slack" }),
    ).toBeVisible();
    await expect(slackMessage.locator("strong")).toHaveText("important");
    await expect(slackMessage).toContainText("R&D");
    await expect(slackMessage.locator("code")).toContainText("code docs");
    await expect(
      slackMessage.getByRole("link", { name: "code docs" }),
    ).toHaveCount(0);
    await expect(
      slackMessage.getByRole("link", { name: "Slack docs" }),
    ).toHaveAttribute("href", "https://example.com/slack-docs");
  });

  test("keeps the transcript mounted after navigation and refocus", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await openThreadViaSlackLink(page);
    const threadId = threadIdFromUrl(page);
    await waitForThreadIdle(page, threadId);

    await page.getByRole("link", { name: "New Thread" }).click();
    await expect(page).toHaveURL(/\/agents\/?$/);
    await page.goBack();
    await expect(page).toHaveURL(new RegExp(`/agents/${threadId}$`));
    await expect(
      page.getByRole("link", { name: "Add greet() helper" }).first(),
    ).toBeVisible();

    const foregroundHydration = page
      .waitForRequest(
        (request) => {
          const path = new URL(request.url()).pathname;
          return (
            request.method() === "GET" &&
            path === `/dashboard/api/threads/${threadId}/state`
          );
        },
        { timeout: 1_000 },
      )
      .then(
        () => true,
        () => false,
      );
    await page.evaluate(() =>
      document.dispatchEvent(new Event("visibilitychange")),
    );
    expect(await foregroundHydration).toBe(false);
    await expect(
      page.getByRole("link", { name: "Add greet() helper" }).first(),
    ).toBeVisible();

    await typeIntoComposer(page, "Can you also add a docstring?");
    await expect(
      page.getByText(/anything else you'd like changed/),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Add greet() helper" }).first(),
    ).toBeVisible();
  });

  test("renders a web follow-up exactly once", async ({ page }) => {
    await loginAs(page, SAME_USER);
    await openThreadViaSlackLink(page);
    const threadId = threadIdFromUrl(page);
    await waitForThreadIdle(page, threadId);
    await waitForThreadNotBusy(page, threadId);

    const followUp = "Can you also add a docstring?";
    await typeIntoComposer(page, followUp);
    // The agent's canned reply is already in the transcript from the Slack run,
    // so wait on the run persisting this message rather than on any reply text.
    await waitForStateToContain(page, threadId, followUp);
    await waitForThreadIdle(page, threadId);
    await waitForThreadNotBusy(page, threadId);

    await expect(
      page.getByTestId("user-message").filter({ hasText: followUp }),
    ).toHaveCount(1);
  });

  test("renders structured input envelopes safely and keeps legacy messages", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await openRunningThreadViaSlackLink(page);
    const threadId = threadIdFromUrl(page);
    await waitForThreadIdle(page, threadId);

    await page.route(
      `**/dashboard/api/threads/${threadId}/state`,
      async (route) => {
        const response = await route.fetch();
        const body = (await response.json()) as {
          values?: { messages?: Array<Record<string, unknown>> };
        };
        const messages = body.values?.messages ?? [];
        body.values = {
          ...body.values,
          messages: [
            {
              type: "human",
              id: "entity-person",
              content:
                '<dynamic-context kind="person" id="github:alice"><display_name>Alice</display_name></dynamic-context>',
            },
            {
              type: "human",
              id: "entity-system",
              content:
                '<dynamic-context kind="system" id="system:scheduler"><display_name>Scheduler</display_name></dynamic-context>',
            },
            {
              type: "human",
              id: "structured-person",
              content:
                '<input-message sender="github:alice" surface="web" kind="human"><content>Person says &lt;img data-e2e-injected src=x&gt;</content></input-message>',
            },
            {
              type: "human",
              id: "structured-system",
              content:
                '<input-message sender="system:scheduler" surface="automation"><content>Automation checks CI</content></input-message>',
            },
            {
              type: "human",
              id: "legacy-e2e",
              content: "Legacy stays visible",
            },
            ...messages,
          ],
        };
        await route.fulfill({ response, json: body });
      },
    );

    await page.reload();
    await expect(
      page.getByText("Person says <img data-e2e-injected src=x>"),
    ).toBeVisible();
    await expect(page.locator("img[data-e2e-injected]")).toHaveCount(0);
    await expect(page.getByText("Automation checks CI")).toHaveCount(0);
    const systemChip = page.getByRole("button", { name: "Scheduler" });
    await expect(systemChip).toBeVisible();
    await systemChip.click();
    await expect(page.getByText("Automation checks CI")).toBeVisible();
    await expect(page.getByText("Legacy stays visible")).toBeVisible();
    await expect(page.getByText("github:alice", { exact: false })).toHaveCount(
      0,
    );
    await expect(
      page.getByText("system:scheduler", { exact: false }),
    ).toHaveCount(0);
    await expect(
      page
        .locator('[data-message-sender-kind="person"]')
        .filter({ hasText: "Person says" }),
    ).toBeVisible();
    await expect(
      page
        .locator('[data-message-sender-kind="system"]')
        .filter({ hasText: "Automation checks CI" }),
    ).toBeVisible();
  });
});
