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

    // The transcript source hydrates from its snapshot once; coming back to
    // the foreground must not fetch it again.
    const foregroundHydration = page
      .waitForRequest(
        (request) => {
          const path = new URL(request.url()).pathname;
          return (
            request.method() === "GET" &&
            path === `/dashboard/api/threads/${threadId}/transcript`
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

  test("a new user hydrates from the transcript by default", async ({
    page,
  }) => {
    await loginAs(page, { login: "carol", email: "carol@example.com" });
    const hydrations: Array<string> = [];
    page.on("request", (request) => {
      if (request.method() !== "GET") return;
      const path = new URL(request.url()).pathname;
      if (/^\/dashboard\/api\/threads\/[^/]+\/(state|transcript)$/.test(path))
        hydrations.push(path);
    });

    await openThreadViaSlackLink(page);
    const threadId = threadIdFromUrl(page);
    await waitForThreadIdle(page, threadId);
    await expectTranscriptVisible(page);

    expect(hydrations).toContain(
      `/dashboard/api/threads/${threadId}/transcript`,
    );
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

  test("keeps a web model override on the next Slack turn", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await openThreadViaSlackLink(page);
    const threadId = threadIdFromUrl(page);
    await waitForThreadIdle(page, threadId);
    await waitForThreadNotBusy(page, threadId);

    await page.getByRole("button", { name: /GPT-5\.6 Sol/ }).click();
    await page.getByText("GPT-5.6 Sol", { exact: true }).last().hover();
    await page.getByRole("option", { name: /Opus 5\.5/ }).click();
    await typeIntoComposer(page, "Use Opus for this thread");
    await waitForThreadIdle(page, threadId);
    await waitForThreadNotBusy(page, threadId);

    const slackState = (await (
      await page.request.get("/control/state")
    ).json()) as { thread_ts: string };
    const response = await page.request.post("/mock/slack/send", {
      data: {
        text: "<@U0BOT> continue with the selected model",
        mention_bot: true,
        thread_ts: slackState.thread_ts,
      },
    });
    expect(response.ok()).toBeTruthy();

    await expect
      .poll(async () => {
        const runs = (await (
          await page.request.get(`/threads/${threadId}/runs`)
        ).json()) as Array<{
          kwargs?: {
            config?: { configurable?: { agent_model_id?: string } };
          };
        }>;
        return runs[0]?.kwargs?.config?.configurable?.agent_model_id;
      })
      .toBe("anthropic:claude-opus-5-5");
  });

  test("renders structured input envelopes safely and keeps legacy messages", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await openRunningThreadViaSlackLink(page);
    const threadId = threadIdFromUrl(page);
    await waitForThreadIdle(page, threadId);

    // A new thread is served from the transcript, so the rows the renderer has
    // to defend against are injected into the snapshot it reads.
    await page.route(
      `**/dashboard/api/threads/${threadId}/transcript`,
      async (route) => {
        const response = await route.fetch();
        const body = (await response.json()) as {
          turns?: Array<{ turn_id: string }>;
          messages?: Array<Record<string, unknown>>;
        };
        const turnId = body.turns?.[0]?.turn_id;
        if (!turnId) {
          await route.fulfill({ response });
          return;
        }
        const injected = [
          [
            "entity-person",
            '<dynamic-context kind="person" id="github:alice">\ndisplay_name: Alice\n</dynamic-context>',
          ],
          [
            "entity-system",
            '<dynamic-context kind="system" id="system:scheduler">\ndisplay_name: Scheduler\n</dynamic-context>',
          ],
          [
            "structured-person",
            '<input-message sender="github:alice" surface="web" kind="human">\nPerson says &lt;img data-e2e-injected src=x&gt;\n</input-message>',
          ],
          // Rows already in the database wrap their text in `<content>`.
          [
            "structured-system",
            '<input-message sender="system:scheduler" surface="automation"><content>Automation checks CI</content></input-message>',
          ],
          ["legacy-e2e", "Legacy stays visible"],
        ].map(([messageId, text], index) => ({
          message_id: messageId,
          turn_id: turnId,
          role: "human",
          text,
          reasoning: "",
          namespace: [],
          attachments: null,
          usage: null,
          created_at: `2000-01-01T00:00:0${index}Z`,
        }));
        body.messages = [...injected, ...(body.messages ?? [])];
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
