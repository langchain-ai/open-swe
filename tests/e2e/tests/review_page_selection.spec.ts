import { test, expect } from "@playwright/test";
import { SAME_USER, loginAs, seedOpenPullRequest } from "./helpers/dashboard";

test("clears the line selection when cancelling an inline comment", async ({
  page,
}) => {
  await page.request.post("/control/reset");
  await loginAs(page, SAME_USER);
  const pr = await seedOpenPullRequest(page, {
    author: "bob",
    files: { "selection.py": "FIRST = 1\nSECOND = 2\n" },
  });
  await page.goto(`/agents/reviews/${pr.repo}/${pr.number}`);
  const diff = page.getByRole("region", { name: "Changes" });
  const line = diff.locator('[data-column-number="1"]').first();
  await line.hover();
  await diff.locator("[data-gutter-utility-slot] button").click();
  await expect(diff.getByLabel("Comment body")).toBeVisible();
  await expect(diff.locator("[data-selected-line]").first()).toBeVisible();

  await diff.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.getByRole("heading", { name: "Changes", exact: true }).click();
  await expect(diff.getByLabel("Comment body")).toHaveCount(0);
  await expect(diff.locator("[data-selected-line]")).toHaveCount(0);

  await line.hover();
  await diff.locator("[data-gutter-utility-slot] button").click();
  await diff.getByLabel("Comment body").press("Escape");
  await expect(diff.getByLabel("Comment body")).toHaveCount(0);
  await expect(diff.locator("[data-selected-line]")).toHaveCount(0);
});
