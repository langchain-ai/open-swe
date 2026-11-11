import { expect, test, type Page } from "@playwright/test";

// The lab pages expose their test APIs on window; these types mirror the
// shapes in ui/src/features/trace-flight/hosts/lab without importing app code.
interface Run {
  seed: number;
  endTick: number;
  endReason: string;
  flapDeltas: number[];
}
interface HarnessState {
  seed: number;
  ready: boolean;
  ended: string | null;
  tick: number;
  score: number;
  passed: number;
  lives: number;
  hash: number | null;
}
interface HarnessApi {
  load: (seed: number) => void;
  step: (ticks: number) => void;
  setBot: (on: boolean) => void;
  flap: () => void;
  state: () => HarnessState;
  run: () => Run | null;
  replayMatches: () => boolean | null;
}
declare global {
  interface Window {
    traceFlight?: HarnessApi;
    traceFlightLab?: { state: () => { tick: number } };
  }
}

async function openHarness(page: Page, query: string) {
  await page.goto(`/?page=game&manual&${query}`);
  await page.waitForFunction(() => window.traceFlight !== undefined);
}

test("a bot run replays to the same state the server would compute", async ({
  page,
}) => {
  await openHarness(page, "seed=7&bot");
  const state = await page.evaluate(() => {
    window.traceFlight!.step(1800);
    return window.traceFlight!.state();
  });
  expect(state.ended).toBeNull();
  expect(state.passed).toBeGreaterThan(10);
  expect(await page.evaluate(() => window.traceFlight!.replayMatches())).toBe(
    true,
  );
});

test("a link with seed, inputs and tick reproduces the exact frame", async ({
  page,
}) => {
  await openHarness(page, "seed=21&bot");
  const { run, hash } = await page.evaluate(() => {
    window.traceFlight!.step(900);
    return {
      run: window.traceFlight!.run(),
      hash: window.traceFlight!.state().hash,
    };
  });
  expect(run).not.toBeNull();
  await openHarness(
    page,
    `seed=21&inputs=${run!.flapDeltas.join(",")}&tick=${run!.endTick}`,
  );
  const replayed = await page.evaluate(() => window.traceFlight!.state());
  expect(replayed.tick).toBe(run!.endTick);
  expect(replayed.hash).toBe(hash);
});

test("a finished run shows its replayed score and your best on the next run", async ({
  page,
}) => {
  await openHarness(page, "seed=7&bot&tick=1500");
  const score = await page.evaluate(() => window.traceFlight!.state().score);
  await page.getByRole("button", { name: "End run" }).click();
  const card = page.getByRole("dialog", { name: "Run over" });
  await expect(card).toContainText(String(score));
  await card.locator("button", { hasText: "Play again" }).click();
  await expect(card).toBeHidden();
  expect(await page.evaluate(() => window.traceFlight!.state().ready)).toBe(
    true,
  );
});

test("restarting mid-run still submits it, so it becomes your best", async ({
  page,
}) => {
  // Played after mounting, so nothing a link fast-forwarded can stand in for the restarted run.
  await openHarness(page, "seed=7&bot");
  const score = await page.evaluate(() => {
    window.traceFlight!.step(1500);
    return window.traceFlight!.state().score;
  });
  await page.getByRole("button", { name: "Restart" }).click();
  await page.evaluate(() => {
    window.traceFlight!.setBot(false);
    window.traceFlight!.flap();
    window.traceFlight!.step(1);
  });
  await page.getByRole("button", { name: "End run" }).click();
  await expect(page.getByRole("dialog", { name: "Run over" })).toContainText(
    `Best ${score}`,
  );
});

test("a new course that ends before its first frame still shows its card", async ({
  page,
}) => {
  await openHarness(page, "seed=3&inputs=0&tick=600");
  const card = page.getByRole("dialog", { name: "Run over" });
  await expect(card).toBeVisible();
  // A first run has no best to beat; one sent twice would show itself as the best.
  await expect(card).not.toContainText("Best");
  await page.evaluate(() => window.traceFlight!.load(4));
  await expect
    .poll(() => page.evaluate(() => window.traceFlight!.state().seed))
    .toBe(4);
  await page.waitForTimeout(200);
  await expect(card).toBeVisible();
  await card.locator("button", { hasText: "Back to chat" }).focus();
  await page.keyboard.press("Escape");
  await expect(card).toBeHidden();
});

test("the bird lab lands on the linked tick under Strict Mode", async ({
  page,
}) => {
  await page.goto("/?scenario=hit&manual&tick=120");
  await page.waitForFunction(() => window.traceFlightLab !== undefined);
  expect(await page.evaluate(() => window.traceFlightLab!.state().tick)).toBe(
    120,
  );
});

// Screenshots for a person to review, attached to the report; not compared.
for (const theme of ["light", "dark"] as const) {
  test(`captures the harness in ${theme} theme`, async ({ page }, info) => {
    const moments = [
      ["ready", `seed=3&theme=${theme}`],
      ["mid-run", `seed=3&bot&hitboxes&tick=700&theme=${theme}`],
      ["game-over", `seed=3&inputs=0&tick=600&theme=${theme}`],
    ] as const;
    for (const [name, query] of moments) {
      await openHarness(page, query);
      await info.attach(`${theme}-${name}.png`, {
        body: await page.screenshot(),
        contentType: "image/png",
      });
    }
  });
}
