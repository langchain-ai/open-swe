import { expect, test } from "@playwright/test";

import {
  loginAs,
  openThreadViaSlackLink,
  SAME_ORIGIN_HEADERS,
  SAME_USER,
} from "./helpers/dashboard";

test("Back to app returns to the thread that opened settings", async ({
  page,
}) => {
  await loginAs(page, SAME_USER);
  await openThreadViaSlackLink(page);
  const threadUrl = page.url();
  const threadHref = new URL(threadUrl);

  await expect(page.locator("html")).toHaveAttribute(
    "data-agents-theme",
    "true",
  );
  await page.getByRole("button", { name: SAME_USER.login }).click();
  await page.getByRole("menuitem", { name: "Settings" }).click();
  await expect(page).toHaveURL(/\/my-settings$/);

  const backLink = page.getByRole("link", { name: "Back to app" });
  await expect(backLink).toHaveAttribute(
    "href",
    `${threadHref.pathname}${threadHref.search}${threadHref.hash}`,
  );
  await backLink.click();
  await expect(page).toHaveURL(threadUrl);
});

test("task coordination opt-in persists and can be disabled", async ({
  page,
}) => {
  await loginAs(page, SAME_USER);
  const saved = await page.request.get("/dashboard/api/profile");
  expect(saved.ok()).toBeTruthy();
  const profile = (await saved.json()) as Record<string, unknown>;
  const options = (await (
    await page.request.get("/dashboard/api/options")
  ).json()) as {
    default_agent_model: string;
    default_agent_reasoning_effort: string;
  };
  const update = {
    default_model: profile.default_model ?? options.default_agent_model,
    reasoning_effort:
      profile.reasoning_effort ?? options.default_agent_reasoning_effort,
  };
  const reset = await page.request.put("/dashboard/api/profile", {
    headers: SAME_ORIGIN_HEADERS,
    data: { ...update, experimental_task_coordination: false },
  });
  expect(reset.ok()).toBeTruthy();
  try {
    await page.goto("/my-settings/experiments");
    const toggle = page.getByRole("switch", {
      name: "Asynchronous task coordination (experimental)",
    });
    await expect(toggle).not.toBeChecked();
    for (const enabled of [true, false]) {
      const savedResponse = page.waitForResponse(
        (response) =>
          response.url().endsWith("/dashboard/api/profile") &&
          response.request().method() === "PUT",
      );
      await toggle.click();
      expect((await savedResponse).ok()).toBeTruthy();
      await page.reload();
      await expect(toggle).toBeChecked({ checked: enabled });
    }
  } finally {
    const restored = await page.request.put("/dashboard/api/profile", {
      headers: SAME_ORIGIN_HEADERS,
      data: {
        ...update,
        experimental_task_coordination:
          profile.experimental_task_coordination ?? false,
      },
    });
    expect(restored.ok()).toBeTruthy();
  }
});
