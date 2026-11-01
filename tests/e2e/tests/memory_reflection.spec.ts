import { test, expect, type APIRequestContext } from "@playwright/test";

import { SAME_USER } from "./helpers/dashboard";

// Mirrors `MEMORY_REFLECTION_LESSON` in fake_llm.py, which the fork remembers.
const LESSON = "E2E lesson: the greeting module lives in greet.py.";

async function control<T>(
  request: APIRequestContext,
  path: string,
  init: { method?: "GET" | "POST"; data?: unknown } = {},
): Promise<T> {
  const res =
    init.method === "GET"
      ? await request.get(path)
      : await request.post(path, { data: init.data ?? {} });
  expect(
    res.ok(),
    `${path} → ${res.status()}: ${await res.text()}`,
  ).toBeTruthy();
  return (await res.json()) as T;
}

async function setMemory(request: APIRequestContext, on: boolean) {
  await control(request, "/control/user-preferences", {
    data: { login: SAME_USER.login, preferences: { experimental_memory: on } },
  });
}

test.describe("memory reflection", () => {
  // Preferences live on the users row, which a reset keeps.
  test.afterEach(async ({ request }) => {
    await setMemory(request, false);
  });

  // A thread that ran a shell command goes idle; its fork is told to remember what
  // it learned. The fork's first model call must start exactly as its source's last
  // did, tools and conversation, with the reply to it, so it reads the provider's
  // prompt cache that call wrote. The fake model compares the two requests.
  test("an idle thread's fork starts its model call with its source's exact prefix", async ({
    page,
    request,
  }) => {
    await setMemory(request, true);
    await control(request, "/control/memory-reflection/check", {
      data: { checked: false },
    });
    await page.goto("/mock/slack");
    await page.locator("#reset").click();

    const { thread_id: threadId } = await control<{ thread_id: string }>(
      request,
      "/mock/slack/send",
      { data: { text: "<@U0BOT> E2E_MEMORY_REFLECTION check the repository" } },
    );
    await expect
      .poll(
        async () =>
          (
            await control<{ idle: boolean; runs: number }>(
              request,
              `/control/thread-idle?thread_id=${threadId}`,
              { method: "GET" },
            )
          ).idle,
        { timeout: 60_000 },
      )
      .toBe(true);

    await expect
      .poll(
        async () =>
          (
            await request.post(
              `/control/memory-reflection/fork?thread_id=${threadId}`,
            )
          ).status(),
        { timeout: 30_000 },
      )
      .toBe(200);

    await expect
      .poll(
        async () =>
          (
            await control<{ checked: boolean }>(
              request,
              "/control/memory-reflection/check",
              { method: "GET" },
            )
          ).checked,
        { timeout: 60_000 },
      )
      .toBe(true);
    const check = await control<{
      prefix_matches: boolean;
      same_tools: boolean;
      appended_messages: number;
      divergence: unknown;
    }>(request, "/control/memory-reflection/check", { method: "GET" });
    expect(check.same_tools).toBe(true);
    expect(check.prefix_matches, JSON.stringify(check.divergence)).toBe(true);
    expect(check.appended_messages).toBeGreaterThan(0);

    await expect
      .poll(
        async () =>
          (
            await control<{ events: Array<Record<string, unknown>> }>(
              request,
              "/control/memory-events",
              { method: "GET" },
            )
          ).events.find(
            (event) => event.body === LESSON && event.thread_id === threadId,
          ),
        { timeout: 60_000 },
      )
      .toMatchObject({
        saved_by_idle_reflection: true,
        reflection_thread_id: expect.any(String),
      });
  });
});
