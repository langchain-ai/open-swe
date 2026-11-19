import { expect, test } from "@playwright/test";
import { createRequire } from "node:module";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { createServer } from "node:http";
import { gzipSync } from "node:zlib";
import { _electron as electron } from "playwright";

const repoRoot = resolve(__dirname, "..", "..", "..");
const desktopRoot = join(repoRoot, "desktop");
const uiArchive = join(repoRoot, "ui", ".output", "desktop", "ui.json.gz");
const electronPath = createRequire(join(desktopRoot, "package.json"))(
  "electron",
) as string;

test("Desktop keeps cloud browser authentication redirects inside the viewer", async () => {
  const stateRoot = mkdtempSync(join(tmpdir(), "desktop-browser-"));
  const archive = join(stateRoot, "ui.json.gz");
  writeFileSync(
    archive,
    gzipSync(
      JSON.stringify({
        files: [
          {
            path: "/_shell.html",
            body: Buffer.from(
              "<html><body>Browser fixture</body></html>",
            ).toString("base64"),
          },
        ],
      }),
    ),
  );
  const server = createServer((request, response) => {
    if (request.url === "/auth") {
      response.writeHead(302, {
        "set-cookie":
          "viewer=session; HttpOnly; Path=/act; SameSite=None; Secure; Partitioned",
        location: "/act",
      });
      response.end();
      return;
    }
    const authenticated = request.headers.cookie === "viewer=session";
    response.writeHead(authenticated ? 200 : 403, {
      "content-type": "text/html",
    });
    response.end(
      authenticated ? "Viewer connected" : "Window link is not valid",
    );
  });
  await new Promise<void>((ready) => server.listen(0, "127.0.0.1", ready));
  const address = server.address();
  if (!address || typeof address === "string")
    throw new Error("No viewer address");
  const viewerOrigin = `http://127.0.0.1:${address.port}`;
  const electronApp = await electron.launch({
    executablePath: electronPath,
    args: [
      ...(process.platform === "linux" ? ["--no-sandbox"] : []),
      desktopRoot,
      "--backend-url=https://desktop.test",
    ],
    env: {
      ...process.env,
      HOME: stateRoot,
      XDG_CONFIG_HOME: stateRoot,
      APPDATA: stateRoot,
      OPEN_SWE_DESKTOP_UI_ARCHIVE: archive,
    },
  });
  try {
    const page = await electronApp.firstWindow();
    await page.waitForURL("open-swe://app/**", { waitUntil: "load" });
    await page.evaluate((origin) => {
      const frame = document.createElement("iframe");
      frame.title = "Browser window";
      frame.src = `${origin}/auth`;
      document.body.append(frame);
    }, viewerOrigin);
    await expect(
      page
        .frameLocator('iframe[title="Browser window"]')
        .getByText("Viewer connected", { exact: true }),
    ).toBeVisible({ timeout: 10_000 });
  } finally {
    await electronApp.close();
    await new Promise<void>((closed, reject) =>
      server.close((error) => (error ? reject(error) : closed())),
    );
    rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("Desktop links supersede startup loading", async () => {
  const stateRoot = mkdtempSync(join(tmpdir(), "desktop-links-"));
  const electronApp = await electron.launch({
    executablePath: electronPath,
    args: [
      ...(process.platform === "linux" ? ["--no-sandbox"] : []),
      desktopRoot,
      "--backend-url=https://desktop.test",
    ],
    env: {
      ...process.env,
      HOME: stateRoot,
      XDG_CONFIG_HOME: stateRoot,
      APPDATA: stateRoot,
      OPEN_SWE_DESKTOP_UI_ARCHIVE: uiArchive,
    },
  });
  try {
    const page = await electronApp.firstWindow();
    await page.waitForURL("open-swe://app/**", { waitUntil: "load" });
    await electronApp.evaluate(({ protocol }) => {
      protocol.unhandle("open-swe");
      protocol.handle("open-swe", () => new Response("Ready"));
    });
    const desktopWindow = await electronApp.browserWindow(page);
    await page.goto("data:text/html,Reload fixture");
    await expect
      .poll(() =>
        desktopWindow.evaluate((window) => window.webContents.isLoading()),
      )
      .toBe(false);
    await electronApp.evaluate(({ app, Menu }) => {
      Menu.getApplicationMenu()
        ?.items.find((item) => item.label === "View")
        ?.submenu?.items.find((item) => item.label === "Reload")
        ?.click();
      app.emit(
        "continue-activity",
        { preventDefault() {} },
        "NSUserActivityTypeBrowsingWeb",
        {},
        {
          webpageURL:
            "https://desktop.test/agents/reviews/a/b/1?tab=changes#file-1",
        },
      );
    });
    await expect(page.getByText("Ready", { exact: true })).toBeVisible();
    await expect(page).toHaveURL(
      "open-swe://app/agents/reviews/a/b/1?tab=changes#file-1",
    );
    await page.evaluate(() => window.openSweDesktop!.copyPageLink());
    expect(
      await electronApp.evaluate(({ clipboard }) => clipboard.readText()),
    ).toBe("https://desktop.test/agents/reviews/a/b/1?tab=changes#file-1");
    await page.goto("data:text/html,Non-app page");
    await expect
      .poll(() =>
        desktopWindow.evaluate((window) => window.webContents.isLoading()),
      )
      .toBe(false);
    await electronApp.evaluate(({ Menu }) => {
      Menu.getApplicationMenu()
        ?.items.find((item) => item.label === "View")
        ?.submenu?.items.find((item) => item.label === "Reload")
        ?.click();
    });
    await expect(page.getByText("Ready", { exact: true })).toBeVisible();
    await expect(page).toHaveURL("open-swe://app/");
  } finally {
    await electronApp.close();
    rmSync(stateRoot, { recursive: true, force: true });
  }
});
