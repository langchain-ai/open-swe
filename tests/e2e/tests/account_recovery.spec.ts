import { expect, test } from "@playwright/test";
import { SAME_ORIGIN_HEADERS } from "./helpers/dashboard";

test("a new LangSmith email enters the dashboard without GitHub linking", async ({
  page,
}) => {
  await page.goto("/login");
  await page.getByRole("link", { name: "Continue with LangSmith" }).click();
  await page
    .getByRole("textbox", { name: "Email" })
    .fill(`new-${crypto.randomUUID()}@example.com`);
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(page.getByTestId("composer-editor")).toBeVisible();
  const session = await (await page.request.get("/dashboard/api/me")).json();
  expect(session.requires_github_link).toBe(false);
  expect(session.github_login).toBeNull();
  expect(
    (await page.request.get("/dashboard/api/me/preferences")).ok(),
  ).toBeTruthy();
});

test("matching LangSmith email requires GitHub proof and restores the original account", async ({
  page,
}) => {
  const created = await page.request.post("/control/account-recovery-thread");
  expect(created.ok()).toBeTruthy();
  const thread = await created.json();
  await page.request.post("/control/login", {
    data: { login: thread.login, email: thread.email },
  });
  const original = await (await page.request.get("/dashboard/api/me")).json();
  expect(
    (
      await page.request.put("/dashboard/api/me/preferences", {
        headers: SAME_ORIGIN_HEADERS,
        data: { default_visibility: "private", follow_up_behavior: "queue" },
      })
    ).ok(),
  ).toBeTruthy();
  expect(
    (
      await page.request.post("/dashboard/api/auth/logout", {
        headers: SAME_ORIGIN_HEADERS,
      })
    ).ok(),
  ).toBeTruthy();

  await page.goto("/login");
  await page.getByRole("link", { name: "Continue with LangSmith" }).click();
  await page
    .getByRole("textbox", { name: "Email" })
    .fill(thread.email.toUpperCase());
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Return to your existing account" }),
  ).toBeVisible();
  await expect(page.getByTestId("composer-editor")).toHaveCount(0);
  const pending = await (await page.request.get("/dashboard/api/me")).json();
  expect(pending.requires_github_link).toBe(true);
  expect(pending.user_id).not.toBe(original.user_id);
  expect(pending.github_login).toBeNull();
  expect(
    (
      await page.request.get(`/dashboard/api/threads/${thread.thread_id}`)
    ).status(),
  ).toBe(403);
  expect(
    (
      await page.request.post("/dashboard/api/threads", {
        headers: SAME_ORIGIN_HEADERS,
        data: { prompt: "Must not create a duplicate history", start: false },
      })
    ).status(),
  ).toBe(403);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Return to your existing account" }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Link GitHub", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "GitHub account verification" }),
  ).toBeVisible();
  await page.getByRole("textbox", { name: "GitHub user" }).fill(thread.login);
  await page.getByRole("button", { name: "Authorize GitHub" }).click();
  await expect(
    page.getByRole("heading", { name: "Return to your existing account" }),
  ).toHaveCount(0);
  await expect(page.getByTestId("composer-editor")).toBeVisible();
  const restored = await (await page.request.get("/dashboard/api/me")).json();
  expect(restored.user_id).toBe(original.user_id);
  expect(restored.github_login).toBe(thread.login);
  expect(restored.requires_github_link).toBe(false);
  expect(restored.follow_up_behavior).toBe("queue");
  expect(
    (await page.request.get(`/dashboard/api/threads/${thread.thread_id}`)).ok(),
  ).toBeTruthy();

  expect(
    (
      await page.request.post("/dashboard/api/auth/logout", {
        headers: SAME_ORIGIN_HEADERS,
      })
    ).ok(),
  ).toBeTruthy();
  await page.goto("/login");
  await page.getByRole("link", { name: "Continue with LangSmith" }).click();
  await page
    .getByRole("textbox", { name: "Email" })
    .fill(thread.email.toUpperCase());
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(page.getByTestId("composer-editor")).toBeVisible();
  const returning = await (await page.request.get("/dashboard/api/me")).json();
  expect(returning.user_id).toBe(original.user_id);
  expect(returning.requires_github_link).toBe(false);
  expect(
    (await page.request.get(`/dashboard/api/threads/${thread.thread_id}`)).ok(),
  ).toBeTruthy();
});
