import { expect, test } from "@playwright/test";
import { SAME_ORIGIN_HEADERS } from "./helpers/dashboard";

test("email-match recovery verifies GitHub and survives the next login", async ({
  page,
}) => {
  const created = await page.request.post("/control/account-recovery-thread");
  expect(created.ok()).toBeTruthy();
  const thread = await created.json();
  await page.request.post("/control/login", {
    data: { login: thread.login, email: thread.email },
  });
  expect((await page.request.get("/dashboard/api/me")).status()).toBe(401);
  await page.request.post("/dashboard/api/auth/logout", {
    headers: SAME_ORIGIN_HEADERS,
  });

  await page.goto("/login");
  await page.getByRole("link", { name: "Continue with LangSmith" }).click();
  await page
    .getByRole("textbox", { name: "Email" })
    .fill(thread.email.toUpperCase());
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(page.getByTestId("composer-editor")).toBeVisible();
  const unlinked = await (await page.request.get("/dashboard/api/me")).json();
  expect(unlinked.user_id).not.toBe(thread.user_id);
  expect(unlinked.github_login).toBeNull();
  expect(
    (
      await page.request.get(`/dashboard/api/threads/${thread.thread_id}`)
    ).status(),
  ).toBe(404);
  const blocked = await page.request.post("/dashboard/api/threads", {
    headers: SAME_ORIGIN_HEADERS,
    data: {
      prompt: "Must not access a repository before linking GitHub",
      repo: "fakeorg/demo",
      start: false,
    },
  });
  expect(blocked.status()).toBe(403);

  await page.evaluate(() =>
    localStorage.setItem("open-swe.repos.cache.v2", "stale"),
  );
  await page.getByRole("button", { name: unlinked.login, exact: true }).click();
  await page.getByRole("menuitem", { name: "Sign out", exact: true }).click();
  await expect(page).toHaveURL(/\/login(?:\?|$)/);
  expect((await page.request.get("/dashboard/api/me")).status()).toBe(401);
  expect(
    await page.evaluate(() => localStorage.getItem("open-swe.repos.cache.v2")),
  ).toBeNull();
  await page.getByRole("link", { name: "Continue with LangSmith" }).click();
  await page
    .getByRole("textbox", { name: "Email" })
    .fill(thread.email.toUpperCase());
  await page.getByRole("button", { name: "Continue", exact: true }).click();

  await page
    .getByRole("button", { name: "Select repository", exact: true })
    .click();
  await page.getByRole("button", { name: "Link GitHub", exact: true }).click();
  await page.getByRole("textbox", { name: "GitHub user" }).fill(thread.login);
  await page.getByRole("button", { name: "Authorize GitHub" }).click();
  await expect(page.getByTestId("composer-editor")).toBeVisible();
  const restored = await (await page.request.get("/dashboard/api/me")).json();
  expect(restored.user_id).toBe(thread.user_id);
  expect(
    (await page.request.get(`/dashboard/api/threads/${thread.thread_id}`)).ok(),
  ).toBeTruthy();

  await page.request.post("/dashboard/api/auth/logout", {
    headers: SAME_ORIGIN_HEADERS,
  });
  await page.goto("/login");
  await page.getByRole("link", { name: "Continue with LangSmith" }).click();
  await page
    .getByRole("textbox", { name: "Email" })
    .fill(thread.email.toUpperCase());
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(page.getByTestId("composer-editor")).toBeVisible();
  const returning = await (await page.request.get("/dashboard/api/me")).json();
  expect(returning.user_id).toBe(thread.user_id);
});
