import { expect, test } from "@playwright/test";
import { SAME_ORIGIN_HEADERS } from "./helpers/dashboard";

test("recovery with different emails verifies GitHub and survives the next login", async ({
  page,
}) => {
  const created = await page.request.post("/control/account-recovery-thread");
  expect(created.ok()).toBeTruthy();
  const thread = await created.json();
  const langsmithEmail = `langsmith-${thread.email}`;
  await page.request.post("/control/login", {
    data: { login: thread.login, email: thread.email },
  });
  const original = await (await page.request.get("/dashboard/api/me")).json();
  await page.request.post("/dashboard/api/auth/logout", {
    headers: SAME_ORIGIN_HEADERS,
  });

  await page.goto("/login");
  await page.getByRole("link", { name: "Continue with LangSmith" }).click();
  await page.getByRole("textbox", { name: "Email" }).fill(langsmithEmail);
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Link GitHub to use Open SWE" }),
  ).toBeVisible();
  await expect(page.getByTestId("composer-editor")).toHaveCount(0);
  const blocked = await page.request.post("/dashboard/api/threads", {
    headers: SAME_ORIGIN_HEADERS,
    data: { prompt: "Must not create a duplicate history", start: false },
  });
  expect(blocked.status()).toBe(403);

  await page.evaluate(() =>
    localStorage.setItem("open-swe.repos.cache.v2", "stale"),
  );
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(page).toHaveURL(/\/login(?:\?|$)/);
  expect((await page.request.get("/dashboard/api/me")).status()).toBe(401);
  expect(
    await page.evaluate(() => localStorage.getItem("open-swe.repos.cache.v2")),
  ).toBeNull();
  await page.getByRole("link", { name: "Continue with LangSmith" }).click();
  await page.getByRole("textbox", { name: "Email" }).fill(langsmithEmail);
  await page.getByRole("button", { name: "Continue", exact: true }).click();

  await page.getByRole("button", { name: "Link GitHub", exact: true }).click();
  await page.getByRole("textbox", { name: "GitHub user" }).fill(thread.login);
  await page.getByRole("button", { name: "Authorize GitHub" }).click();
  await expect(page.getByTestId("composer-editor")).toBeVisible();
  const restored = await (await page.request.get("/dashboard/api/me")).json();
  expect(restored.user_id).toBe(original.user_id);
  expect(
    (await page.request.get(`/dashboard/api/threads/${thread.thread_id}`)).ok(),
  ).toBeTruthy();

  await page.request.post("/dashboard/api/auth/logout", {
    headers: SAME_ORIGIN_HEADERS,
  });
  await page.goto("/login");
  await page.getByRole("link", { name: "Continue with LangSmith" }).click();
  await page.getByRole("textbox", { name: "Email" }).fill(langsmithEmail);
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(page.getByTestId("composer-editor")).toBeVisible();
  const returning = await (await page.request.get("/dashboard/api/me")).json();
  expect(returning.user_id).toBe(original.user_id);
});

test("first-time LangSmith users must link GitHub before using Open SWE", async ({
  page,
}) => {
  await page.goto("/login");
  await page.getByRole("link", { name: "Continue with LangSmith" }).click();
  await page
    .getByRole("textbox", { name: "Email" })
    .fill(`new-user-${crypto.randomUUID()}@example.com`);
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Link GitHub to use Open SWE" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Link GitHub", exact: true }),
  ).toBeVisible();
  await expect(page.getByTestId("composer-editor")).toHaveCount(0);
  expect((await page.request.get("/dashboard/api/profile")).status()).toBe(403);
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(page).toHaveURL(/\/login(?:\?|$)/);
  expect((await page.request.get("/dashboard/api/me")).status()).toBe(401);
});
