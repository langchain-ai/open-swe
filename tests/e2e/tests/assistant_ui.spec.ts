import { expect, test, type Page } from "@playwright/test";
import {
  SAME_USER,
  loginAs,
  typeIntoComposer,
  waitForStateToContain,
  waitForThreadIdle,
  waitForThreadNotBusy,
} from "./helpers/dashboard";

const conversation = (page: Page) =>
  page.getByTestId("assistant-ui-conversation");
const composer = (page: Page) =>
  page.getByRole("textbox", { name: "Message input" });

async function setExperimentalMode(page: Page, enabled: boolean) {
  const profileResponse = await page.request.get("/dashboard/api/profile");
  expect(profileResponse.ok()).toBeTruthy();
  const profile = await profileResponse.json();
  const optionsResponse = await page.request.get("/dashboard/api/options");
  expect(optionsResponse.ok()).toBeTruthy();
  const options = await optionsResponse.json();
  const response = await page.request.put("/dashboard/api/profile", {
    headers: { origin: new URL(profileResponse.url()).origin },
    data: {
      ...profile,
      default_model: profile.default_model || options.default_agent_model,
      reasoning_effort:
        profile.reasoning_effort || options.default_agent_reasoning_effort,
      experimental_assistant_ui: enabled,
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  expect(await response.json()).toMatchObject({
    experimental_assistant_ui: enabled,
  });
}

async function startSlackThread(page: Page, prompt: string) {
  const response = await page.request.post("/mock/slack/send", {
    data: { text: `<@U0BOT> ${prompt}` },
  });
  expect(response.ok()).toBeTruthy();
  const { thread_id: id } = await response.json();
  expect(id).toBeTruthy();
  return id as string;
}

async function switchThread(page: Page, id: string) {
  await page
    .locator(`[data-sidebar-frame] a[href="/assistant/${id}"]`)
    .first()
    .click();
  await expect(page).toHaveURL(new RegExp(`/assistant/${id}$`));
  await expect(composer(page)).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await loginAs(page, SAME_USER);
  expect((await page.request.post("/control/reset")).ok()).toBeTruthy();
  await setExperimentalMode(page, true);
});

test.afterEach(async ({ page }) => {
  await setExperimentalMode(page, false);
});

test("waits for the profile and hydrates the transcript only once", async ({
  page,
}) => {
  const id = await startSlackThread(page, "Add a greet() helper and open a PR");
  await waitForThreadIdle(page, id);
  await waitForThreadNotBusy(page, id);
  const profileRequested = Promise.withResolvers<void>();
  const releaseProfile = Promise.withResolvers<void>();
  await page.route("**/dashboard/api/profile", async (route) => {
    profileRequested.resolve();
    await releaseProfile.promise;
    await route.continue();
  });
  const statePath = `/dashboard/api/threads/${id}/state`;
  const stateRequests: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === statePath) {
      stateRequests.push(request.url());
    }
  });
  const warmedState = page.waitForResponse(
    (response) => new URL(response.url()).pathname === statePath,
  );

  try {
    await page.goto(`/agents/${id}`, { waitUntil: "domcontentloaded" });
    await profileRequested.promise;
    const response = await warmedState;
    await response.finished();
    expect(response.ok()).toBeTruthy();
    expect(await response.headerValue("content-encoding")).toBe("gzip");
    await expect(page.getByTestId("composer-editor")).toHaveCount(0);
    await expect(conversation(page)).toHaveCount(0);

    releaseProfile.resolve();
    await expect(conversation(page)).toBeVisible();
    await expect(
      conversation(page).getByText(/anything else you'd like changed/),
    ).toBeVisible();
    expect(stateRequests).toHaveLength(1);
  } finally {
    releaseProfile.resolve();
  }
});

test("creates a thread, sends a follow-up, and hydrates the experimental transcript", async ({
  page,
}) => {
  await page.goto("/agents");
  await composer(page).fill("Please add a greet() helper and open a PR");
  await composer(page).press("Enter");
  await expect(page).toHaveURL(/\/assistant\/[0-9a-f-]{36}$/);
  const id = new URL(page.url()).pathname.split("/").at(-1)!;
  await expect(conversation(page)).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Open fakeorg/demo pull request #1" }),
  ).toBeVisible();
  await waitForThreadIdle(page, id);
  await waitForThreadNotBusy(page, id);
  const replies = conversation(page).getByText(
    /anything else you'd like changed/,
  );
  const previousReplies = await replies.count();

  const followUp = "Also add a docstring for the greeting.";
  await composer(page).fill(followUp);
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(
    conversation(page).getByText(followUp, { exact: true }),
  ).toBeVisible();
  await expect(replies).toHaveCount(previousReplies + 1);
  await waitForStateToContain(page, id, followUp);
  await waitForThreadIdle(page, id);
  await page.reload();
  await expect(
    conversation(page).getByText(followUp, { exact: true }),
  ).toBeVisible();
  await expect(replies).toHaveCount(previousReplies + 1);
});

