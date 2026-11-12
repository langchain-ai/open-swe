import { test, expect } from "@playwright/test";
import { loginAs, SAME_USER } from "./helpers/dashboard";

type Shift = { value: number; at: number; nodes: Array<string> };

// Data arriving after first paint (threads, projects, repositories) must not
// move what is already on screen. Slowing the CPU widens the window between
// first paint and the data arriving, which is where shifts happen.
test("the new-thread page does not shift while it loads", async ({
  page,
  context,
}) => {
  await loginAs(page, SAME_USER);
  await context.addInitScript(() => {
    const shifts: Array<Shift> = [];
    (window as unknown as { __shifts: Array<Shift> }).__shifts = shifts;
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries() as Array<
        PerformanceEntry & {
          value: number;
          hadRecentInput: boolean;
          sources: Array<{
            node: Node | null;
            previousRect: DOMRectReadOnly;
            currentRect: DOMRectReadOnly;
          }>;
        }
      >) {
        if (entry.hadRecentInput) continue;
        shifts.push({
          value: entry.value,
          at: entry.startTime,
          nodes: entry.sources.map(
            (source) =>
              `${source.node?.textContent?.trim().slice(0, 40) ?? source.node?.nodeName} ` +
              `y ${source.previousRect.y}→${source.currentRect.y} x ${source.previousRect.x}→${source.currentRect.x}`,
          ),
        });
      }
    }).observe({ type: "layout-shift", buffered: true });
  });
  // A second workspace adds the composer's workspace picker, arriving last.
  await page.route("**/dashboard/api/workspaces/options", async (route) => {
    const response = await route.fetch();
    const options = (await response.json()) as {
      workspaces: Array<Record<string, unknown>>;
    };
    await new Promise((resolve) => setTimeout(resolve, 1500));
    await route.fulfill({
      response,
      json: {
        ...options,
        workspaces: [
          ...options.workspaces,
          {
            ...options.workspaces[0],
            slug: "layout-shift-second",
            name: "Second workspace",
            is_default: false,
            repos: [],
          },
        ],
      },
    });
  });
  const cdp = await context.newCDPSession(page);
  await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });

  await page.goto("/agents");
  await expect(
    page.getByRole("heading", { name: "What should we work on?" }),
  ).toBeVisible();
  await expect(page.getByTestId("sidebar-threads-skeleton")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Environment" })).toBeVisible();
  await page.waitForTimeout(1000);

  // Under the throttled CPU Chrome paints the server's markup before it has
  // parsed all of it; those partial frames are not data moving the page.
  const shifts = await page.evaluate(() => {
    const parsed = (
      performance.getEntriesByType(
        "navigation",
      )[0] as PerformanceNavigationTiming
    ).domInteractive;
    return (window as unknown as { __shifts: Array<Shift> }).__shifts.filter(
      (shift) => shift.at >= parsed,
    );
  });
  expect(shifts, JSON.stringify(shifts, null, 2)).toEqual([]);
});
