import { test, expect } from "@playwright/test";
import {
  OTHER_USER,
  SAME_USER,
  composerFor,
  expectTranscriptVisible,
  loginAs,
  openRunningThreadViaSlackLink,
  openThreadViaSlackLink,
  threadIdFromUrl,
  threadState,
  typeIntoComposer,
  waitForStateToContain,
  waitForThreadIdle,
} from "./helpers/dashboard";

// Drives the REAL built ui/ app (served same-origin from the harness) for the
// Slack → web handoff. Only the LLM/GitHub/Slack/token boundaries are faked.
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

  test("shows a sent Slack message before the tool call that follows it", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await page.request.post("/control/reset");

    const send = await page.request.post("/mock/slack/send", {
      data: {
        text: "<@U0BOT> E2E_SLACK_REPLY_ORDER reproduce the message order",
      },
    });
    expect(send.ok()).toBeTruthy();
    const { thread_id: threadId } = (await send.json()) as {
      thread_id: string;
    };

    await page.goto(`/agents/${threadId}`);
    const sentMessage = page.getByText("On it!", { exact: true });
    const ongoingToolCall = page.getByRole("button", {
      name: /^Running · sleep 20 · 1 action$/,
    });
    await expect(sentMessage).toBeVisible();
    await expect(ongoingToolCall).toBeVisible();

    expect(
      await sentMessage.evaluate(
        (message, toolCall) =>
          Boolean(
            message.compareDocumentPosition(toolCall) &
            Node.DOCUMENT_POSITION_FOLLOWING,
          ),
        await ongoingToolCall.elementHandle(),
      ),
    ).toBe(true);
  });

  test("keeps follow-ups visible across the queued-to-transcript handoff", async ({
    page,
  }, testInfo) => {
    await loginAs(page, SAME_USER);
    await openRunningThreadViaSlackLink(page);
    const threadId = threadIdFromUrl(page);

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
    await expect(page.getByText(queuedText).first()).toBeVisible();
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
    const threadId = threadIdFromUrl(page);

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

  test("injects sender context only when one web user's context changes", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await page.goto("/agents");
    const dismissOnboarding = page.getByRole("button", {
      name: "Maybe later",
    });
    if (await dismissOnboarding.isVisible()) await dismissOnboarding.click();
    await page.keyboard.press("Escape");

    const clearInstructions = await page.request.delete(
      "/dashboard/api/me/instructions",
      { headers: { origin: new URL(page.url()).origin } },
    );
    expect(clearInstructions.ok()).toBeTruthy();

    const editor = page.getByTestId("composer-editor");
    await editor.focus();
    await editor.pressSequentially("first sender payload message");
    await editor.press("Enter");
    await expect(page).toHaveURL(/\/agents\/[^/]+$/);
    const threadId = threadIdFromUrl(page);
    await waitForStateToContain(
      page,
      threadId,
      "This metadata was generated by Open SWE for the sender of this message.",
    );
    await waitForThreadIdle(page, threadId);
    await expect(page.getByTestId("composer-editor")).toHaveAttribute(
      "contenteditable",
      "true",
    );

    await editor.focus();
    await editor.pressSequentially("second sender payload message");
    await editor.press("Enter");
    await waitForStateToContain(
      page,
      threadId,
      "second sender payload message",
    );
    await waitForThreadIdle(page, threadId);
    await page.waitForTimeout(2_000);

    let state = await threadState(page, threadId);
    const payloadMarker =
      /This metadata was generated by Open SWE for the sender of this message\./g;
    expect(state.match(payloadMarker) ?? []).toHaveLength(1);

    const instructions = "Always use the sender preference update marker.";
    const origin = new URL(page.url()).origin;
    const instructionsResponse = await page.request.put(
      "/dashboard/api/me/instructions",
      {
        headers: { origin, referer: `${origin}/` },
        data: { instructions },
      },
    );
    expect(
      instructionsResponse.ok(),
      await instructionsResponse.text(),
    ).toBeTruthy();

    await editor.focus();
    await editor.pressSequentially("sender preference changed message");
    await editor.press("Enter");
    await waitForStateToContain(
      page,
      threadId,
      "sender preference changed message",
    );
    await waitForStateToContain(page, threadId, instructions);
    await expect
      .poll(
        async () =>
          (await threadState(page, threadId)).match(payloadMarker)?.length ?? 0,
        { timeout: 60_000, intervals: [500] },
      )
      .toBe(2);
    await waitForThreadIdle(page, threadId);

    state = await threadState(page, threadId);
    expect(state.match(payloadMarker) ?? []).toHaveLength(2);
  });

  test("keeps the submitted message and thread view visible while a new chat starts", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    const [sessionResponse, profileResponse, mappingResponse] =
      await Promise.all([
        page.request.get("/dashboard/api/me"),
        page.request.get("/dashboard/api/profile"),
        page.request.get("/dashboard/api/my-mapping"),
      ]);
    const session = (await sessionResponse.json()) as {
      slack_oauth_enabled?: boolean;
    };
    const profile = (await profileResponse.json()) as {
      default_model?: string;
    };
    const mapping = (await mappingResponse.json()) as {
      slack_user_id?: string;
    };
    const needsOnboarding =
      !profile.default_model ||
      (session.slack_oauth_enabled && !mapping.slack_user_id);
    await page.goto("/agents");
    const dismissOnboarding = page.getByRole("button", {
      name: "Maybe later",
    });
    if (needsOnboarding) {
      await expect(dismissOnboarding).toBeVisible();
      await dismissOnboarding.click();
      await expect(dismissOnboarding).toBeHidden();
    }

    const prompt = "Reproduce the new chat send experience";
    const editor = page.getByTestId("composer-editor");
    await editor.click();
    await editor.pressSequentially(prompt);
    await page.evaluate((submittedPrompt) => {
      const observations = {
        messageSeen: true,
        messageDisappeared: false,
        threadSeen: false,
        newChatReturned: false,
      };
      const visible = (element: Element) =>
        (element as HTMLElement).getClientRects().length > 0;
      const sample = () => {
        const submittedMessageVisible = Array.from(
          document.querySelectorAll(
            '[data-testid="user-message"], [data-testid="composer-editor"]',
          ),
        ).some(
          (element) =>
            visible(element) &&
            (element.textContent ?? "").includes(submittedPrompt),
        );
        const newChatVisible = Array.from(
          document.querySelectorAll('[data-testid="composer-editor"]'),
        ).some(
          (element) =>
            visible(element) &&
            element.getAttribute("aria-placeholder") ===
              "Ask Open SWE to build, fix bugs, explore",
        );

        if (/^\/agents\/[^/]+$/.test(window.location.pathname)) {
          observations.threadSeen = true;
        }
        if (submittedMessageVisible) observations.messageSeen = true;
        if (observations.messageSeen && !submittedMessageVisible) {
          observations.messageDisappeared = true;
        }
        if (
          observations.messageSeen &&
          !submittedMessageVisible &&
          newChatVisible
        ) {
          observations.newChatReturned = true;
        }
      };
      const observer = new MutationObserver(sample);
      observer.observe(document.documentElement, {
        attributes: true,
        childList: true,
        subtree: true,
      });
      window.setInterval(sample, 10);
      sample();
      Object.assign(window, { __newChatObservations: observations });
    }, prompt);

    await editor.press("Enter");
    await expect(page).toHaveURL(/\/agents\/[^/]+$/);
    await expect
      .poll(() =>
        page.evaluate(
          () =>
            (
              window as typeof window & {
                __newChatObservations: { messageSeen: boolean };
              }
            ).__newChatObservations.messageSeen,
        ),
      )
      .toBe(true);
    // The flash this guards against lands within a navigation, so a couple of
    // seconds of sampling is enough to catch it.
    await page.waitForTimeout(2_000);

    const observations = await page.evaluate(
      () =>
        (
          window as typeof window & {
            __newChatObservations: {
              messageSeen: boolean;
              messageDisappeared: boolean;
              threadSeen: boolean;
              newChatReturned: boolean;
            };
          }
        ).__newChatObservations,
    );
    expect.soft(observations.messageDisappeared).toBe(false);
    expect.soft(observations.newChatReturned).toBe(false);
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
    const threadId = threadIdFromUrl(page);

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
