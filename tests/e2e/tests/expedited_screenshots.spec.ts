import { test, expect } from "@playwright/test";

test("screenshots lead, Bob reviews the code before approving", async ({
  page,
  request,
}, testInfo) => {
  await request.post("/control/reset");
  await request.post("/control/login", { data: { login: "bob" } });
  await request.post("/control/collaborator-permission", {
    data: { login: "bob", permission: "write" },
  });
  await page.route("https://evidence.example/*.png", async (route) => {
    await route.fulfill({
      contentType: "image/svg+xml",
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="140"><rect width="600" height="140" fill="#222"/><text x="30" y="75" fill="white" font-size="24">Synthetic UI screenshot</text></svg>',
    });
  });
  const seeded = await request.post("/control/pull-request", {
    data: {
      title: "Synthetic screenshot-first review",
      author: "alice",
      head: "visual-change",
      files: { "greet.py": 'def greet(name):\n    return f"Hello, {name}!"\n' },
      body: "| Before | After |\n|---|---|\n| ![Before](https://evidence.example/before.png) | ![After](https://evidence.example/after.png) |",
    },
  });
  expect(seeded.ok()).toBeTruthy();
  const { number } = (await seeded.json()) as { number: number };
  const posted = await request.post("/control/expedited-card", {
    data: { number },
  });
  expect(posted.ok()).toBeTruthy();
  await page.goto("/mock/slack");
  await page.locator("#user").selectOption("U_BOB");
  const card = page
    .locator(".msg.bot")
    .filter({ hasText: "Expedited review requested" })
    .last();
  await expect(card.locator("img.block-image")).toHaveCount(2);
  await expect(card.locator("img.block-image").first()).toHaveAttribute(
    "alt",
    "Before",
  );
  await expect(
    card.getByRole("button", { name: "Approve", exact: true }),
  ).toHaveCount(0);
  await card.screenshot({ path: testInfo.outputPath("after.png") });
  await card.getByRole("button", { name: "Review files" }).click();
  const dialog = page.locator("#review-modal");
  await expect(dialog).toContainText("greet.py");
  await expect(dialog).toContainText("Hello");
  await dialog.screenshot({ path: testInfo.outputPath("code.png") });
  await dialog.getByRole("button", { name: "Approve", exact: true }).click();
  await expect
    .poll(async () => {
      const response = await request.get("/control/expedited-approvals");
      return ((await response.json()) as Array<{ approvers: string[] }>).at(-1)
        ?.approvers;
    })
    .toEqual(["bob"]);
});
