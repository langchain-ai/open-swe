import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import os from "node:os";
import path from "node:path";
import { _electron as electron } from "playwright";

const repoRoot = path.resolve(__dirname, "..", "..", "..");
const desktopRoot = path.join(repoRoot, "desktop");
const graphEntrypoint = path.join(repoRoot, "tests", "e2e", "desktop-agent.ts");
const electronPath = createRequire(path.join(desktopRoot, "package.json"))(
  "electron",
) as string;

function runGit(cwd: string, ...args: string[]): void {
  execFileSync("git", args, { cwd, stdio: "pipe" });
}

async function typeIntoComposer(
  page: import("@playwright/test").Page,
  text: string,
): Promise<void> {
  const editor = page.getByTestId("composer-editor");
  await editor.click();
  await editor.pressSequentially(text);
  await editor.press("Enter");
}

// Playwright parses the fixture object out of the source, so the empty pattern
// is how a test takes `testInfo` without provisioning any fixture.
// oxlint-disable-next-line no-empty-pattern
test("This Mac runs and restores the TypeScript coding agent", async ({}, testInfo) => {
  const stateRoot = mkdtempSync(
    path.join(os.tmpdir(), "open-swe-desktop-e2e-"),
  );
  const home = path.join(stateRoot, "home");
  const project = path.join(stateRoot, "project");
  const gitConfig = path.join(stateRoot, "gitconfig");
  mkdirSync(home);
  mkdirSync(project);
  writeFileSync(gitConfig, "");
  writeFileSync(path.join(project, "README.md"), "# TypeScript fixture\n");
  runGit(project, "init", "-q");
  runGit(project, "config", "user.name", "Test");
  runGit(project, "config", "user.email", "test@example.com");
  runGit(project, "add", ".");
  runGit(project, "commit", "-qm", "fixture");

  const electronApp = await electron.launch({
    executablePath: electronPath,
    args: [
      ...(process.platform === "linux" ? ["--no-sandbox"] : []),
      "--password-store=basic",
      desktopRoot,
      "--dev",
    ],
    cwd: repoRoot,
    env: {
      ...process.env,
      HOME: home,
      XDG_CONFIG_HOME: path.join(stateRoot, "xdg-config"),
      APPDATA: path.join(stateRoot, "app-data"),
      GIT_CONFIG_GLOBAL: gitConfig,
      OPENAI_API_KEY: "e2e-fake-key",
      OPEN_SWE_LOCAL_GRAPH_ENTRYPOINT: graphEntrypoint,
      OPEN_SWE_LOCAL_SERVER_LOGS: "1",
    },
  });

  const electronProcess = electronApp.process();
  const events: string[] = [];
  const record = (message: string): void => {
    events.push(`${new Date().toISOString()} ${message}`);
  };
  electronProcess.stdout?.on("data", (chunk) =>
    record(`electron stdout: ${chunk}`),
  );
  electronProcess.stderr?.on("data", (chunk) =>
    record(`electron stderr: ${chunk}`),
  );
  try {
    const page = await electronApp.firstWindow();
    page.on("console", (message) =>
      record(`console ${message.type()}: ${message.text()}`),
    );
    page.on("requestfailed", (request) =>
      record(
        `request failed: ${request.method()} ${request.url()} ${request.failure()?.errorText}`,
      ),
    );
    page.on("response", (response) => {
      if (response.status() >= 400) {
        record(
          `response ${response.status()}: ${response.request().method()} ${response.url()}`,
        );
      }
    });
    record(`first window: ${page.url()}`);
    const userData = await electronApp.evaluate(({ app }) =>
      app.getPath("userData"),
    );
    writeFileSync(
      path.join(userData, "desktop-projects.json"),
      `${JSON.stringify(
        [{ cwd: realpathSync(project), name: "project", addedAt: Date.now() }],
        null,
        2,
      )}\n`,
      { mode: 0o600 },
    );

    await page.reload();
    record(`reloaded after project registration: ${page.url()}`);
    const localMode = page.getByRole("link", {
      name: "Continue in local mode",
    });
    if (!/\/agents(?:\/|$)/.test(new URL(page.url()).pathname)) {
      await expect(localMode).toBeVisible({ timeout: 30_000 });
      await localMode.click();
      record("entered local mode");
    }
    await expect(page).toHaveURL(/\/agents/);
    record(`agents route ready: ${page.url()}`);
    await expect(
      page.getByRole("button", { name: "project", exact: true }),
    ).toBeVisible();
    record("project visible");

    await typeIntoComposer(page, "Add and verify a typed greeting helper.");
    record("prompt submitted");

    await expect(page).toHaveURL(/\/agents\/local\//);
    await expect(
      page.getByText(
        "Done. I added and verified the TypeScript greeting helper.",
      ),
    ).toBeVisible();
    record("stream completed");
    await expect
      .poll(() => existsSync(path.join(project, "greeting.ts")))
      .toBe(true);
    expect(readFileSync(path.join(project, "greeting.ts"), "utf8")).toContain(
      "function greet(name: string): string",
    );

    await page.getByRole("button", { name: "Show panel" }).click();
    await page.getByRole("button", { name: /Changes/ }).click();
    await expect(page.getByText("greeting.ts", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Add panel surface" }).click();
    await page.getByRole("menuitem", { name: /Terminal/ }).click();
    await expect(
      page.getByRole("textbox", { name: "Terminal input" }),
    ).toBeVisible();
    record("diff and terminal visible");

    const threadUrl = page.url();
    await page.reload();
    await expect(page).toHaveURL(threadUrl);
    await expect(
      page.getByText(
        "Done. I added and verified the TypeScript greeting helper.",
      ),
    ).toBeVisible();
    record("thread restored");
  } finally {
    const eventsPath = testInfo.outputPath("desktop-events.txt");
    writeFileSync(eventsPath, `${events.join("\n")}\n`);
    await testInfo.attach("desktop-events", {
      path: eventsPath,
      contentType: "text/plain",
    });
    await Promise.race([
      electronApp.close().catch(() => {}),
      new Promise<void>((resolve) => setTimeout(resolve, 5_000)),
    ]);
    if (electronProcess.exitCode === null) electronProcess.kill("SIGKILL");
    if (!process.env.E2E_KEEP_TMP)
      rmSync(stateRoot, { recursive: true, force: true });
  }
});
