import { test, expect } from "@playwright/test";

test("shows a linked Thinking reply until the final response arrives", async ({
  page,
  request,
}) => {
  await page.goto("/mock/slack");
  await page.locator("#reset").click();
  const send = await request.post("/mock/slack/send", {
    data: { text: "<@U0BOT> now also tweak it E2E_BUSY_HOLD:4" },
  });
  expect(send.ok()).toBeTruthy();

  const thinking = page.locator(".msg.bot").filter({ hasText: "Thinking..." });
  await expect(thinking).toBeVisible();
  await expect(
    thinking.getByRole("link", { name: "Open in Web" }),
  ).toBeVisible();

  await expect(thinking).toHaveCount(0, { timeout: 60_000 });
  await expect(
    page.locator(".msg.bot").filter({ hasText: "Done" }),
  ).toBeVisible();
});
