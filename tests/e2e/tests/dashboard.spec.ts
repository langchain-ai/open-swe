import { test, expect, type Page } from "@playwright/test";

// Drives the REAL built ui/ app (served same-origin from the harness) for the
// Slack → web handoff. Only the LLM/GitHub/Slack/token boundaries are faked.
const SAME_USER = { login: "alice", email: "alice@example.com" };
const OTHER_USER = { login: "bob", email: "bob@example.com" };

async function loginAs(page: Page, user: { login: string; email: string }) {
  const res = await page.request.post("/control/login", { data: user });
  expect(res.ok()).toBeTruthy();
}

// The composer is a rich-text editor, not a <textarea>: it carries the prompt
// as `aria-placeholder` plus a visible overlay, so `getByPlaceholder` (which
// only matches the `placeholder` attribute) can't see it. Assert on both hooks
// so the visible prompt text stays covered.
function composerFor(page: Page, placeholder: RegExp) {
  return {
    editor: page.getByTestId("composer-editor"),
    prompt: page.getByText(placeholder),
  };
}

// Typing goes through real key events rather than `fill()`: the editor builds
// its state from beforeinput/keydown, and `fill()`'s single bulk insert leaves
// it out of sync with the DOM.
async function typeIntoComposer(page: Page, text: string) {
  const editor = page.getByTestId("composer-editor");
  await editor.click();
  await editor.pressSequentially(text);
  await editor.press("Enter");
}

async function setRepoPrivate(page: Page, value: boolean) {
  const res = await page.request.post("/control/repo-private", {
    data: { private: value },
  });
  expect(res.ok()).toBeTruthy();
}

async function setPullRequestHealth(
  page: Page,
  values: Record<string, unknown>,
) {
  const res = await page.request.post("/control/pull-request-health", {
    data: { number: 1, ...values },
  });
  expect(res.ok()).toBeTruthy();
}

