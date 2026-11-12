import { test, expect } from "@playwright/test";
import { loginAs, SAME_USER } from "./helpers/dashboard";

const ids = [1, 2, 3].map((n) => `76000000-0000-4000-8000-00000000003${n}`);

test.beforeEach(async ({ page }) => {
  await loginAs(page, SAME_USER);
  for (const [index, id] of ids.entries()) {
    await page.request.delete(`/threads/${id}`);
    const response = await page.request.post("/threads", {
      data: {
        thread_id: id,
        metadata: {
          title: `Selection fixture ${index + 1}`,
          owner_login: SAME_USER.login,
          participant_logins: { [SAME_USER.login]: true },
          source: "dashboard",
          origin: "dashboard",
          thread_category: "interactive",
          trigger_kind: "user",
          created_at_ms: Date.now() - index * 60_000,
          latest_run_status: "success",
        },
      },
    });
    expect(response.ok()).toBeTruthy();
  }
  await page.addInitScript(() => {
    localStorage.setItem(
      "open-swe.agents.sidebar-prefs",
      JSON.stringify({ organize: "workspace" }),
    );
  });
  await page.goto("/agents");
  await expect(
    page.locator(`[data-selectable-thread="${ids[2]}"]`),
  ).toBeVisible();
});

test.afterEach(async ({ page }) => {
  for (const id of ids) await page.request.delete(`/threads/${id}`);
});

test("sidebar selection supports ranges, modifier toggles and ordinary navigation", async ({
  page,
}) => {
  const row = (index: number) =>
    page.locator(`[data-selectable-thread="${ids[index]}"]`);
  await row(0).click({ modifiers: ["Control"] });
  await row(2).click({ modifiers: ["Shift"] });
  await expect(
    page.getByRole("status", { name: "" }).filter({ hasText: "3 selected" }),
  ).toBeVisible();
  await expect(page).toHaveURL(/\/agents$/);
  await row(1).click({ modifiers: ["Meta"] });
  await expect(page.getByText("2 selected", { exact: true })).toBeVisible();
  await row(2).press("Escape");
  await expect(page.getByText("2 selected", { exact: true })).toBeHidden();
  await row(0).click({ modifiers: ["Control"] });
  await page.getByRole("button", { name: "Clear", exact: true }).click();
  await expect(page.getByText("1 selected", { exact: true })).toBeHidden();
  await row(0).click({ modifiers: ["Control"] });
  await row(1).click();
  await expect(page).toHaveURL(new RegExp(ids[1]));
  await expect(page.getByText("1 selected", { exact: true })).toBeHidden();
});

for (const action of ["Archive", "Delete"] as const) {
  test(`bulk ${action.toLowerCase()} retains failed threads and confirms deletion count`, async ({
    page,
  }) => {
    const row = (index: number) =>
      page.locator(`[data-selectable-thread="${ids[index]}"]`);
    await row(0).click({ modifiers: ["Control"] });
    await row(1).click({ modifiers: ["Shift"] });
    const endpoint =
      action === "Archive"
        ? `/threads/${ids[1]}/resolve`
        : `/threads/${ids[1]}`;
    await page.route(`**/dashboard/api${endpoint}`, async (route) => {
      if (
        route.request().method() === (action === "Archive" ? "POST" : "DELETE")
      ) {
        await route.fulfill({
          status: 500,
          json: { detail: "Selection fixture failure" },
        });
      } else await route.continue();
    });
    await page.getByRole("button", { name: action, exact: true }).click();
    if (action === "Delete") {
      const dialog = page.getByRole("dialog");
      await expect(
        dialog.getByText("Delete 2 threads", { exact: true }),
      ).toHaveCount(2);
      await dialog.getByRole("button", { name: "Cancel" }).click();
      await expect(row(0)).toBeVisible();
      await page.getByRole("button", { name: "Delete", exact: true }).click();
      await dialog.getByRole("button", { name: "Delete 2 threads" }).click();
    }
    await expect(row(0)).toBeHidden();
    await expect(row(1)).toHaveAttribute(
      "aria-label",
      "Selection fixture 2, selected",
    );
    await expect(page.getByText("1 selected", { exact: true })).toBeVisible();
    await expect(row(2)).toBeVisible();
    await page.unroute(`**/dashboard/api${endpoint}`);
    await page.getByRole("button", { name: action, exact: true }).click();
    if (action === "Delete") {
      await page
        .getByRole("dialog")
        .getByRole("button", { name: "Delete 1 thread" })
        .click();
    }
    await expect(row(1)).toBeHidden();
    await expect(page.getByText("1 selected", { exact: true })).toBeHidden();
  });
}
