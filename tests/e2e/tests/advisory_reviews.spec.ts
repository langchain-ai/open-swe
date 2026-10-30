import { test, expect } from "@playwright/test";
import { loginAs, SAME_USER, seedOpenPullRequest } from "./helpers/dashboard";

test("publishes a fresh advisory before superseding the old summary", async ({
  page,
}, testInfo) => {
  await page.request.post("/control/reset");
  await loginAs(page, SAME_USER);
  const pr = await seedOpenPullRequest(page, {
    repo: "fakeorg/demo",
    title: "Refresh advisory assessment",
    body: "A reassessment should produce a fresh review notification.",
    author: "bob",
    head: "advisory-reassessment",
    files: { "README.md": "Updated documentation.\n" },
  });
  const publish = async (assessment: string) => {
    const response = await page.request.post("/control/publish-advisory", {
      data: { number: pr.number, assessment },
    });
    expect(response.ok(), await response.text()).toBeTruthy();
    return (await response.json()) as { id: number; body: string };
  };
  const original = await publish("Initial assessment: documentation only.");
  await page.goto("/mock/github");
  await expect(page.locator(`[data-review="${original.id}"]`)).toContainText(
    "Initial assessment",
  );
  const before = testInfo.outputPath("advisory-before.png");
  await page.screenshot({ path: before, fullPage: true });
  await testInfo.attach("Before reassessment", {
    path: before,
    contentType: "image/png",
  });

  const replacement = await publish(
    "Latest assessment: updated documentation remains safe.",
  );
  expect(replacement.id).not.toBe(original.id);
  await expect(page.locator(`[data-review="${original.id}"] pre`)).toHaveText(
    "Superseded",
  );
  await expect(page.locator(`[data-review="${replacement.id}"]`)).toContainText(
    "Latest assessment",
  );
  await expect(page.locator("[data-review]")).toHaveCount(2);
  const after = testInfo.outputPath("advisory-after.png");
  await page.screenshot({ path: after, fullPage: true });
  await testInfo.attach("After reassessment", {
    path: after,
    contentType: "image/png",
  });
});
