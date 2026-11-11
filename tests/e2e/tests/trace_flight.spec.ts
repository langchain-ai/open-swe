import { expect, test } from "@playwright/test";

import {
  SAME_USER,
  dismissOnboardingIfShown,
  loginAs,
  readyComposerEditor,
} from "./helpers/dashboard";

const GAME_CHUNK = /\/assets\/TraceFlightGame-[^/]+\.js$/;

test("four quick clicks on the logo open Trace Flight, and Esc brings back the draft", async ({
  page,
}) => {
  const gameRequests: string[] = [];
  page.on("request", (request) => {
    if (GAME_CHUNK.test(request.url())) gameRequests.push(request.url());
  });

  await loginAs(page, SAME_USER);
  await page.goto("/agents");
  await dismissOnboardingIfShown(page);
  const editor = await readyComposerEditor(page);
  await editor.click();
  await editor.pressSequentially("half-written task");

  // The game is its own chunk: the chat page doesn't load it until you reach for it.
  expect(gameRequests).toEqual([]);

  const logo = page.getByRole("button", { name: "Robin" });
  await logo.click({ clickCount: 4 });

  const game = page.getByRole("dialog", { name: "Trace Flight" });
  await expect(game).toBeVisible();
  expect(gameRequests).toHaveLength(1);
  // The leap out of the logo ends in play, where the controls show.
  await expect(game.getByRole("button", { name: "Pause" })).toBeVisible();
  await expect(
    game.getByRole("application", { name: "Trace Flight" }),
  ).toBeFocused();

  await page.keyboard.press("Escape");

  await expect(game).toBeHidden();
  await expect(logo).toBeFocused();
  await expect(editor).toHaveText("half-written task");
});
