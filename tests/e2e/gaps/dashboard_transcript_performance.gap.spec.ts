import { expect, test } from "@playwright/test";

import {
  SAME_USER,
  loginAs,
  openThreadViaSlackLink,
  threadIdFromUrl,
  waitForThreadIdle,
} from "../tests/helpers/dashboard";

test("a long transcript mounts a bounded tail and stays responsive", async ({
  page,
}) => {
  await loginAs(page, SAME_USER);
  await openThreadViaSlackLink(page);
  const threadId = threadIdFromUrl(page);
  await waitForThreadIdle(page, threadId);

  await page.addInitScript(() => {
    const metrics = { longTasks: [] as number[], rafGaps: [] as number[] };
    let previous = performance.now();
    const sample = (now: number) => {
      metrics.rafGaps.push(now - previous);
      previous = now;
      requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
    try {
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries())
          metrics.longTasks.push(entry.duration);
      }).observe({ type: "longtask", buffered: true });
    } catch {}
    Object.assign(window, { __gapMetrics: metrics });
  });

  await page.route(
    `**/dashboard/api/threads/${threadId}/state`,
    async (route) => {
      const response = await route.fetch();
      const body = (await response.json()) as {
        values?: { messages?: Array<Record<string, unknown>> };
      };
      const messages = Array.from({ length: 1_500 }, (_, index) => ({
        type: "human",
        id: `long-user-${index}`,
        content: `Long transcript message ${index} with enough markdown content to exercise layout and rendering.`,
      }));
      body.values = { ...body.values, messages };
      await route.fulfill({ response, json: body });
    },
  );

  const started = Date.now();
  await page.goto(`/agents/${threadId}`);
  await expect(page.getByText("Long transcript message 1499")).toBeVisible();
  await expect(page.getByTestId("composer-editor")).toBeVisible();
  const interactiveMs = Date.now() - started;
  const mountedRows = await page.getByTestId("user-message").count();
  const metrics = await page.evaluate(
    () =>
      (
        window as typeof window & {
          __gapMetrics: { longTasks: number[]; rafGaps: number[] };
        }
      ).__gapMetrics,
  );

  expect.soft(mountedRows).toBeLessThanOrEqual(500);
  expect.soft(interactiveMs).toBeLessThanOrEqual(1_500);
  expect.soft(Math.max(0, ...metrics.longTasks)).toBeLessThanOrEqual(200);
  expect.soft(Math.max(0, ...metrics.rafGaps)).toBeLessThanOrEqual(200);
});
