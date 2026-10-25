import { expect, test } from "@playwright/test";

import { loginAs, SAME_USER, seedOpenPullRequest } from "./helpers/dashboard";

for (const width of [320, 390, 768]) {
  test(`mobile actions remain reachable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await loginAs(page, SAME_USER);
    const pr = await seedOpenPullRequest(page, {
      repo: "fakeorg/demo",
      title: "Improve workspace settings and repository navigation",
      author: "bob",
      head: `mobile-layout-${width}`,
      files: { "mobile.py": "MOBILE = True\n" },
    });
    await page.goto("/agents/reviews");
    await expect(page.getByLabel("Open a pull request review")).toBeInViewport({
      ratio: 1,
    });
    await expect(
      page.getByRole("button", { name: "All Reviews", exact: true }),
    ).toBeInViewport({ ratio: 1 });

    await page.goto(`/agents/reviews/fakeorg/demo/${pr.number}`);
    const title = page.getByRole("heading", {
      name: /Improve workspace settings/,
    });
    await expect(title).toBeVisible();
    expect((await title.boundingBox())!.width).toBeGreaterThan(150);
    await expect(
      page.getByLabel("Review channel", { exact: true }),
    ).toBeInViewport({ ratio: 1 });
    await expect(
      page.getByRole("button", {
        name: "Request review in Slack",
        exact: true,
      }),
    ).toBeInViewport({ ratio: 1 });
    if (width < 768) {
      const wrap = page.getByRole("button", {
        name: "Wrap lines",
        exact: true,
      });
      await wrap.scrollIntoViewIfNeeded();
      const bounds = (await wrap.boundingBox())!;
      expect(bounds.width).toBeGreaterThanOrEqual(44);
      expect(bounds.height).toBeGreaterThanOrEqual(44);
      await wrap.click();
      await expect(wrap).toHaveAttribute("aria-pressed", "true");
    }

    await page.goto("/admin/users");
    await expect(
      page.getByRole("button", { name: "Send to everyone", exact: true }),
    ).toBeInViewport({ ratio: 1 });
  });
}

for (const viewport of [
  { width: 1512, height: 850 },
  { width: 390, height: 844 },
]) {
  test(`admin scroll stays inside the content pane at ${viewport.width}px`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    await loginAs(page, SAME_USER);
    await page.goto("/admin");
    await expect(
      page.getByRole("heading", { name: /Expedited Slack review/ }),
    ).toBeAttached();

    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollHeight))
      .toBe(viewport.height);

    await expect(
      page.getByRole("heading", { name: /Expedited Slack review/ }),
    ).toBeVisible();
    // Scroll on every poll: a client render after hydration can reset it.
    await expect
      .poll(() =>
        page.locator("main").evaluate((element) => {
          element.scrollTop = element.scrollHeight;
          return element.scrollTop;
        }),
      )
      .toBeGreaterThan(0);

    await page.evaluate(() =>
      window.scrollTo(0, document.documentElement.scrollHeight),
    );
    expect(await page.evaluate(() => window.scrollY)).toBe(0);
  });
}
