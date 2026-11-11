import { test, expect } from "@playwright/test";
import { loginAs, SAME_USER } from "./helpers/dashboard";

type Shift = { value: number; nodes: Array<string> };

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
          nodes: entry.sources.map(
            (source) =>
              `${source.node?.textContent?.trim().slice(0, 40) ?? source.node?.nodeName} ` +
              `y ${source.previousRect.y}→${source.currentRect.y} x ${source.previousRect.x}→${source.currentRect.x}`,
          ),
        });
      }
    }).observe({ type: "layout-shift", buffered: true });
  });
  const cdp = await context.newCDPSession(page);
  await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });

  await page.goto("/agents");
  await expect(
    page.getByRole("heading", { name: "What should we work on?" }),
  ).toBeVisible();
  await expect(page.getByTestId("sidebar-threads-skeleton")).toHaveCount(0);
  await page.waitForTimeout(1000);

  const shifts = await page.evaluate(
    () => (window as unknown as { __shifts: Array<Shift> }).__shifts,
  );
  expect(shifts, JSON.stringify(shifts, null, 2)).toEqual([]);
});