// E2E_BUSY_HOLD:8 makes the fake LLM hold the run open for 8s. The window has to
// outlast the click through to the thread plus one reload, which takes over 5s
// on a CI runner; once the run finishes the retry loop below can never pass.
async function openRunningThreadViaSlackLink(page: Page) {
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
async function openThreadViaSlackLink(
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
  ).toBeVisible();

  const webLink = page.locator('.msg.bot a[href*="/agents/"]').first();
  await expect(webLink).toBeVisible();
  await webLink.click();
  await expect(page).toHaveURL(/\/agents\//);
}

// The SDK hydrates an idle thread's transcript from getState on load, which can
// briefly lag; a reload re-fetches it. Retry until the PR link renders.
async function openMultiRepoPrThreadViaSlackLink(page: Page) {
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
  ).toBeVisible();

  const webLink = page.locator('.msg.bot a[href*="/agents/"]').first();
  await expect(webLink).toBeVisible();
  await webLink.click();
  await expect(page).toHaveURL(/\/agents\//);
}

async function expectTranscriptVisible(page: Page) {
  await expect(async () => {
    await page.reload();
    await expect(
      page.getByRole("link", { name: "Add greet() helper" }).first(),
    ).toBeVisible({ timeout: 8000 });
  }).toPass({ timeout: 60000 });
}

async function waitForThreadIdle(page: Page, threadId: string) {
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

async function waitForThreadNotBusy(page: Page, threadId: string) {
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

async function waitForStateToContain(
  page: Page,
  threadId: string,
  text: string,
) {
  await expect
    .poll(
      async () => {
        const res = await page.request.get(
          `/dashboard/api/threads/${threadId}/state`,
        );
        if (!res.ok()) return false;
        return JSON.stringify(await res.json()).includes(text);
      },
      { timeout: 60_000, intervals: [500] },
    )
    .toBe(true);
}

async function latestPrBody(page: Page): Promise<string> {
  const res = await page.request.get("/mock/github/data");
  expect(res.ok()).toBeTruthy();
  const prs = (await res.json()) as Array<{ body?: string }>;
  expect(prs.length).toBeGreaterThan(0);
  return prs[prs.length - 1]?.body ?? "";
}

async function openThreadActionsMenu(page: Page) {
  await page
    .getByRole("link", { name: /please add a greet/ })
    .first()
    .click({ button: "right" });
}

test.describe("Slack → web handoff (real dashboard UI)", () => {
  test("the SAME user continues the conversation in the web app", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await openThreadViaSlackLink(page);

    // The owner sees the composer (either the follow-up bar once the transcript
    // hydrates, or the empty-state bar before it — both mean they can type).
    const composer = composerFor(
      page,
      /Add a follow up|Send the first message/,
    );
    await expect(composer.editor).toBeVisible();
    await expect(composer.prompt).toBeVisible();
    // Continue from the web — a new agent reply streams into the same thread.
    await typeIntoComposer(page, "Looks good — can you also add a docstring?");
    await expect(
      page.getByText(/anything else you'd like changed/),
    ).toBeVisible();

    // The transcript that started in Slack is here too (incl. the PR link).
    await expect(
      page.getByRole("link", { name: "Add greet() helper" }).first(),
    ).toBeVisible();
  });

  test("keeps pull requests from multiple repositories above the composer", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await openMultiRepoPrThreadViaSlackLink(page);

    const companionLink = page.getByRole("link", {
      name: "Open anotherorg/companion pull request #2",
    });
    await expect(async () => {
      await page.reload();
      await expect(companionLink).toBeVisible({ timeout: 8000 });
    }).toPass({ timeout: 60_000 });

    const strip = page.getByTestId("thread-pull-requests");
    await expect(strip).toBeVisible();
    await expect(
      page.getByRole("link", {
        name: "Open fakeorg/demo pull request #1",
      }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "Show 1 more" }),
    ).toBeVisible();

    await companionLink.hover();
    const hoverCard = page.getByTestId("pr-hover-card-anotherorg/companion-2");
    await expect(hoverCard).toBeVisible();
    await expect(hoverCard).toContainText("anotherorg/companion #2");
    await expect(hoverCard).toContainText("Add companion integration");
    await expect(hoverCard).toContainText("open-swe[bot]");
    await expect(hoverCard).toContainText("main");
    await expect(hoverCard).toContainText("add-integration");
    await expect(hoverCard).toContainText("1 file");

    await page.getByRole("button", { name: "Show 1 more" }).click();
    await expect(
      page.getByRole("link", {
        name: "Open fakeorg/demo pull request #1",
      }),
    ).toBeVisible();
    await expect(companionLink).toBeVisible();
  });

  test("shows live pull request health and submits actionable fixes", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await openThreadViaSlackLink(page);
    const threadId = new URL(page.url()).pathname.split("/").pop() ?? "";
    expect(threadId).not.toBe("");
    await expectTranscriptVisible(page);

    const summary = page.getByTestId("pr-summary-fakeorg/demo-1");
    const fixButton = page.getByRole("button", { name: "Fix PR #1 issues" });
    await expect(summary).toHaveAttribute(
      "data-pr-tone",
      "text-muted-foreground",
    );
    await expect(summary).not.toHaveAttribute(
      "data-pr-tone",
      "text-success-foreground",
    );
    await expect(summary).toContainText("draft");
    await expect(fixButton).toHaveCount(0);

    await setPullRequestHealth(page, {
      draft: false,
      state: "open",
      merged: false,
      mergeable: true,
      mergeable_state: "clean",
      check_runs: [],
      statuses: [],
      review_threads: [],
    });
    await page.reload();
    await expect(summary).toHaveAttribute(
      "data-pr-tone",
      "text-success-foreground",
    );
    await expect(summary).toContainText("open");
    await expect(fixButton).toHaveCount(0);
    await page.waitForTimeout(1_000);

    await page.route("**/pull-request-status", (route) =>
      route.fulfill({ status: 502, json: { detail: "GitHub unavailable" } }),
    );
    const failedRefresh = page.waitForResponse(
      (response) =>
        response.url().endsWith("/pull-request-status") &&
        response.status() === 502,
    );
    await page.evaluate(() =>
      window.dispatchEvent(new Event("visibilitychange")),
    );
    await failedRefresh;
    await expect(summary).toHaveAttribute(
      "data-pr-tone",
      "text-muted-foreground",
      { timeout: 5_000 },
    );
    await summary.focus();
    await expect(
      page.getByTestId("pr-hover-card-fakeorg/demo-1"),
    ).toContainText("GitHub health is unavailable");
    await page.keyboard.press("Escape");
    await page.unroute("**/pull-request-status");

    await setPullRequestHealth(page, {
      mergeable: false,
      mergeable_state: "dirty",
      check_runs: [
        {
          name: "unit-tests",
          status: "completed",
          conclusion: "failure",
          details_url: "https://checks.example/unit-tests",
        },
        {
          name: "browser-e2e",
          status: "completed",
          conclusion: "timed_out",
          details_url: "https://checks.example/browser-e2e",
        },
        {
          name: "preview-deploy",
          status: "in_progress",
          conclusion: null,
        },
      ],
      statuses: [
        {
          context: "legacy/security-scan",
          state: "error",
          target_url: "https://checks.example/security",
        },
      ],
      review_threads: [
        {
          author: "reviewer-one",
          body: "Handle the null response before reading the payload.",
          path: "agent/dashboard/routes.py",
          line: 42,
          url: "https://github.example/discussion/1",
        },
        {
          author: "reviewer-two",
          body: "Add regression coverage for the retry path.",
          path: "tests/dashboard/test_routes.py",
          original_line: 88,
          url: "https://github.example/discussion/2",
        },
        {
          is_resolved: true,
          author: "reviewer-three",
          body: "This resolved comment must not be counted.",
          path: "README.md",
          line: 1,
        },
      ],
    });
    await page.reload();

    await expect(summary).toHaveAttribute("data-pr-tone", "text-destructive");
    await expect(summary).toContainText("3 checks");
    await expect(summary).toContainText("2 comments");
    await expect(summary).toContainText("Conflict");
    await expect(summary).toContainText("1 pending");
    await expect(fixButton).toBeVisible();

    await summary.focus();
    const hoverCard = page.getByTestId("pr-hover-card-fakeorg/demo-1");
    await expect(hoverCard).toBeVisible();
    const failingChecks = hoverCard.getByTestId("pr-failing-checks");
    await expect(failingChecks).toContainText("unit-tests");
    await expect(failingChecks).toContainText("browser-e2e");
    await expect(failingChecks).toContainText("legacy/security-scan");
    const unresolvedComments = hoverCard.getByTestId("pr-unresolved-comments");
    await expect(unresolvedComments).toContainText(
      "Handle the null response before reading the payload.",
    );
    await expect(unresolvedComments).toContainText(
      "Add regression coverage for the retry path.",
    );
    await expect(unresolvedComments).not.toContainText(
      "This resolved comment must not be counted.",
    );

    await page.keyboard.press("Escape");
    await fixButton.click();
    const fixPrompt = "Fix the actionable issues on";
    await waitForStateToContain(page, threadId, fixPrompt);
    await expect(page.getByText(new RegExp(fixPrompt)).first()).toBeVisible();
  });

  // A cold load of a finished thread must hydrate from `getState()` alone. The
  // event stream is blocked so run replay can't stand in for that read: a
  // long-finished run has no replay left, which is what makes a broken hydrate
  // surface as a permanently empty transcript.
  test("a cold load renders a finished thread's transcript without run replay", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await openThreadViaSlackLink(page);
    const threadId = new URL(page.url()).pathname.split("/").pop() ?? "";
    expect(threadId).not.toBe("");
    await waitForThreadIdle(page, threadId);

    await page.route("**/stream/events", (route) => route.abort());
    await page.goto(`/agents/${threadId}`);
    await expect(
      page.getByRole("link", { name: "Add greet() helper" }).first(),
    ).toBeVisible({ timeout: 20_000 });
    await expect(
      page.getByText("This thread has no messages yet."),
    ).toHaveCount(0);
  });

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

  test("keeps sent Slack messages visible while work is folded", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await openThreadViaSlackLink(page);
    await expectTranscriptVisible(page);

    const worked = page.getByRole("button", {
      name: /^Worked(?: for .+)? · \d+ actions?$/,
    });
    const acknowledgement = page.getByText("On it!", { exact: true });
    const edit = page.getByRole("button", { name: "Edited greet.py" });

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
  });

  test("expands an Edit call into a highlighted inline diff", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await openThreadViaSlackLink(page);
    await expectTranscriptVisible(page);

    const worked = page.getByRole("button", {
      name: /^Worked(?: for .+)? · \d+ actions?$/,
    });
    await expect(worked).toBeVisible();
    await worked.click();

    const edit = page.getByRole("button", { name: "Edited greet.py" });
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
    await expect(inlineDiff).not.toContainText("farewell");
    await expect
      .poll(() => inlineDiff.locator("[data-line] span").count())
      .toBeGreaterThan(2);
  });

  test("keeps the transcript mounted after navigation and refocus", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await openThreadViaSlackLink(page);
    const threadId = new URL(page.url()).pathname.split("/").pop() ?? "";
    expect(threadId).not.toBe("");
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

  test("does not expose the originating Slack thread for public repos", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await openThreadViaSlackLink(page);

    await openThreadActionsMenu(page);
    await expect(page.getByText("Open Slack thread")).toHaveCount(0);
    await expect.poll(() => latestPrBody(page)).not.toContain("Slack thread");
  });

  test("exposes the originating Slack thread for private repos", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await openThreadViaSlackLink(page, { repoPrivate: true });

    await openThreadActionsMenu(page);
    const sourceItem = page.getByText("Open Slack thread");
    await expect(sourceItem).toBeVisible();
    const popupPromise = page.waitForEvent("popup");
    await sourceItem.click();
    const popup = await popupPromise;
    await expect(popup).toHaveURL(/\/mock\/slack/);
    await expect.poll(() => latestPrBody(page)).toContain("Slack thread");
  });

  // The queued card is optimistic, so a regression shows up as a flash the DOM
  // holds for only the length of one request — too short for a locator poll.
  test("never flashes a queued card when no run is in progress", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await openThreadViaSlackLink(page);
    const threadId = new URL(page.url()).pathname.split("/").pop() ?? "";
    expect(threadId).not.toBe("");
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

  test("keeps follow-ups visible while queued during a running agent", async ({
    page,
  }, testInfo) => {
    await loginAs(page, SAME_USER);
    await openRunningThreadViaSlackLink(page);
    const threadId = new URL(page.url()).pathname.split("/").pop() ?? "";
    expect(threadId).not.toBe("");

    const queuedText = "Please queue this follow-up while you finish the PR.";
    const busyComposer = composerFor(page, /Send a message to queue next/);
    await expect(async () => {
      await page.reload();
      await expect(busyComposer.prompt).toBeVisible({ timeout: 8000 });
    }).toPass({ timeout: 60000 });
    await typeIntoComposer(page, queuedText);

    const queuedMessage = page
      .getByTestId("queued-message")
      .filter({ hasText: queuedText });
    await expect(queuedMessage).toBeVisible();
    const screenshotPath = testInfo.outputPath("queued-messages-dashboard.png");
    await page.screenshot({ path: screenshotPath, fullPage: true });
    await testInfo.attach("queued-messages-dashboard", {
      path: screenshotPath,
      contentType: "image/png",
    });

    const serverRefresh = await page.waitForResponse((response) => {
      const path = new URL(response.url()).pathname;
      return (
        response.request().method() === "GET" &&
        path === `/dashboard/api/threads/${threadId}`
      );
    });
    expect(serverRefresh.ok()).toBeTruthy();
    await expect(serverRefresh.json()).resolves.toMatchObject({
      status: "running",
    });
    await expect(queuedMessage).toBeVisible();
  });

  // The dashboard proxy rewrites a run's input into the structured envelope. If
  // that rewrite drops the client-minted message id, the SDK's optimistic copy
  // never reconciles with the server's echo and the same text renders twice —
  // once in place, once at the tail of the transcript.
  test("keeps sender metadata hidden after refreshing a new web thread", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await page.goto("/agents");
    const dismissOnboarding = page.getByRole("button", {
      name: "Maybe later",
    });
    await expect(dismissOnboarding).toBeVisible();
    await dismissOnboarding.click();
    await expect(dismissOnboarding).toBeHidden();

    const prompt = "list my open langchainplus PRs";
    await typeIntoComposer(page, prompt);
    await expect(page).toHaveURL(/\/agents\/[^/]+$/);
    const threadId = new URL(page.url()).pathname.split("/").pop() ?? "";
    expect(threadId).not.toBe("");

    const userMessage = page
      .getByTestId("user-message")
      .filter({ hasText: prompt });
    await expect(userMessage).toContainText(prompt);
    await expect(userMessage).not.toContainText("sender_context");
    await waitForStateToContain(page, threadId, "system:sender-context");

    await page.reload();
    await expect(userMessage).toContainText(prompt);
    await expect(userMessage).not.toContainText("sender_context");
  });

  test("renders a web follow-up exactly once", async ({ page }) => {
    await loginAs(page, SAME_USER);
    await openThreadViaSlackLink(page);
    const threadId = new URL(page.url()).pathname.split("/").pop() ?? "";
    expect(threadId).not.toBe("");
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
    const threadId = new URL(page.url()).pathname.split("/").pop() ?? "";
    expect(threadId).not.toBe("");
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

  test("stops a Slack-started run from the web app", async ({ page }) => {
    await loginAs(page, SAME_USER);
    await page.goto("/mock/slack");
    await page.locator("#reset").click();

    const send = await page.request.post("/mock/slack/send", {
      data: {
        text: "<@U0BOT> E2E_BUSY_HOLD please add a greet() helper and open a PR",
      },
    });
    expect(send.ok()).toBeTruthy();
    const { thread_id: threadId } = (await send.json()) as {
      thread_id: string;
    };

    await page.goto(`/agents/${threadId}`);
    const stopButton = page.getByRole("button", { name: "Stop run" });
    await expect(stopButton).toBeVisible();

    const cancelResponsePromise = page.waitForResponse((response) => {
      const path = new URL(response.url()).pathname;
      return (
        response.request().method() === "POST" &&
        path === `/dashboard/api/threads/${threadId}/cancel`
      );
    });
    await stopButton.click();

    const cancelResponse = await cancelResponsePromise;
    expect(cancelResponse.ok()).toBeTruthy();
    await expect(cancelResponse.json()).resolves.toMatchObject({
      id: threadId,
      status: "interrupted",
    });
    await expect(
      page.getByRole("button", { name: "Send message" }),
    ).toBeVisible();
    await expect(stopButton).toHaveCount(0);
  });

  // Escape has to survive the composer's own editor, which registers a Lexical
  // escape command of its own — hence pressing it with the editor focused.
  test("stops a run with Escape from inside the composer", async ({ page }) => {
    await loginAs(page, SAME_USER);
    await page.goto("/mock/slack");
    await page.locator("#reset").click();

    const send = await page.request.post("/mock/slack/send", {
      data: {
        text: "<@U0BOT> E2E_BUSY_HOLD please add a greet() helper and open a PR",
      },
    });
    expect(send.ok()).toBeTruthy();
    const { thread_id: threadId } = (await send.json()) as {
      thread_id: string;
    };

    await page.goto(`/agents/${threadId}`);
    const stopButton = page.getByRole("button", { name: "Stop run" });
    await expect(stopButton).toBeVisible();

    const cancelResponsePromise = page.waitForResponse((response) => {
      const path = new URL(response.url()).pathname;
      return (
        response.request().method() === "POST" &&
        path === `/dashboard/api/threads/${threadId}/cancel`
      );
    });
    await page.getByTestId("composer-editor").click();
    await page.keyboard.press("Escape");

    const cancelResponse = await cancelResponsePromise;
    expect(cancelResponse.ok()).toBeTruthy();
    await expect(cancelResponse.json()).resolves.toMatchObject({
      id: threadId,
      status: "interrupted",
    });
    await expect(
      page.getByRole("button", { name: "Send message" }),
    ).toBeVisible();
    await expect(stopButton).toHaveCount(0);
  });

  test("a DIFFERENT user can post, and their message is attributed", async ({
    page,
  }) => {
    await loginAs(page, OTHER_USER);
    await openThreadViaSlackLink(page);
    const threadId = new URL(page.url()).pathname.split("/").pop() ?? "";
    expect(threadId).not.toBe("");

    // The same thread + transcript is visible…
    await expectTranscriptVisible(page);

    // …and a non-owner now gets a composer too (owner-only restriction removed).
    const composer = composerFor(
      page,
      /Add a follow up|Send the first message/,
    );
    await expect(composer.editor).toBeVisible();
    await expect(composer.prompt).toBeVisible();

    // Posting starts a new run — the agent's follow-up reply streams in.
    const followUp = "Can you also add a docstring?";
    await typeIntoComposer(page, followUp);
    await waitForStateToContain(page, threadId, followUp);

    // The non-owner's message is tagged server-side with their GitHub login, so
    // the owner can tell who sent it. Read it from the transcript the server
    // stored: in the sender's own session the bubble is still the SDK's
    // optimistic echo of what they typed, which carries no envelope.
    await page.reload();
    await expect(
      page.getByText(new RegExp(`@${OTHER_USER.login}`)).first(),
    ).toBeVisible();
  });

  // A slow sidebar used to render as a blank column, indistinguishable from an
  // account with no threads.
  test("shows a loading placeholder while the sidebar list is in flight", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);

    let release = () => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route("**/dashboard/api/threads/page?*", async (route) => {
      const params = new URL(route.request().url()).searchParams;
      if (params.get("resolved") === "false" && params.get("limit") === "10") {
        await held;
      }
      await route.continue();
    });

    // The held request would block `load`, so stop waiting at the first byte.
    await page.goto("/agents", { waitUntil: "commit" });

    const skeleton = page.getByTestId("sidebar-threads-skeleton");
    await expect(skeleton).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("status")).toContainText("Loading threads");

    release();
    await expect(skeleton).toBeHidden({ timeout: 30_000 });
  });

  // A persisted filter makes the "no matches" branch true before any data has
  // arrived, so the two states could otherwise render together.
  test("does not claim an empty result while the sidebar is still loading", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await page.addInitScript(() => {
      localStorage.setItem(
        "open-swe.agents.sidebar-prefs",
        JSON.stringify({ filters: { statuses: ["running"] } }),
      );
    });

    let release = () => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route("**/dashboard/api/threads/page?*", async (route) => {
      const params = new URL(route.request().url()).searchParams;
      if (params.get("resolved") === "false" && params.get("limit") === "10") {
        await held;
      }
      await route.continue();
    });

    await page.goto("/agents", { waitUntil: "commit" });

    const skeleton = page.getByTestId("sidebar-threads-skeleton");
    await expect(skeleton).toBeVisible({ timeout: 30_000 });

    // The skeleton is in the server-rendered HTML, so its presence says nothing
    // about hydration — and the persisted filter is only read on the client.
    // `useSidebarPrefs` writes the full sanitized object back on mount, so the
    // stored value gaining a key the seed never had is the hydration signal.
    await page.waitForFunction(
      () =>
        (localStorage.getItem("open-swe.agents.sidebar-prefs") ?? "").includes(
          "collapsed",
        ),
      undefined,
      { timeout: 30_000 },
    );

    await expect(skeleton).toBeVisible();
    await expect(page.getByText("No threads match these filters.")).toHaveCount(
      0,
    );

    release();
  });
});
