import { test, expect } from "@playwright/test";
import { loginAs, SAME_USER } from "./helpers/dashboard";

// Most of these assert on the raw HTTP response, never the hydrated DOM: a
// bundling or config regression that breaks server rendering falls back to the
// client and still looks correct in a browser, so only the response body
// catches it. The browser ones check where that markup leads once it hydrates.
test.describe("server rendering", () => {
  test("an unauthenticated app route redirects before any HTML is sent", async ({
    request,
  }) => {
    const res = await request.get("/agents", { maxRedirects: 0 });
    expect(res.status()).toBe(307);
    expect(res.headers()["location"]).toBe("/login?redirect=%2Fagents");
  });

  test("queue control requests reach the backend without a dashboard session", async ({
    request,
  }) => {
    const res = await request.post("/merge-queue/control", {
      data: "x".repeat(4097),
      maxRedirects: 0,
    });
    expect(res.status()).toBe(413);
    expect(await res.json()).toEqual({
      detail: "Queue control request is too large",
    });
  });

  test("the login page arrives server-rendered", async ({ request }) => {
    const html = await (await request.get("/login")).text();
    expect(html).toContain("Sign in to Robin");
  });

  test("an authenticated route is rendered without waiting on the session", async ({
    request,
  }) => {
    const login = await request.post("/control/login", {
      data: { login: "alice", email: "alice@example.com" },
    });
    expect(login.ok()).toBeTruthy();

    const html = await (await request.get("/agents")).text();
    // The page is the app's loading frame, and the session the browser
    // resolves afterwards is nowhere in it.
    expect(html).toContain('aria-label="Loading Robin"');
    expect(html).not.toContain("alice");
  });

  test("a signed-in page hydrates the loading frame the server sent", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    const errors: Array<string> = [];
    page.on("pageerror", (error) => errors.push(error.message));

    await page.goto("/agents");
    await expect(page.getByTestId("composer-editor")).toBeVisible();
    // The session the head script fetched usually lands before the routes
    // hydrate; rendering it there would not match the server's markup.
    expect(errors.filter((message) => /#418|hydrat/i.test(message))).toEqual(
      [],
    );
  });

  test("a session cookie the backend turns down still ends at sign-in", async ({
    page,
    baseURL,
  }) => {
    await page
      .context()
      .addCookies([{ name: "osw_session", value: "expired", url: baseURL! }]);
    await page.goto("/agents");
    await expect(page).toHaveURL(/\/login\?redirect=%2Fagents$/);
  });
});
