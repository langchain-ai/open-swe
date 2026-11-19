import { test, expect, type Locator, type Page } from "@playwright/test";
import {
  OTHER_USER,
  SAME_USER,
  composerFor,
  dismissOnboardingIfShown,
  expectTranscriptVisible,
  loginAs,
  openRunningThreadViaSlackLink,
  optIntoQueue,
  openThreadViaSlackLink,
  readyComposerEditor,
  threadIdFromUrl,
  threadState,
  typeIntoComposer,
  waitForStateToContain,
  waitForThreadIdle,
} from "./helpers/dashboard";

// Drives the REAL built ui/ app (served same-origin from the harness) for the
// Slack → web handoff. Only the LLM/GitHub/Slack/token boundaries are faked.
test.describe("Slack → web handoff (real dashboard UI)", () => {
  test("prefills an editable handoff without sending it", async ({ page }) => {
    await loginAs(page, SAME_USER);
    const editor = page.getByTestId("composer-editor");
    const prompt =
      'Fix retries & preserve tool arguments.\n\n<!-- langsmith-issue {"id":"issue-1"} -->';
    await page.goto(`/agents?prompt=${encodeURIComponent(prompt)}`);
    await dismissOnboardingIfShown(page);
    await expect(editor).toContainText(
      "Fix retries & preserve tool arguments.",
    );
    await expect(editor).toContainText(
      '<!-- langsmith-issue {"id":"issue-1"} -->',
    );
    await expect(page).toHaveURL(/\/agents\/?$/);
    await expect(editor).toHaveAttribute("data-lexical-editor", "true");
    await editor.press("End");
    await editor.pressSequentially(" Please investigate first.");
    const edited = await editor.innerText();
    await page.getByRole("link", { name: "Automations", exact: true }).click();
    await expect(page).toHaveURL(/\/agents\/automations/);
    await page.goBack();
    await expect(editor).toHaveText(edited, { useInnerText: true });
  });

  test("opens the composer without saving a personal model for a fresh user", async ({
    page,
  }) => {
    await loginAs(page, {
      login: "workspace-default-onboarding-e2e",
      email: "workspace-default-onboarding-e2e@example.com",
    });
    const initialProfile = await page.request.get("/dashboard/api/profile");
    expect(initialProfile.ok()).toBeTruthy();
    const reset = await page.request.put("/dashboard/api/profile", {
      headers: { origin: new URL(initialProfile.url()).origin },
      data: { slack_onboarding_dismissed: false },
    });
    expect(reset.ok()).toBeTruthy();
    const sessionResponse = await page.request.get("/dashboard/api/me");
    expect(sessionResponse.ok()).toBeTruthy();
    const session = (await sessionResponse.json()) as Record<string, unknown>;
    let slackEnabled = false;
    await page.route("**/dashboard/api/me", async (route) => {
      await route.fulfill({
        json: {
          ...session,
          slack_oauth_enabled: slackEnabled,
          slack_user_id: null,
        },
      });
    });
    await page.clock.setFixedTime(Date.now() + 61_000);
    const profileBefore = await page.request.get("/dashboard/api/profile");
    expect(profileBefore.ok()).toBeTruthy();
    expect(await profileBefore.json()).not.toHaveProperty("default_model");

    await page.goto("/agents");
    await expect(page.getByTestId("composer-editor")).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.reload();
    await expect(page.getByTestId("composer-editor")).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(0);

    const profileAfter = await page.request.get("/dashboard/api/profile");
    expect(profileAfter.ok()).toBeTruthy();
    expect(await profileAfter.json()).not.toHaveProperty("default_model");

    slackEnabled = true;
    await page.clock.setFixedTime(Date.now() + 121_000);
    await page.reload();
    await expect(
      page.getByRole("button", { name: "Don't ask again" }),
    ).toBeVisible();
    const saved = page.waitForResponse(
      "**/dashboard/api/profile/slack-onboarding-dismissal",
    );
    await page.getByRole("button", { name: "Don't ask again" }).click();
    expect((await saved).ok()).toBeTruthy();
    const dismissed = await (
      await page.request.get("/dashboard/api/profile")
    ).json();
    expect(dismissed.slack_onboarding_dismissed).toBe(true);
    expect(dismissed).not.toHaveProperty("default_model");
    expect(dismissed).not.toHaveProperty("reasoning_effort");
    await page.reload();
    await expect(page.getByTestId("composer-editor")).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(0);
  });

  test("the SAME user continues the conversation in the web app", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await openThreadViaSlackLink(page);

    // The owner sees the composer (either the follow-up bar once the transcript
    // hydrates, or the empty-state bar before it — both mean they can type).
    const composer = composerFor(
      page,
      /Work with Robin|Send the first message/,
    );
    await expect(composer.editor).toBeVisible();
    await expect(composer.prompt).toBeVisible();
    // Continue from the web — a new agent reply streams into the same thread.
    await expect(composer.editor).toBeFocused();
    await page.keyboard.type("Looks good — can you also add a docstring?");
    await page.keyboard.press("Enter");
    await expect(
      page.getByText(/anything else you'd like changed/),
    ).toBeVisible();

    // The transcript that started in Slack is here too (incl. the PR link).
    const pullRequestLink = page
      .getByRole("main")
      .getByRole("link", { name: "Add greet() helper", exact: true })
      .first();
    await expect(pullRequestLink).toBeVisible();
    // The hover card exists only once the thread's PR list has loaded; the PR
    // pill renders from that same list, so its arrival means the link is a
    // hover trigger and not a plain anchor.
    await expect(
      page.getByRole("link", { name: /Open fakeorg\/demo pull request #1/ }),
    ).toBeVisible();
    // The transcript is a stick-to-bottom scroller that can still shift after
    // the reply streams in; a shift under the pointer closes the tooltip, so
    // hover again until the card stays.
    await expect(async () => {
      await pullRequestLink.hover();
      await expect(
        page.getByTestId("pr-hover-card-fakeorg/demo-1"),
      ).toBeVisible({ timeout: 2_000 });
    }).toPass({ timeout: 20_000 });
  });

  test("shows an optimistic message while the send is in flight", async ({
    page,
  }, testInfo) => {
    await loginAs(page, SAME_USER);
    await openThreadViaSlackLink(page);
    const threadId = threadIdFromUrl(page);
    await waitForThreadIdle(page, threadId);

    // Every send is a `run.start` command; hold it so the optimistic row has
    // to stand in for the message.
    let releaseSend: () => void = () => {};
    const sendReleased = new Promise<void>((resolve) => {
      releaseSend = resolve;
    });
    let sendStarted: () => void = () => {};
    const sendReceived = new Promise<void>((resolve) => {
      sendStarted = resolve;
    });
    await page.route(
      `**/dashboard/api/threads/${threadId}/commands`,
      async (route) => {
        sendStarted();
        await sendReleased;
        await route.continue();
      },
    );

    const prompt = "Show this immediately while the send is accepted.";
    await typeIntoComposer(page, prompt);
    await sendReceived;

    const optimisticMessage = page
      .getByTestId("user-message")
      .filter({ hasText: prompt });
    await expect(optimisticMessage).toBeVisible();
    await expect(optimisticMessage).toHaveAttribute(
      "data-message-delivery-status",
      "sending",
    );
    const optimisticId =
      await optimisticMessage.getAttribute("data-message-id");
    expect(optimisticId).toBeTruthy();
    const screenshotPath = testInfo.outputPath("optimistic-message-send.png");
    await page.screenshot({ path: screenshotPath, fullPage: true });
    await testInfo.attach("optimistic-message-send", {
      path: screenshotPath,
      contentType: "image/png",
    });

    releaseSend();
    await expect(optimisticMessage).toHaveCount(1);
    await waitForStateToContain(page, threadId, prompt);
    await expect(optimisticMessage).toHaveCount(1);
    await expect(optimisticMessage).toHaveAttribute(
      "data-message-id",
      optimisticId!,
    );
  });

  test("keeps a starting chat reachable after leaving the new-chat page", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await openThreadViaSlackLink(page);
    const existingThreadId = threadIdFromUrl(page);
    await page.goto(`/agents/${existingThreadId}`);
    const existingChat = page
      .getByRole("complementary")
      .locator(`a[href="/agents/${existingThreadId}"]`);
    await expect(existingChat).toBeVisible();
    const sidebar = page.getByRole("complementary");
    await sidebar
      .getByRole("button", { name: /^(Projects|Recents) options$/ })
      .first()
      .click();
    await page
      .getByRole("menuitemradio", { name: "Date", exact: true })
      .click();
    await page.keyboard.press("Escape");
    await sidebar.getByRole("button", { name: "Today", exact: true }).click();
    await page.getByRole("link", { name: "New", exact: true }).click();
    await dismissOnboardingIfShown(page);

    let releaseStart: () => void = () => {};
    const released = new Promise<void>((resolve) => {
      releaseStart = resolve;
    });
    let threadId = "";
    const listedBeforeAcceptance = page.waitForResponse(async (response) => {
      if (
        !threadId ||
        !new URL(response.url()).pathname.endsWith("/threads/page")
      )
        return false;
      const body = (await response.json()) as { items?: Array<{ id: string }> };
      return body.items?.some((thread) => thread.id === threadId) ?? false;
    });
    const prematureRequests: string[] = [];
    const recordRequest = (request: import("@playwright/test").Request) => {
      if (
        threadId &&
        request.method() === "GET" &&
        new URL(request.url()).pathname.includes(`/threads/${threadId}`)
      )
        prematureRequests.push(request.url());
    };
    page.on("request", recordRequest);
    await page.route("**/dashboard/api/threads/*/**", async (route) => {
      const url = new URL(route.request().url());
      const match = url.pathname.match(/\/threads\/([^/]+)\/commands$/);
      if (match && route.request().method() === "POST") {
        threadId = match[1]!;
        const response = await route.fetch();
        await released;
        await route.fulfill({ response });
        return;
      }
      await route.continue();
    });

    let releaseSidebar: () => void = () => {};
    const sidebarReleased = new Promise<void>((resolve) => {
      releaseSidebar = resolve;
    });
    let heldSidebarRequests = 0;
    const prompt = "Keep this new chat reachable during startup.";
    try {
      await typeIntoComposer(page, prompt);
      await expect.poll(() => threadId).not.toBe("");
      const pendingChat = sidebar.locator(`a[href="/agents/${threadId}"]`);
      const recentSection = sidebar.locator("section").filter({
        has: page.getByRole("button", { name: "Today", exact: true }),
      });
      await expect(
        recentSection.locator(`a[href="/agents/${threadId}"]`),
      ).toBeVisible();
      await existingChat.click();
      await expect(page).toHaveURL(new RegExp(`/agents/${existingThreadId}$`));
      await expect(pendingChat).toHaveCount(1);
      await expect(
        pendingChat.getByRole("img", { name: "Starting chat" }),
      ).toBeVisible();
      await expect(sidebar.getByText("Starting", { exact: true })).toHaveCount(
        0,
      );
      await expect(
        recentSection.locator(`a[href="/agents/${threadId}"]`),
      ).toBeVisible();
      await sidebar.getByRole("button", { name: "Today", exact: true }).click();
      await expect(pendingChat).toHaveCount(0);
      await sidebar.getByRole("button", { name: "Today", exact: true }).click();
      for (const [menu, grouping] of [
        ["Recents options", "Project"],
        ["Projects options", "Date"],
      ]) {
        await sidebar.getByRole("button", { name: menu, exact: true }).click();
        await page
          .getByRole("menuitemradio", { name: grouping, exact: true })
          .click();
        await page.keyboard.press("Escape");
        await expect(pendingChat).toHaveCount(1);
        await expect(pendingChat).toBeVisible();
      }
      await expect(
        recentSection.locator(`a[href="/agents/${threadId}"]`),
      ).toBeVisible();
      await listedBeforeAcceptance;
      await expect(
        pendingChat.getByRole("img", { name: "Starting chat" }),
      ).toBeVisible();
      await pendingChat.click();
      await expect(page).toHaveURL(new RegExp(`/agents/${threadId}$`));
      await expect(
        page.getByRole("main").getByText(prompt, { exact: true }).last(),
      ).toBeVisible();
      await expect(
        page.getByRole("button", { name: "Stop run" }),
      ).toBeVisible();
      const pendingRow = pendingChat.locator("..");
      await expect(pendingRow.getByRole("button")).toHaveCount(0);
      await expect(pendingChat).not.toHaveAttribute("data-selectable-thread");
      await expect(pendingChat).toHaveAttribute("draggable", "false");
      await expect(pendingRow.locator("xpath=..")).not.toHaveAttribute(
        "draggable",
        "true",
      );
      expect(prematureRequests).toEqual([]);
      page.off("request", recordRequest);
      await page.route(
        /\/dashboard\/api\/threads\/(page|pinned|repos)(\?|$)/,
        async (route) => {
          heldSidebarRequests++;
          await sidebarReleased;
          await route.continue();
        },
      );
      await page.getByRole("link", { name: "New", exact: true }).click();
      releaseStart();
      await expect.poll(() => heldSidebarRequests).toBeGreaterThan(0);
      await expect(page).toHaveURL(/\/agents\/?$/);
      await pendingChat.click();
      await expect(
        pendingChat.getByRole("img", { name: "Starting chat" }),
      ).toHaveCount(0);
      await expect(pendingChat).toHaveCount(1);
      await expect(
        page.getByTestId("user-message").filter({ hasText: prompt }),
      ).toBeVisible();
      await expect(
        page.getByRole("button", { name: "Add attachments" }),
      ).toBeVisible();
      await waitForStateToContain(page, threadId, prompt);
      await existingChat.click();
      await expect(pendingChat).toHaveCount(1);
      await pendingChat.click();
      await expect(page.getByTestId("composer-editor")).toHaveAttribute(
        "contenteditable",
        "true",
      );
      const listed = page.waitForResponse(async (response) => {
        if (!new URL(response.url()).pathname.endsWith("/threads/page"))
          return false;
        const body = (await response.json()) as {
          items?: Array<{ id: string }>;
        };
        return body.items?.some((thread) => thread.id === threadId) ?? false;
      });
      releaseSidebar();
      await listed;
      await expect(pendingChat).toHaveCount(1);
      await expect(page.getByText("Unable to load thread")).toHaveCount(0);
      await expect(page.getByText("Unable to display thread")).toHaveCount(0);
    } finally {
      page.off("request", recordRequest);
      releaseStart();
      releaseSidebar();
      await page.removeAllListeners("response", { behavior: "wait" });
    }
  });

  test("opens an accepted chat and stops it without waiting for sidebar refreshes", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.emulateMedia({ colorScheme: "dark" });
    await loginAs(page, SAME_USER);
    await page.goto("/agents");
    await dismissOnboardingIfShown(page);
    let releaseSidebar: () => void = () => {};
    const sidebarReleased = new Promise<void>((resolve) => {
      releaseSidebar = resolve;
    });
    let heldSidebarRequests = 0;
    await page.route(
      /\/dashboard\/api\/threads\/(page|pinned|repos)(\?|$)/,
      async (route) => {
        heldSidebarRequests++;
        await sidebarReleased;
        await route.continue();
      },
    );
    try {
      const prompt = "E2E_BUSY_HOLD:30 keep this accepted chat usable.";
      await typeIntoComposer(page, prompt);
      await expect.poll(() => heldSidebarRequests).toBeGreaterThan(0);
      await expect(page).toHaveURL(/\/agents\/[0-9a-f-]{36}$/);
      const threadId = threadIdFromUrl(page);
      const row = page
        .getByRole("complementary")
        .locator(`a[href="/agents/${threadId}"]`);
      await expect(row).toHaveCount(1);
      await expect(row.getByRole("img", { name: "Starting chat" })).toHaveCount(
        0,
      );
      await expect(
        page.getByTestId("user-message").filter({ hasText: prompt }),
      ).toBeVisible();
      await expect(page.getByTestId("composer-editor")).toHaveAttribute(
        "contenteditable",
        "true",
      );
      await expect(page.getByText("On it!", { exact: true })).toBeVisible();
      await test.info().attach("accepted-with-sidebar-held", {
        body: await page.screenshot(),
        contentType: "image/png",
      });
      await page.getByRole("button", { name: "Stop run" }).click();
      await expect(page.getByRole("button", { name: "Stop run" })).toHaveCount(
        0,
      );
      await waitForThreadIdle(page, threadId);
      await expect(
        row.getByRole("img", { name: "Thread running" }),
      ).toHaveCount(0);
      await row.click({ button: "right" });
      await page.getByRole("menuitem", { name: "Delete", exact: true }).click();
      await page
        .getByRole("dialog", { name: "Delete thread" })
        .getByRole("button", { name: "Delete", exact: true })
        .click();
      await expect(page).toHaveURL(/\/agents\/?$/);
      await expect(row).toHaveCount(0);
      releaseSidebar();
    } finally {
      releaseSidebar();
    }
  });

  test("honors Stop before acceptance while sidebar refreshes are held", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.emulateMedia({ colorScheme: "dark" });
    await loginAs(page, SAME_USER);
    let releaseStart: () => void = () => {};
    const started = new Promise<void>((resolve) => {
      releaseStart = resolve;
    });
    let threadId = "";
    await page.route("**/dashboard/api/threads/*/commands", async (route) => {
      threadId = new URL(route.request().url()).pathname.split("/").at(-2)!;
      await started;
      await route.continue();
    });
    let releaseSidebar: () => void = () => {};
    const sidebarReleased = new Promise<void>((resolve) => {
      releaseSidebar = resolve;
    });
    await page.route(
      /\/dashboard\/api\/threads\/(page|pinned|repos)(\?|$)/,
      async (route) => {
        await sidebarReleased;
        await route.continue();
      },
    );
    try {
      await page.goto("/agents");
      await dismissOnboardingIfShown(page);
      await expect(
        page.getByRole("status", { name: "Loading pinned threads" }),
      ).toBeVisible();
      await typeIntoComposer(
        page,
        "E2E_BUSY_HOLD:30 stop this chat during startup.",
      );
      await expect.poll(() => threadId).not.toBe("");
      const sidebar = page.getByRole("complementary");
      const row = sidebar.locator(`a[href="/agents/${threadId}"]`);
      await expect(row).toBeVisible();
      await sidebar.getByRole("button", { name: "Projects options" }).click();
      await page
        .getByRole("menuitemradio", { name: "Date", exact: true })
        .click();
      await expect(page.getByRole("menu")).toHaveCount(0);
      await expect(row).toBeVisible();
      await test.info().attach("starting-with-cold-sidebar", {
        body: await page.screenshot(),
        contentType: "image/png",
      });
      await page.getByRole("button", { name: "Stop run" }).click();
      await expect(page.getByTestId("composer-editor")).toHaveAttribute(
        "contenteditable",
        "true",
      );
      const cancelled = page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname ===
          `/dashboard/api/threads/${threadId}/cancel`,
      );
      releaseStart();
      expect((await cancelled).ok()).toBeTruthy();
      await expect(page).toHaveURL(/\/agents\/?$/);
      await page
        .getByRole("complementary")
        .locator(`a[href="/agents/${threadId}"]`)
        .click();
      await expect(page.getByRole("button", { name: "Stop run" })).toHaveCount(
        0,
      );
      await expect(page.getByTestId("composer-editor")).toHaveAttribute(
        "contenteditable",
        "true",
      );
    } finally {
      releaseStart();
      releaseSidebar();
    }
  });

  test("recovers from startup failures on Home and on the pending chat route", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.emulateMedia({ colorScheme: "dark" });
    await loginAs(page, SAME_USER);
    await page.goto("/agents");
    await dismissOnboardingIfShown(page);
    let releaseFailure: () => void = () => {};
    const failureReleased = new Promise<void>((resolve) => {
      releaseFailure = resolve;
    });
    let attempt = 0;
    let failedThreadId = "";
    const prematureReads: string[] = [];
    page.on("request", (request) => {
      if (
        failedThreadId &&
        request.method() === "GET" &&
        new URL(request.url()).pathname.includes(
          `/dashboard/api/threads/${failedThreadId}`,
        )
      )
        prematureReads.push(request.url());
    });
    await page.route("**/dashboard/api/threads/*/commands", async (route) => {
      attempt++;
      if (attempt > 2) {
        await route.continue();
        return;
      }
      failedThreadId =
        new URL(route.request().url()).pathname.split("/").at(-2) ?? "";
      if (attempt === 2) await failureReleased;
      await route.fulfill({
        status: 503,
        json: { detail: "Startup temporarily unavailable" },
      });
    });
    try {
      await typeIntoComposer(page, "Recover from a rejected startup.");
      await expect(
        page.getByText("Startup temporarily unavailable", { exact: true }),
      ).toBeVisible();
      const sidebar = page.getByRole("complementary");
      await expect(
        sidebar.locator(`a[href="/agents/${failedThreadId}"]`),
      ).toHaveCount(0);
      await expect(page.getByTestId("composer-editor")).toHaveAttribute(
        "contenteditable",
        "true",
      );
      await typeIntoComposer(page, "Recover after opening the starting chat.");
      await expect.poll(() => attempt).toBe(2);
      const pendingChat = sidebar.locator(
        `a[href="/agents/${failedThreadId}"]`,
      );
      await pendingChat.click();
      await expect(page).toHaveURL(new RegExp(`/agents/${failedThreadId}$`));
      await expect(
        page.getByRole("button", { name: "Stop run" }),
      ).toBeVisible();
      releaseFailure();
      await expect(page).toHaveURL(/\/agents\/?$/);
      await expect(
        page.getByText("Could not start the Robin agent", { exact: true }),
      ).toBeVisible();
      await expect(pendingChat).toHaveCount(0);
      await expect(page.getByText("Unable to load thread")).toHaveCount(0);
      await expect(page.getByText("Unable to display thread")).toHaveCount(0);
      expect(prematureReads).toEqual([]);
      await test.info().attach("startup-failure-recovered", {
        body: await page.screenshot(),
        contentType: "image/png",
      });
      await typeIntoComposer(page, "This retry should start normally.");
      await expect(page).toHaveURL(/\/agents\/[0-9a-f-]{36}$/);
      await expect(
        page
          .getByTestId("user-message")
          .filter({ hasText: "This retry should start normally." }),
      ).toBeVisible();
    } finally {
      releaseFailure();
    }
  });

  test("reports a failed startup without leaving the page navigated to", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await page.goto("/agents");
    await dismissOnboardingIfShown(page);
    let releaseFailure: () => void = () => {};
    const failureReleased = new Promise<void>((resolve) => {
      releaseFailure = resolve;
    });
    let releaseAutomations: () => void = () => {};
    const automationsReleased = new Promise<void>((resolve) => {
      releaseAutomations = resolve;
    });
    let threadId = "";
    await page.route("**/dashboard/api/threads/*/commands", async (route) => {
      threadId =
        new URL(route.request().url()).pathname.split("/").at(-2) ?? "";
      await failureReleased;
      await route.fulfill({
        status: 503,
        json: { detail: "Startup failed after navigation" },
      });
    });
    // While the next route's code loads, the URL has moved but Home is still
    // mounted; holding that code makes the failure land in that window.
    let automationsHeld = false;
    await page.route("**/assets/automations-*.js", async (route) => {
      automationsHeld = true;
      await automationsReleased;
      await route.continue();
    });
    try {
      await typeIntoComposer(page, "Do not take me away if startup fails.");
      await expect.poll(() => threadId).not.toBe("");
      const row = page
        .getByRole("complementary")
        .locator(`a[href="/agents/${threadId}"]`);
      await expect(row).toBeVisible();
      await page
        .getByRole("link", { name: "Automations", exact: true })
        .click();
      await expect(page).toHaveURL(/\/agents\/automations/);
      await expect.poll(() => automationsHeld).toBe(true);
      releaseFailure();
      await expect(row).toHaveCount(0);
      releaseAutomations();
      await expect(
        page.getByRole("heading", { name: "Automations", level: 1 }),
      ).toBeVisible();
      await expect(
        page.getByText("Could not start the Robin agent", { exact: true }),
      ).toBeVisible();
      await expect(
        page.getByText("Startup failed after navigation", { exact: true }),
      ).toBeVisible();
      await expect(page).toHaveURL(/\/agents\/automations/);
    } finally {
      releaseFailure();
      releaseAutomations();
    }
  });

  test("keeps the newer chat open when an older startup finishes last", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await page.goto("/agents");
    await dismissOnboardingIfShown(page);
    let releaseOlder: () => void = () => {};
    const olderReleased = new Promise<void>((resolve) => {
      releaseOlder = resolve;
    });
    let olderId = "";
    await page.route("**/dashboard/api/threads/*/commands", async (route) => {
      if (!olderId) {
        olderId =
          new URL(route.request().url()).pathname.split("/").at(-2) ?? "";
        await olderReleased;
      }
      await route.continue();
    });
    try {
      await typeIntoComposer(
        page,
        "The older startup must not steal navigation.",
      );
      await expect.poll(() => olderId).not.toBe("");
      const olderRow = page
        .getByRole("complementary")
        .locator(`a[href="/agents/${olderId}"]`);
      await olderRow.click();
      await page.getByRole("link", { name: "New", exact: true }).click();
      await typeIntoComposer(page, "Keep the newer chat open.");
      await expect(page).toHaveURL(/\/agents\/[0-9a-f-]{36}$/);
      const newerId = threadIdFromUrl(page);
      expect(newerId).not.toBe(olderId);
      await expect(
        page
          .getByTestId("user-message")
          .filter({ hasText: "Keep the newer chat open." }),
      ).toBeVisible();
      releaseOlder();
      await expect(
        olderRow.getByRole("img", { name: "Starting chat" }),
      ).toHaveCount(0);
      await expect(olderRow).toHaveCount(1);
      await expect(page).toHaveURL(new RegExp(`/agents/${newerId}$`));
      await olderRow.click();
      await expect(
        page
          .getByTestId("user-message")
          .filter({ hasText: "The older startup must not steal navigation." }),
      ).toBeVisible();
      await expect(
        page.getByRole("complementary").locator(`a[href="/agents/${newerId}"]`),
      ).toHaveCount(1);
    } finally {
      releaseOlder();
    }
  });

  test("recovers when deferred Stop fails and retries from the accepted chat", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.emulateMedia({ colorScheme: "dark" });
    await loginAs(page, SAME_USER);
    await page.goto("/agents");
    await dismissOnboardingIfShown(page);
    let releaseStart: () => void = () => {};
    const startReleased = new Promise<void>((resolve) => {
      releaseStart = resolve;
    });
    let releaseSidebar: () => void = () => {};
    const sidebarReleased = new Promise<void>((resolve) => {
      releaseSidebar = resolve;
    });
    let threadId = "";
    let cancellations = 0;
    await page.route("**/dashboard/api/threads/*/commands", async (route) => {
      threadId =
        new URL(route.request().url()).pathname.split("/").at(-2) ?? "";
      await startReleased;
      await route.continue();
    });
    await page.route("**/dashboard/api/threads/*/cancel", async (route) => {
      cancellations++;
      if (cancellations === 1)
        await route.fulfill({
          status: 503,
          json: { detail: "Stop temporarily unavailable" },
        });
      else await route.continue();
    });
    await page.route(
      /\/dashboard\/api\/threads\/(page|pinned|repos)(\?|$)/,
      async (route) => {
        await sidebarReleased;
        await route.continue();
      },
    );
    try {
      const prompt = "E2E_BUSY_HOLD:30 retry Stop after startup.";
      await typeIntoComposer(page, prompt);
      await expect.poll(() => threadId).not.toBe("");
      const row = page
        .getByRole("complementary")
        .locator(`a[href="/agents/${threadId}"]`);
      await row.click();
      await page.getByRole("button", { name: "Stop run" }).click();
      await expect(
        page.getByText("Stopping after startup…", { exact: true }),
      ).toBeVisible();
      await expect(
        row.getByRole("img", { name: "Stopping after startup" }),
      ).toBeVisible();
      expect(cancellations).toBe(0);
      await test.info().attach("stopping-before-acceptance", {
        body: await page.screenshot(),
        contentType: "image/png",
      });
      releaseStart();
      await expect(
        page.getByText("Could not stop the new chat", { exact: true }),
      ).toBeVisible();
      await expect(page).toHaveURL(new RegExp(`/agents/${threadId}$`));
      await expect(
        row.getByRole("img", { name: "Stopping after startup" }),
      ).toHaveCount(0);
      await expect(
        page.getByTestId("user-message").filter({ hasText: prompt }),
      ).toBeVisible();
      await expect(page.getByTestId("composer-editor")).toHaveAttribute(
        "contenteditable",
        "true",
      );
      await expect(
        page.getByRole("button", { name: "Stop run" }),
      ).toBeVisible();
      await test.info().attach("accepted-after-stop-failure", {
        body: await page.screenshot(),
        contentType: "image/png",
      });
      await page
        .getByRole("button", { name: "Close toast", exact: true })
        .click();
      await page.getByRole("button", { name: "Stop run" }).click();
      await expect.poll(() => cancellations).toBe(2);
      await expect(page.getByRole("button", { name: "Stop run" })).toHaveCount(
        0,
      );
      await waitForThreadIdle(page, threadId);
      await expect(row).toHaveCount(1);
      await expect(
        row.getByRole("img", { name: "Thread running" }),
      ).toHaveCount(0);
    } finally {
      releaseStart();
      releaseSidebar();
    }
  });

  test("keeps an early-listed pinned startup navigation-only until acceptance", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.emulateMedia({ colorScheme: "dark" });
    await loginAs(page, SAME_USER);
    await openThreadViaSlackLink(page);
    const existingId = threadIdFromUrl(page);
    const sidebar = page.getByRole("complementary");
    const existingRow = sidebar.locator(`a[href="/agents/${existingId}"]`);
    await existingRow.click({ button: "right" });
    await page.getByRole("menuitem", { name: "Pin", exact: true }).click();
    const pinned = sidebar.locator("section").filter({
      has: page.getByRole("button", { name: "Pinned", exact: true }),
    });
    await expect(
      pinned.locator(`a[href="/agents/${existingId}"]`),
    ).toBeVisible();
    await page.getByRole("link", { name: "New", exact: true }).click();
    await dismissOnboardingIfShown(page);
    let releaseStart: () => void = () => {};
    const startReleased = new Promise<void>((resolve) => {
      releaseStart = resolve;
    });
    let threadId = "";
    let pinnedOnServer = false;
    await page.route("**/dashboard/api/threads/*/commands", async (route) => {
      threadId =
        new URL(route.request().url()).pathname.split("/").at(-2) ?? "";
      const response = await route.fetch();
      const pin = await page.request.post(
        `/dashboard/api/threads/${threadId}/pin`,
        {
          headers: { origin: new URL(route.request().url()).origin },
        },
      );
      expect(pin.ok()).toBeTruthy();
      pinnedOnServer = true;
      await startReleased;
      await route.fulfill({ response });
    });
    let reorders = 0;
    await page.route("**/dashboard/api/threads/pinned/order", async (route) => {
      reorders++;
      await route.continue();
    });
    try {
      await expect(
        page.getByRole("heading", { name: "What should we work on?" }),
      ).toBeVisible();
      await typeIntoComposer(
        page,
        "Keep this pinned chat safe before acceptance.",
      );
      await expect.poll(() => threadId).not.toBe("");
      await expect.poll(() => pinnedOnServer).toBe(true);
      await existingRow.click({ button: "right" });
      await page.getByRole("menuitem", { name: "Unpin", exact: true }).click();
      await existingRow.click({ button: "right" });
      await page.getByRole("menuitem", { name: "Pin", exact: true }).click();
      const pendingRow = pinned.locator(`a[href="/agents/${threadId}"]`);
      await expect(pendingRow).toBeVisible();
      await expect(
        sidebar.locator(`a[href="/agents/${threadId}"]`),
      ).toHaveCount(1);
      await expect(
        pendingRow.getByRole("img", { name: "Starting chat" }),
      ).toBeVisible();
      await expect(pendingRow).not.toHaveAttribute("data-selectable-thread");
      await expect(pendingRow.locator("..").locator("..")).not.toHaveAttribute(
        "draggable",
        "true",
      );
      await pendingRow.press("Alt+ArrowDown");
      await existingRow.press("Alt+ArrowUp");
      expect(reorders).toBe(0);
      await pendingRow.click();
      await expect(page).toHaveURL(new RegExp(`/agents/${threadId}$`));
      await expect(page.getByText("1 selected", { exact: true })).toHaveCount(
        0,
      );
      await expect(
        page.getByRole("button", { name: "Stop run" }),
      ).toBeVisible();
      await test.info().attach("pinned-before-acceptance", {
        body: await page.screenshot(),
        contentType: "image/png",
      });
      releaseStart();
      await expect(
        pendingRow.getByRole("img", { name: "Starting chat" }),
      ).toHaveCount(0);
      await expect(pendingRow).toHaveAttribute(
        "data-selectable-thread",
        threadId,
      );
      await existingRow.press("Alt+ArrowUp");
      await expect.poll(() => reorders).toBe(1);
      await expect(
        sidebar.locator(`a[href="/agents/${threadId}"]`),
      ).toHaveCount(1);
    } finally {
      releaseStart();
    }
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

  test("keeps grouped live work after its fold row", async ({ page }) => {
    await loginAs(page, SAME_USER);
    await page.request.post("/control/reset");

    const send = await page.request.post("/mock/slack/send", {
      data: {
        text: "<@U0BOT> E2E_SLACK_REPLY_GROUPED_ORDER reproduce grouped order",
      },
    });
    expect(send.ok()).toBeTruthy();
    const { thread_id: threadId } = (await send.json()) as {
      thread_id: string;
    };

    await page.goto(`/agents/${threadId}`);
    const sentMessage = page.getByText("On it!", { exact: true });
    await expect(sentMessage).toBeVisible();
    // Stop only once the delegated task is under way: the model loop runs on
    // Managed Deep Agents, so its start reaches the transcript a hop after the reply.
    await expect(
      page.getByRole("button", { name: /^Delegating .* · 1 action$/ }),
    ).toBeVisible();
    const stopRun = page.getByRole("button", { name: "Stop run" });
    await expect(stopRun).toBeVisible();
    await stopRun.click();

    const workFold = page.getByRole("button", {
      name: /^Worked(?: for .*)? · 1 action$/,
    });
    const liveGroupedWork = page.getByText("Task", { exact: true });
    await expect(workFold).toBeVisible();
    await expect(liveGroupedWork).toBeVisible();

    expect(
      await workFold.evaluate(
        (fold, groupedWork) =>
          Boolean(
            fold.compareDocumentPosition(groupedWork) &
            Node.DOCUMENT_POSITION_FOLLOWING,
          ),
        await liveGroupedWork.elementHandle(),
      ),
    ).toBe(true);
  });

  test("keeps the live activity row below a Slack reply sent mid-run", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await page.request.post("/control/reset");
    const threadId = await sendSegmentedReplies(page);

    await page.goto(`/agents/${threadId}`);
    await expectInDocumentOrder([
      page.getByText("On it!", { exact: true }),
      page.getByRole("button", { name: /^Worked(?: for .*)? · 1 action$/ }),
      page.getByText("Found the cause.", { exact: true }),
      page.getByRole("button", { name: /^Running · sleep 10 · 1 action$/ }),
    ]);
  });

  test("folds each stretch of settled work between its replies", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await page.request.post("/control/reset");
    const threadId = await sendSegmentedReplies(page);

    await page.goto(`/agents/${threadId}`);
    await expect(page.getByText("Fixed it.", { exact: true })).toBeVisible({
      timeout: 60_000,
    });
    await waitForThreadIdle(page, threadId);

    const folds = page.getByRole("button", {
      name: /^Worked(?: for .*)? · 1 action$/,
    });
    await expect(folds).toHaveCount(2);
    const ordered = [
      page.getByText("On it!", { exact: true }),
      page.getByText("Exploring before reporting progress.", { exact: true }),
      folds.first(),
      page.getByText("Reporting progress mid-run.", { exact: true }),
      page.getByText("Found the cause.", { exact: true }),
      page.getByText(
        "Running a long fix while the progress reply is on screen.",
        { exact: true },
      ),
      folds.last(),
      page.getByText("Reporting the outcome.", { exact: true }),
      page.getByText("Fixed it.", { exact: true }),
    ];
    await expectInDocumentOrder(ordered);
    await page.reload();
    await expectInDocumentOrder(ordered);
  });

  test("keeps follow-ups visible across the queued-to-transcript handoff", async ({
    page,
  }, testInfo) => {
    await loginAs(page, SAME_USER);
    await optIntoQueue(page);
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
    await expect(queuedMessage).toHaveAttribute("data-queued-pending", "false");
    await page.reload();
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
    await dismissOnboardingIfShown(page);

    const prompt = "list my open langchainplus PRs";
    await typeIntoComposer(page, prompt);
    await expect(page).toHaveURL(/\/agents\/[^/]+$/);
    const threadId = threadIdFromUrl(page);

    const userMessage = page
      .getByTestId("user-message")
      .filter({ hasText: prompt });
    await expect(userMessage).toContainText(prompt);
    await expect(userMessage).not.toContainText("sender_context");
    // The state comes back JSON-encoded, so the attribute quotes are escaped.
    await waitForStateToContain(
      page,
      threadId,
      '<dynamic-context kind=\\"person\\"',
    );

    await page.reload();
    await expect(userMessage).toContainText(prompt);
    await expect(userMessage).not.toContainText("sender_context");
  });

  // The roster states everything about the people once, and repeats a person's
  // block only when something about them changes.
  test("re-emits a person block only when that person changes", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await page.goto("/agents");
    await dismissOnboardingIfShown(page);

    const clearInstructions = await page.request.delete(
      "/dashboard/api/me/instructions",
      { headers: { origin: new URL(page.url()).origin } },
    );
    expect(clearInstructions.ok()).toBeTruthy();

    // Every person block the thread holds, so a repeat fails with the diff, not a count.
    const rosterBlocks = (state: string): string[] => {
      const parsed = JSON.parse(state) as {
        values?: { messages?: Array<{ content?: unknown }> };
      };
      return (parsed.values?.messages ?? [])
        .map((message) =>
          typeof message.content === "string" ? message.content : "",
        )
        .filter((content) =>
          content.includes('<dynamic-context kind="person"'),
        );
    };
    const expectOneRoster = (state: string) => {
      const blocks = rosterBlocks(state);
      expect(
        blocks,
        blocks.join("\n\n=== next roster block ===\n\n"),
      ).toHaveLength(1);
    };

    const editor = await readyComposerEditor(page);
    await editor.focus();
    await editor.pressSequentially("first sender payload message");
    await editor.press("Enter");
    await expect(page).toHaveURL(/\/agents\/[^/]+$/);
    const threadId = threadIdFromUrl(page);
    // The state comes back JSON-encoded, so the attribute quotes are escaped.
    await waitForStateToContain(
      page,
      threadId,
      '<dynamic-context kind=\\"person\\"',
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

    expectOneRoster(await threadState(page, threadId));

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
    await waitForThreadIdle(page, threadId);

    // The sender's standing instructions changed, so their person block —
    // where they live — is re-sent once.
    const state = await threadState(page, threadId);
    const rosters = rosterBlocks(state);
    expect(
      rosters,
      rosters.join("\n\n=== next roster block ===\n\n"),
    ).toHaveLength(2);
    expect(rosters[0]).not.toContain("standing_instructions:");
    expect(rosters[1]).toContain(`standing_instructions: ${instructions}`);
  });

  test("keeps the submitted message and thread view visible while a new chat starts", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await page.route("**/dashboard/api/options", (route) =>
      route.fulfill({ status: 503, json: { detail: "Options unavailable" } }),
    );
    await page.goto("/agents");
    await dismissOnboardingIfShown(page);
    await expect(
      page.getByText("Could not load model settings. Reload to try again."),
    ).toBeVisible();

    const prompt = "Reproduce the new chat send experience";
    const editor = await readyComposerEditor(page);
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
              "Ask Robin to build, fix bugs, explore",
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

  // Stopping a run must not strand what the user queued behind it: the queue
  // goes back into the composer, where the user decides what to do with it.
  test("returns a queued follow-up to the composer when the user stops the active run", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await optIntoQueue(page);
    await openRunningThreadViaSlackLink(page);

    const queuedText = "Please pick this up once the current run stops.";
    const busyComposer = composerFor(page, /Send a message to queue next/);
    await expect(async () => {
      await page.reload();
      await expect(busyComposer.prompt).toBeVisible({ timeout: 8000 });
    }).toPass({ timeout: 60000 });
    await typeIntoComposer(page, queuedText);
    await expect(
      page
        .getByTestId("queued-message")
        .filter({ hasText: queuedText })
        .and(page.locator("[data-queued-pending='false']")),
    ).toBeVisible();

    await page.getByRole("button", { name: "Stop run" }).click();

    await expect(page.getByTestId("queued-message")).toHaveCount(0, {
      timeout: 30_000,
    });
    await expect(page.getByTestId("composer-editor")).toContainText(
      queuedText,
      { timeout: 30_000 },
    );
    await expect(
      page.getByTestId("user-message").filter({ hasText: queuedText }),
    ).toHaveCount(0);
  });

  test("restores a queued follow-up after stopping a browser-started run and answers when resent", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await page.request.post("/control/reset");
    await optIntoQueue(page);
    await page.goto("/agents");
    await dismissOnboardingIfShown(page);

    // Long enough a hold that queueing and stopping both land mid-run.
    await typeIntoComposer(
      page,
      "E2E_BUSY_HOLD:15 please add a greet() helper and open a PR",
    );
    await expect(page).toHaveURL(/\/agents\/[0-9a-f-]{36}$/, {
      timeout: 30_000,
    });

    const queuedText = "Please pick this up once the current run stops.";
    // The run is past its Slack acknowledgement and inside the hold.
    await expect(page.getByText("On it!", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Stop run" })).toBeVisible();
    await typeIntoComposer(page, queuedText);
    await expect(
      page
        .getByTestId("queued-message")
        .filter({ hasText: queuedText })
        .and(page.locator("[data-queued-pending='false']")),
    ).toBeVisible();

    await page.getByRole("button", { name: "Stop run" }).click();

    await expect(page.getByTestId("queued-message")).toHaveCount(0, {
      timeout: 30_000,
    });
    await expect(page.getByTestId("composer-editor")).toContainText(
      queuedText,
      { timeout: 30_000 },
    );
    await expect(
      page.getByTestId("user-message").filter({ hasText: queuedText }),
    ).toHaveCount(0);

    await page
      .getByRole("button", { name: "Send message", exact: true })
      .click();

    await expect(
      page.getByTestId("user-message").filter({ hasText: queuedText }),
    ).toBeVisible({ timeout: 30_000 });
    await expect(
      page.getByText(/anything else you'd like changed/),
    ).toBeVisible({ timeout: 30_000 });
  });

  test("previews each collapsed reasoning block on its toggle", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await openThreadViaSlackLink(page, {
      message: "<@U0BOT> E2E_THOUGHT please add a greet() helper and open a PR",
    });
    await expectTranscriptVisible(page);

    for (const fold of await page
      .getByRole("button", { name: /^Worked(?: for .+)? · \d+ actions?$/ })
      .all()) {
      await fold.click();
    }

    const toggle = page
      .getByRole("button", { name: "Thought Checking the snapshot status" })
      .first();
    await expect(toggle).toBeVisible();
    await expect(
      page.getByText(/compare it with the latest capture/),
    ).toHaveCount(0);
    await test.info().attach("collapsed-reasoning", {
      body: await page.screenshot(),
      contentType: "image/png",
    });

    await toggle.click();
    await expect(
      page.getByText(/compare it with the latest capture/).first(),
    ).toBeVisible();
    await expect(
      page.getByText("**Checking the snapshot status**"),
    ).toHaveCount(0);
    await test.info().attach("expanded-reasoning", {
      body: await page.screenshot(),
      contentType: "image/png",
    });
  });

  test("stops a Slack-started run from the web app", async ({ page }) => {
    await loginAs(page, SAME_USER);
    await page.goto("/mock/slack");
    const [reset] = await Promise.all([
      page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          new URL(response.url()).pathname === "/control/reset",
      ),
      page.locator("#reset").click(),
    ]);
    expect(reset.ok()).toBeTruthy();

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
    await expect(page.getByText("On it!", { exact: true })).toBeVisible();
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
    const [reset] = await Promise.all([
      page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          new URL(response.url()).pathname === "/control/reset",
      ),
      page.locator("#reset").click(),
    ]);
    expect(reset.ok()).toBeTruthy();

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
    await expect(page.getByText("On it!", { exact: true })).toBeVisible();
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
      /Work with Robin|Send the first message/,
    );
    await expect(composer.editor).toBeVisible();
    await expect(composer.prompt).toBeVisible();

    // Posting starts a new run — the agent's follow-up reply streams in.
    const followUp = "Can you also add a docstring?";
    await typeIntoComposer(page, followUp);
    await waitForStateToContain(page, threadId, followUp);

    // The non-owner's message is attributed server-side to the person the run
    // describes, so the owner can tell who sent it. Read it from the transcript
    // the server stored: in the sender's own session the bubble is still the
    // SDK's optimistic echo of what they typed, which carries no envelope.
    await waitForStateToContain(
      page,
      threadId,
      `display_name: ${OTHER_USER.name}`,
    );
    await page.reload();
    await expect(
      page
        .getByTestId("user-message")
        .filter({ hasText: followUp })
        .getByText(OTHER_USER.name, { exact: true }),
    ).toBeVisible();
  });

  test("shows each sidebar section once earlier sections have settled", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    const id = "75000000-0000-4000-8000-000000000001";
    const now = Date.now();
    await page.request.delete(`/threads/${id}`);
    const created = await page.request.post("/threads", {
      data: {
        thread_id: id,
        if_exists: "raise",
        metadata: {
          owner_login: SAME_USER.login,
          participant_logins: { [SAME_USER.login]: true },
          title: "Sidebar pinned thread",
          source: "dashboard",
          origin: "dashboard",
          thread_category: "interactive",
          trigger_kind: "user",
          created_at_ms: now,
          updated_at_ms: now,
        },
      },
    });
    expect(created.ok()).toBeTruthy();
    const pin = await page.request.post(`/dashboard/api/threads/${id}/pin`, {
      headers: { origin: new URL(created.url()).origin },
    });
    expect(pin.ok()).toBeTruthy();
    await page.addInitScript(() => {
      localStorage.setItem(
        "open-swe.agents.sidebar-prefs",
        JSON.stringify({ organize: "date", collapsedSectionKeys: ["pinned"] }),
      );
    });
    let releaseRecents = () => {};
    let releasePlugins = () => {};
    let releasePinned = () => {};
    const heldRecents = new Promise<void>((resolve) => {
      releaseRecents = resolve;
    });
    const heldPlugins = new Promise<void>((resolve) => {
      releasePlugins = resolve;
    });
    const heldPinned = new Promise<void>((resolve) => {
      releasePinned = resolve;
    });
    await page.route("**/dashboard/api/threads/page?*", async (route) => {
      await heldRecents;
      await route.continue();
    });
    await page.route("**/dashboard/api/threads/pinned", async (route) => {
      await heldPinned;
      await route.continue();
    });
    await page.route("**/dashboard/api/plugins", async (route) => {
      await heldPlugins;
      await route.continue();
    });
    try {
      await page.goto("/agents", { waitUntil: "commit" });
      const pinned = page.getByText("Sidebar pinned thread");
      const pinnedHeader = page.getByRole("button", {
        name: "Pinned",
        exact: true,
      });
      const recents = page.getByRole("status", {
        name: "Loading recent threads",
      });
      await expect(
        page.getByRole("status", { name: "Loading apps" }),
      ).toBeVisible();
      await expect(pinnedHeader).toBeHidden();
      await expect(recents).toBeHidden();
      releasePlugins();
      await expect(pinnedHeader).toBeVisible();
      await expect(recents).toBeHidden();
      releasePinned();
      await expect(recents).toBeVisible();
      await pinnedHeader.click();
      await expect(pinned).toBeVisible();
      const pinnedY = (await pinned.boundingBox())!.y;
      releaseRecents();
      await expect(recents).toBeHidden();
      expect((await pinned.boundingBox())!.y).toBe(pinnedY);
    } finally {
      releaseRecents();
      releasePlugins();
      releasePinned();
      await page.request.delete(`/threads/${id}`);
    }
  });

  test("keeps navigation in place when the concierge profile loads", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    const threadId = "75000000-0000-4000-8000-000000000021";
    await page.request.delete(`/threads/${threadId}`);
    await page.request.post("/threads", {
      data: {
        thread_id: threadId,
        metadata: {
          owner_login: SAME_USER.login,
          title: "Concierge navigation",
          concierge: true,
        },
      },
    });
    await page.route("**/dashboard/api/slack/concierge", (route) =>
      route.fulfill({ json: { thread_id: threadId, channel_id: "DTEST" } }),
    );
    let release = () => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route("**/dashboard/api/profile", async (route) => {
      const response = await route.fetch();
      const profile = (await response.json()) as Record<string, unknown>;
      await held;
      await route.fulfill({
        response,
        json: { ...profile, concierge_mode: true },
      });
    });
    try {
      await page.goto("/agents", { waitUntil: "commit" });
      const newThread = page.getByRole("link", { name: "New", exact: true });
      const navigation = page.getByRole("link", {
        name: "Automations",
        exact: true,
      });
      await expect(newThread).toBeVisible();
      await expect(navigation).toBeHidden();
      const newThreadY = (await newThread.boundingBox())!.y;
      release();
      await expect(page.getByText("Concierge", { exact: true })).toBeVisible();
      await expect(navigation).toBeVisible();
      expect((await newThread.boundingBox())!.y).toBe(newThreadY);
      const documentRequests: string[] = [];
      page.on("request", (request) => {
        if (
          request.isNavigationRequest() &&
          request.frame() === page.mainFrame()
        ) {
          documentRequests.push(request.url());
        }
      });
      await page.getByRole("link", { name: "Concierge", exact: true }).click();
      await expect(page).toHaveURL(`/agents/${threadId}`);
      await expect(newThread).toBeVisible();
      expect(documentRequests).toEqual([]);
      expect((await newThread.boundingBox())!.y).toBe(newThreadY);
    } finally {
      release();
      await page.request.delete(`/threads/${threadId}`);
    }
  });

  test("keeps later project folders hidden until earlier folders load", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    const ids = [
      "75000000-0000-4000-8000-000000000011",
      "75000000-0000-4000-8000-000000000012",
    ];
    const names = ["sidebar-first-folder", "sidebar-second-folder"];
    for (const [index, id] of ids.entries()) {
      await page.request.delete(`/threads/${id}`);
      const created = await page.request.post("/threads", {
        data: {
          thread_id: id,
          if_exists: "raise",
          metadata: {
            owner_login: SAME_USER.login,
            participant_logins: { [SAME_USER.login]: true },
            title: names[index],
            source: "dashboard",
            origin: "dashboard",
            thread_category: "interactive",
            trigger_kind: "user",
            repo_owner: "acme",
            repo_name: names[index],
            created_at_ms: Date.now() + (1 - index) * 1000,
            updated_at_ms: Date.now() + (1 - index) * 1000,
          },
        },
      });
      expect(created.ok()).toBeTruthy();
    }
    await page.addInitScript(() => {
      localStorage.setItem(
        "open-swe.agents.sidebar-prefs",
        JSON.stringify({
          organize: "repo",
          pinnedRepoKeys: [
            "repo:acme/sidebar-first-folder",
            "repo:acme/sidebar-second-folder",
          ],
        }),
      );
    });
    let release = () => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route("**/dashboard/api/threads/page?*", async (route) => {
      if (
        new URL(route.request().url()).searchParams.get("repo") ===
        "acme/sidebar-first-folder"
      ) {
        await held;
      }
      await route.continue();
    });
    try {
      await page.goto("/agents", { waitUntil: "commit" });
      const first = page.getByRole("button", { name: names[0], exact: true });
      const second = page.getByRole("button", { name: names[1], exact: true });
      await expect(first).toBeVisible();
      await expect(
        page.getByRole("status", { name: `Loading chats in ${names[0]}` }),
      ).toBeVisible();
      await expect(second).toBeHidden();
      const firstY = (await first.boundingBox())!.y;
      release();
      await expect(second).toBeVisible();
      expect((await first.boundingBox())!.y).toBe(firstY);
    } finally {
      release();
      for (const id of ids) await page.request.delete(`/threads/${id}`);
    }
  });

  test("keeps the active chat visible while its project loads", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    const id = "75000000-0000-4000-8000-000000000002";
    await page.request.delete(`/threads/${id}`);
    const now = Date.now();
    const created = await page.request.post("/threads", {
      data: {
        thread_id: id,
        if_exists: "raise",
        metadata: {
          owner_login: SAME_USER.login,
          participant_logins: { [SAME_USER.login]: true },
          title: "Sidebar active thread",
          source: "dashboard",
          origin: "dashboard",
          thread_category: "interactive",
          trigger_kind: "user",
          repo_owner: "acme",
          repo_name: "sidebar-loading",
          created_at_ms: now,
          updated_at_ms: now,
        },
      },
    });
    expect(created.ok()).toBeTruthy();
    let release = () => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route("**/dashboard/api/threads/page?*", async (route) => {
      if (new URL(route.request().url()).searchParams.has("repo")) await held;
      await route.continue();
    });
    try {
      await page.goto(`/agents/${id}`, { waitUntil: "commit" });
      await expect(
        page.getByRole("status", { name: "Loading chats in sidebar-loading" }),
      ).toBeVisible();
      await expect(
        page.getByRole("link", { name: /Sidebar active thread/ }).first(),
      ).toBeVisible();
      release();
      await expect(
        page.getByRole("status", { name: "Loading chats in sidebar-loading" }),
      ).toBeHidden();
    } finally {
      release();
      await page.request.delete(`/threads/${id}`);
    }
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
      if (params.get("ownerless") === "true" && params.get("limit") === "25") {
        await held;
      }
      await route.continue();
    });

    try {
      await page.goto("/agents", { waitUntil: "commit" });
      const skeleton = page.getByRole("status", {
        name: "Loading recent threads",
      });
      await expect(skeleton).toBeVisible({ timeout: 30_000 });
      release();
      await expect(skeleton).toBeHidden({ timeout: 30_000 });
    } finally {
      release();
    }
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
        JSON.stringify({
          organize: "date",
          filters: { sources: ["slack"] },
        }),
      );
    });

    let release = () => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route("**/dashboard/api/threads/pinned", (route) =>
      route.fulfill({ json: [] }),
    );
    await page.route("**/dashboard/api/threads/page?*", async (route) => {
      const params = new URL(route.request().url()).searchParams;
      if (params.get("resolved") === "false" && params.get("limit") === "25") {
        await held;
        await route.fulfill({ json: { items: [], limit: 25, offset: 0 } });
      } else {
        await route.continue();
      }
    });

    try {
      await page.goto("/agents", { waitUntil: "commit" });
      const skeleton = page.getByRole("status", {
        name: "Loading recent threads",
      });
      await expect(skeleton).toBeVisible({ timeout: 30_000 });
      await expect(
        page.getByText("No threads match these filters."),
      ).toHaveCount(0);
      release();
      await expect(
        page.getByText("No threads match these filters."),
      ).toBeVisible();
    } finally {
      release();
    }
  });
});

async function sendSegmentedReplies(page: Page): Promise<string> {
  const send = await page.request.post("/mock/slack/send", {
    data: { text: "<@U0BOT> E2E_SLACK_REPLY_SEGMENTS report progress mid-run" },
  });
  expect(send.ok()).toBeTruthy();
  return ((await send.json()) as { thread_id: string }).thread_id;
}

async function expectInDocumentOrder(locators: Array<Locator>) {
  for (const locator of locators) await expect(locator).toBeVisible();
  const [first, ...rest] = await Promise.all(
    locators.map((locator) => locator.elementHandle()),
  );
  expect(
    await first!.evaluate(
      (head, tail) =>
        [head, ...tail].every(
          (node, index, nodes) =>
            index === 0 ||
            Boolean(
              nodes[index - 1]!.compareDocumentPosition(node!) &
              Node.DOCUMENT_POSITION_FOLLOWING,
            ),
        ),
      rest,
    ),
  ).toBe(true);
}
