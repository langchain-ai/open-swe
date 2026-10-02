import { test, expect } from "@playwright/test";
import { loginAs, SAME_USER } from "./helpers/dashboard";

test("workspace admins create and revoke a key without retaining its secret", async ({
  page,
}) => {
  await loginAs(page, SAME_USER);
  await page.goto("/workspaces/default");
  const heading = page.getByRole("heading", { name: "API keys", exact: true });
  await expect(heading).toBeVisible();
  await heading.scrollIntoViewIfNeeded();
  await page.getByLabel("Key name").fill("Release automation");
  await page.getByLabel("Expires in (days)").fill("30");
  await page
    .getByRole("button", { name: "Create API key", exact: true })
    .click();
  const secret = page.getByLabel("New API key");
  await expect(secret).toHaveValue(/^osk_/);
  const suffix = (await secret.inputValue()).slice(-6);
  const keyRow = page.getByRole("listitem").filter({ hasText: `…${suffix}` });
  await page.getByRole("button", { name: "I’ve saved my key" }).click();
  await expect(secret).toHaveCount(0);
  await page.reload();
  await expect(secret).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Revoke Release automation" }),
  ).toBeVisible();
  await heading.scrollIntoViewIfNeeded();
  await page.getByRole("button", { name: "Revoke Release automation" }).click();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Revoke Release automation" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Revoke Release automation" }).click();
  await page.getByRole("button", { name: "Revoke key", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Revoke Release automation" }),
  ).toHaveCount(0);
  await expect(keyRow.getByText(/revoked · Expires/)).toBeVisible();
  await expect(
    keyRow.getByText("Created by Alice", { exact: true }),
  ).toBeVisible();
});
