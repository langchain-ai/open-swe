import { test, expect } from "@playwright/test";

import { loginAs, SAME_USER, SAME_ORIGIN_HEADERS } from "./helpers/dashboard";

test.use({ viewport: { width: 390, height: 844 } });

test("a new scan recovers in the same tab after opening a link without a code", async ({
  page,
}, testInfo) => {
  await loginAs(page, SAME_USER);
  const issued = await page.request.post("/dashboard/api/auth/qr", {
    headers: SAME_ORIGIN_HEADERS,
  });
  expect(issued.ok()).toBeTruthy();
  const { url } = (await issued.json()) as { url: string };
  await page.goto("/login/qr");
  await page.goto(url);
  await expect(
    page.getByRole("button", { name: "Continue", exact: true }),
  ).toBeEnabled();
  await testInfo.attach("fresh-scan-mobile", {
    body: await page.screenshot(),
    contentType: "image/png",
  });
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(page).toHaveURL(/\/agents$/);
});

test("a phone can follow fresh QR links repeatedly and sees explicit invalid-code errors", async ({
  browser,
  page,
}, testInfo) => {
  await loginAs(page, SAME_USER);
  const phoneContext = await browser.newContext({
    baseURL: testInfo.project.use.baseURL,
    viewport: { width: 390, height: 844 },
  });
  const phone = await phoneContext.newPage();
  try {
    for (let scan = 0; scan < 2; scan++) {
      const issued = await page.request.post("/dashboard/api/auth/qr", {
        headers: SAME_ORIGIN_HEADERS,
      });
      expect(issued.ok()).toBeTruthy();
      const { url } = (await issued.json()) as { url: string };
      await phone.goto(url);
      await expect(
        phone.getByRole("button", { name: "Continue", exact: true }),
      ).toBeEnabled();
      await phone
        .getByRole("button", { name: "Continue", exact: true })
        .click();
      await expect(phone).toHaveURL(/\/agents$/);
      const me = await phone.request.get("/dashboard/api/me");
      expect((await me.json()).login).toBe("alice");
      await phone.goto(url);
      await phone
        .getByRole("button", { name: "Continue", exact: true })
        .click();
      await expect(
        phone.getByRole("heading", { name: "Invalid QR code" }),
      ).toBeVisible();
      await expect(phone).toHaveURL(/\/login\/qr/);
    }
    await phone.goto("/login/qr");
    await expect(
      phone.getByRole("heading", { name: "Invalid QR code" }),
    ).toBeVisible();
    await expect(phone.getByText(/scan a new/i)).toBeVisible();
    expect(
      await phone.evaluate(() => document.documentElement.scrollWidth),
    ).toBe(390);
    await testInfo.attach("invalid-qr-mobile", {
      body: await phone.screenshot(),
      contentType: "image/png",
    });
    await phone.goto("/login/qr#code=malformed");
    await expect(
      phone.getByRole("heading", { name: "Invalid QR code" }),
    ).toBeVisible();
    await expect(
      phone.getByRole("button", { name: "Continue", exact: true }),
    ).toHaveCount(0);
    await expect(phone.getByRole("button", { name: /github/i })).toHaveCount(0);
    await phone.emulateMedia({ colorScheme: "dark" });
    await phone.reload();
    await expect(phone.locator("html")).toHaveClass(/dark/);
    await testInfo.attach("invalid-qr-mobile-dark", {
      body: await phone.screenshot(),
      contentType: "image/png",
    });
  } finally {
    await phoneContext.close();
  }
});
