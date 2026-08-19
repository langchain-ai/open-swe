import {
  expect,
  test,
  type Locator,
  type Page,
  type TestInfo,
} from "@playwright/test";

const USER = { login: "alice", email: "alice@example.com" };
const THEME_STORAGE_KEY = "open-swe-theme";

async function login(page: Page) {
  const response = await page.request.post("/control/login", { data: USER });
  expect(response.ok()).toBeTruthy();
}

async function setTheme(page: Page, theme: "light" | "dark") {
  await page.evaluate(
    ([key, value]) => window.localStorage.setItem(key, value),
    [THEME_STORAGE_KEY, theme],
  );
  await page.reload();
  await expect(page.locator("html")).toHaveClass(
    theme === "dark" ? /dark/ : /^(?!.*dark)/,
  );
}

async function expandScrollablePage(page: Page) {
  await page.locator("main").evaluate((main) => {
    main.style.overflow = "visible";
    const shell = main.parentElement;
    if (shell) {
      shell.style.height = "auto";
      shell.style.minHeight = "100vh";
      shell.style.overflow = "visible";
    }
  });
}

async function capture(
  page: Page,
  testInfo: TestInfo,
  name: string,
  options: { fullPage?: boolean; locator?: Locator } = {},
) {
  const path = testInfo.outputPath(`${name}.png`);
  if (options.locator) {
    await options.locator.screenshot({ path });
  } else {
    await page.screenshot({ path, fullPage: options.fullPage ?? true });
  }
  await testInfo.attach(name, { path, contentType: "image/png" });
}

test("LangSmith settings pilot is responsive and theme-aware", async ({
  page,
}, testInfo) => {
  await login(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/login");
  await page.evaluate(
    ([key, value]) => window.localStorage.setItem(key, value),
    [THEME_STORAGE_KEY, "light"],
  );
  await page.goto("/my-settings");

  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
  const pilot = page.locator(".langsmith-settings");
  await expect(pilot).toBeVisible();
  await expect(
    pilot.getByText("GitHub account", { exact: true }),
  ).toBeVisible();
  await expect(pilot.getByText("Connections", { exact: true })).toBeVisible();
  await capture(page, testInfo, "settings-desktop-light");
  await expandScrollablePage(page);
  await capture(page, testInfo, "settings-content-light");

  const account = pilot
    .getByRole("heading", { name: "Account" })
    .locator("xpath=ancestor::section");
  await capture(page, testInfo, "settings-account-light", {
    locator: account,
  });

  const appearance = page.getByRole("combobox").first();
  await appearance.focus();
  await expect(appearance).toBeFocused();
  await appearance.click();
  await expect(page.getByRole("option", { name: "Dark" })).toBeVisible();
  await capture(page, testInfo, "settings-theme-menu-light", {
    fullPage: false,
  });
  await page.getByRole("option", { name: "Dark" }).click();
  await expect(page.locator("html")).toHaveClass(/dark/);
  await capture(page, testInfo, "settings-desktop-dark");
  await capture(page, testInfo, "settings-content-dark");

  const connections = pilot
    .getByRole("heading", { name: "Connections" })
    .locator("xpath=ancestor::section");
  await capture(page, testInfo, "settings-connections-dark", {
    locator: connections,
  });

  await page.setViewportSize({ width: 834, height: 1112 });
  await setTheme(page, "light");
  await capture(page, testInfo, "settings-tablet-light");
  await setTheme(page, "dark");
  await capture(page, testInfo, "settings-tablet-dark");

  await page.setViewportSize({ width: 390, height: 844 });
  await setTheme(page, "light");
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  await capture(page, testInfo, "settings-mobile-light");
  await expandScrollablePage(page);
  await capture(page, testInfo, "settings-mobile-content-light");
  await setTheme(page, "dark");
  await capture(page, testInfo, "settings-mobile-dark");
  await expandScrollablePage(page);
  await capture(page, testInfo, "settings-mobile-content-dark");

  await expect(page.getByText("Save instructions")).toBeVisible();
});
