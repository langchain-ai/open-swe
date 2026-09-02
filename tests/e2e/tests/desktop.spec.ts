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
import { delimiter, join, resolve } from "node:path";
import { _electron as electron } from "playwright";

const repoRoot = resolve(__dirname, "..", "..", "..");
const desktopRoot = join(repoRoot, "desktop");
const e2eRoot = join(repoRoot, "tests", "e2e");
const baseURL = `http://127.0.0.1:${process.env.E2E_PORT ?? "2024"}`;
const e2eTmp = resolve(process.env.E2E_TMP ?? join(e2eRoot, ".e2e-tmp"));
const fakeRemote = join(e2eTmp, "github", "fakeorg__demo.git");
const electronPath = createRequire(join(desktopRoot, "package.json"))(
  "electron",
) as string;

function sessionCookie(setCookie: string): string {
  const match = setCookie.match(/(?:^|,\s*)osw_session=([^;]+)/);
  if (!match)
    throw new Error("Harness login did not return an osw_session cookie");
  return match[1];
}

async function typeIntoComposer(
  page: import("@playwright/test").Page,
  text: string,
) {
  const editor = page.getByTestId("composer-editor");
  await editor.click();
  await editor.pressSequentially(text);
  await editor.press("Enter");
}

test("Desktop runs a local thread on the Open SWE graph against the shared fakes", async ({
  request,
}, testInfo) => {
  mkdirSync(e2eTmp, { recursive: true });
  const stateRoot = mkdtempSync(join(e2eTmp, "desktop-"));
  const home = join(stateRoot, "home");
  const project = join(stateRoot, "demo");
  const gitConfig = join(stateRoot, "gitconfig");
  mkdirSync(home, { recursive: true });
  writeFileSync(gitConfig, "");

  const reset = await request.post("/control/reset");
  expect(reset.ok()).toBeTruthy();
  execFileSync("git", ["clone", fakeRemote, project], {
    env: { ...process.env, GIT_CONFIG_GLOBAL: gitConfig },
    stdio: "pipe",
  });

  const login = await request.post("/control/login", {
    data: { login: "alice", email: "alice@example.com" },
  });
  expect(login.ok()).toBeTruthy();
  const cookie = sessionCookie(login.headers()["set-cookie"] ?? "");
  const pythonPath = [e2eRoot, process.env.PYTHONPATH]
    .filter((value): value is string => Boolean(value))
    .join(delimiter);

  const electronApp = await electron.launch({
    executablePath: electronPath,
    args: [
      ...(process.platform === "linux" ? ["--no-sandbox"] : []),
      desktopRoot,
      "--dev",
      `--backend-url=${baseURL}`,
    ],
    cwd: repoRoot,
    env: {
      ...process.env,
      HOME: home,
      XDG_CONFIG_HOME: join(stateRoot, "xdg-config"),
      APPDATA: join(stateRoot, "app-data"),
      OPEN_SWE_BACKEND_URL: baseURL,
      // The local backend runs the real graph with the scripted fake model, so
      // the provider keys only have to satisfy the composer's credential gate.
      OPEN_SWE_LOCAL_BACKEND_CONFIG: join(e2eRoot, "langgraph.desktop.json"),
      ANTHROPIC_API_KEY: "e2e-fake-key",
      OPENAI_API_KEY: "e2e-fake-key",
      E2E_BASE: baseURL,
      E2E_FAKE_GITHUB_API: `${baseURL}/fake-gh`,
      E2E_REMOTE: fakeRemote,
      E2E_TMP: e2eTmp,
      GIT_CONFIG_GLOBAL: gitConfig,
      PYTHONPATH: pythonPath,
      UV_CACHE_DIR: join(e2eTmp, "uv-cache"),
    },
  });

  const context = electronApp.context();
  await context.tracing.start({
    screenshots: true,
    snapshots: true,
    sources: true,
  });
  const trace = testInfo.outputPath("electron-trace.zip");
  let page: Awaited<ReturnType<typeof electronApp.firstWindow>> | null = null;
  try {
    page = await electronApp.firstWindow();
    const userData = await electronApp.evaluate(({ app }) =>
      app.getPath("userData"),
    );
    mkdirSync(userData, { recursive: true });
    writeFileSync(
      join(userData, "desktop-projects.json"),
      `${JSON.stringify(
        [{ cwd: realpathSync(project), name: "demo", addedAt: Date.now() }],
        null,
        2,
      )}\n`,
      { mode: 0o600 },
    );
    await electronApp.evaluate(
      async ({ session }, details) => {
        await session.defaultSession.cookies.set(details);
      },
      {
        url: `${baseURL}/`,
        name: "osw_session",
        value: cookie,
        path: "/",
        httpOnly: true,
      },
    );

    const profileResponse = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === "/dashboard/api/profile",
    );
    await page.goto("open-swe://app/agents");
    const profile = (await (await profileResponse).json()) as {
      default_model?: string | null;
    };
    if (!profile.default_model) {
      const maybeLater = page.getByRole("button", { name: "Maybe later" });
      await expect(maybeLater).toBeVisible();
      await maybeLater.click();
    }

    await expect(
      page.getByRole("button", { name: /Cloud threads, \d+/ }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: /This Mac threads, \d+/ }),
    ).toHaveCount(0);
    const sidebar = page.locator("[data-sidebar-frame]");
    const composer = page.getByTestId("composer-editor").locator("../../..");
    await expect(
      composer.getByRole("button", { name: "This Mac", exact: true }),
    ).toBeVisible();
    await expect(
      composer.getByRole("button", { name: "demo", exact: true }),
    ).toBeVisible();
    await expect(
      composer.getByRole("button", { name: "main", exact: true }),
    ).toBeVisible();
    const unifiedScreenshot = testInfo.outputPath(
      "desktop-unified-threads.png",
    );
    await page.screenshot({ path: unifiedScreenshot, fullPage: true });
    await testInfo.attach("desktop-unified-threads", {
      path: unifiedScreenshot,
      contentType: "image/png",
    });

    await typeIntoComposer(
      page,
      "E2E_DESKTOP_LOCAL please add a greet() helper and open a PR",
    );

    await expect(page).toHaveURL(/open-swe:\/\/app\/agents\/local\//);
    await expect(page.getByText(/Done! I added/)).toBeVisible();
    await expect(
      sidebar.getByRole("link", {
        name: /E2E_DESKTOP_LOCAL please add a greet\(\) helper and open a PR/,
      }),
    ).toBeVisible();
    const prLink = page.getByRole("link", {
      name: "Add greet() helper",
      exact: true,
    });
    await expect(prLink).toHaveAttribute(
      "href",
      `${baseURL}/mock/github/fakeorg/demo/pull/1`,
    );

    await expect.poll(() => existsSync(join(project, "greet.py"))).toBe(true);
    expect(readFileSync(join(project, "greet.py"), "utf8")).toContain(
      'return f"Hello, {name}!"',
    );

    await expect
      .poll(async () => {
        const response = await request.get("/mock/github/data");
        if (!response.ok()) return [];
        return response.json();
      })
      .toMatchObject([
        {
          title: "Add greet() helper",
          head: "add-greet",
          base: "main",
          draft: true,
          files: [{ filename: "greet.py" }],
        },
      ]);

    const screenshot = testInfo.outputPath("desktop-local-agent.png");
    await page.screenshot({ path: screenshot, fullPage: true });
    await testInfo.attach("desktop-local-agent", {
      path: screenshot,
      contentType: "image/png",
    });
  } finally {
    await context.tracing.stop({ path: trace }).catch(() => {});
    if (existsSync(trace)) {
      await testInfo.attach("electron-trace", {
        path: trace,
        contentType: "application/zip",
      });
    }
    await electronApp.close().catch(() => {});
    if (!process.env.E2E_KEEP_TMP)
      rmSync(stateRoot, { recursive: true, force: true });
  }
});
