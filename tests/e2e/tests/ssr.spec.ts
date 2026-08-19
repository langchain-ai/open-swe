import { test, expect } from "@playwright/test";

// These assert on the raw HTTP response, never the hydrated DOM: a bundling or
// config regression that breaks server rendering falls back to the client and
// still looks correct in a browser, so only the response body catches it.
test.describe("server rendering", () => {
  test("an unauthenticated app route redirects before any HTML is sent", async ({
    request,
  }) => {
    const res = await request.get("/agents", { maxRedirects: 0 });
    expect(res.status()).toBe(307);
    expect(res.headers()["location"]).toBe("/login?redirect=%2Fagents");
  });

  test("the login page arrives server-rendered", async ({ request }) => {
    const html = await (await request.get("/login")).text();
    expect(html).toContain("Sign in to Jarvis");
  });

  test("an authenticated route is rendered with the session already resolved", async ({
    request,
  }) => {
    const login = await request.post("/control/login", {
      data: { login: "alice", email: "alice@example.com" },
    });
    expect(login.ok()).toBeTruthy();

    const html = await (await request.get("/agents")).text();
    // Sidebar chrome only renders once the root's server-side session lookup
    // returns a user, so its presence proves the whole path ran on the server.
    expect(html).toContain("Automations");
    expect(html).toContain("alice");
  });
});