test("keeps separate drafts while a background thread completes", async ({
  page,
}) => {
  const first = await startSlackThread(
    page,
    "First task: E2E_BUSY_HOLD:15 add a greet() helper and open a PR",
  );
  const second = await startSlackThread(
    page,
    "Second task: add a greet() helper and open a PR",
  );
  await page.goto(`/agents/${first}`);
  await expect(page.getByRole("button", { name: "Stop run" })).toBeVisible();
  await composer(page).fill("Draft for the first task");

  await switchThread(page, second);
  await expect(composer(page)).toHaveValue("");
  await composer(page).fill("Draft for the second task");
  await waitForThreadIdle(page, first);
  await expect(page).toHaveURL(new RegExp(`/assistant/${second}$`));
  await expect(composer(page)).toHaveValue("Draft for the second task");

  await switchThread(page, first);
  await expect(composer(page)).toHaveValue("Draft for the first task");
  await expect(
    conversation(page).getByText(/anything else you'd like changed/),
  ).toBeVisible();
  await switchThread(page, second);
  await expect(composer(page)).toHaveValue("Draft for the second task");
  await waitForThreadIdle(page, second);
});

test("queues a follow-up and answers it after stopping the active run", async ({
  page,
}) => {
  const id = await startSlackThread(
    page,
    "E2E_BUSY_HOLD:30 add a greet() helper and open a PR",
  );
  await page.goto(`/agents/${id}`);
  await expect(page.getByRole("button", { name: "Stop run" })).toBeVisible();
  await expect
    .poll(async () => {
      const response = await page.request.get(
        `/dashboard/api/threads/${id}/state`,
      );
      expect(response.ok()).toBeTruthy();
      const state = await response.json();
      return state.values.messages.some(
        (message: { type: string; name?: string }) =>
          message.type === "tool" && message.name === "slack_thread_reply",
      );
    })
    .toBe(true);
  const followUp = "Please continue after stopping the current run.";
  await composer(page).fill(followUp);
  await page.getByRole("button", { name: "Send follow up" }).click();
  await expect(
    conversation(page).getByText("Queued next", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Stop run" }).click();
  await expect(
    conversation(page).getByText("Queued next", { exact: true }),
  ).toHaveCount(0);
  await expect(
    conversation(page).getByText(/anything else you'd like changed/),
  ).toBeVisible();
  await waitForStateToContain(page, id, followUp);
  await waitForThreadIdle(page, id);
});

test("standard mode still sends, and the setting applies to the same existing thread", async ({
  page,
}) => {
  await setExperimentalMode(page, false);
  const id = await startSlackThread(page, "Add a greet() helper and open a PR");
  await page.goto(`/agents/${id}`);
  await expect(page.getByTestId("composer-editor")).toBeVisible();
  await expect(conversation(page)).toHaveCount(0);
  await waitForThreadIdle(page, id);
  await waitForThreadNotBusy(page, id);
  await typeIntoComposer(page, "Continue using the standard composer.");
  await expect(
    page.getByText(/anything else you'd like changed/),
  ).toBeVisible();
  await waitForThreadIdle(page, id);

  await page.goto("/my-settings");
  const toggle = page.getByRole("switch", {
    name: "Assistant UI (experimental)",
  });
  await expect(toggle).toHaveAttribute("aria-checked", "false");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-checked", "true");
  await page.getByRole("link", { name: "Back to app" }).click();
  await expect(page).toHaveURL(new RegExp(`/assistant/${id}$`));
  await expect(conversation(page)).toBeVisible();
  await expect(composer(page)).toBeVisible();
  await expect(
    conversation(page).getByText("Continue using the standard composer.", {
      exact: true,
    }),
  ).toBeVisible();
});

test("retries a failed queued message without replacing a newer draft", async ({
  page,
}) => {
  const id = await startSlackThread(
    page,
    "E2E_BUSY_HOLD:20 add a greet() helper and open a PR",
  );
  await page.goto(`/agents/${id}`);
  await expect(page.getByRole("button", { name: "Stop run" })).toBeVisible();
  const messagePath = `**/dashboard/api/threads/${id}/messages`;
  await page.route(messagePath, (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Queue temporarily unavailable" }),
    }),
  );
  const prompt = "Retry this follow-up once the queue recovers.";
  await composer(page).fill(prompt);
  await page.getByRole("button", { name: "Send follow up" }).click();
  await expect(
    page.getByRole("button", { name: "Retry message" }),
  ).toBeVisible();
  await composer(page).fill("Keep this newer draft.");
  await page.unroute(messagePath);
  const accepted = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname ===
        `/dashboard/api/threads/${id}/messages` &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Retry message" }).click();
  expect((await accepted).ok()).toBeTruthy();
  await expect(page.getByRole("button", { name: "Retry message" })).toHaveCount(
    0,
  );
  await expect(composer(page)).toHaveValue("Keep this newer draft.");
  await waitForStateToContain(page, id, prompt);
  await waitForThreadIdle(page, id);
  await expect(composer(page)).toHaveValue("Keep this newer draft.");
  await expect(
    conversation(page).getByText(prompt, { exact: true }),
  ).toHaveCount(1);
});
