const test = require("node:test");
const assert = require("node:assert/strict");
const { DesktopEnvironments } = require("../build/desktop-environments.cjs");

function backend(answers) {
  const requests = [];
  let clock = 0;
  const environments = new DesktopEnvironments({
    now: () => clock,
    maxAgeMs: 1_000,
    fetch: async (url) => {
      requests.push(url);
      const answer = answers.shift();
      if (answer instanceof Error) throw answer;
      return new Response(JSON.stringify(answer), { status: 200 });
    },
  });
  return { environments, requests, advance: (ms) => (clock += ms) };
}

test("keeps only well-formed dashboard origins", () => {
  assert.deepEqual(
    DesktopEnvironments.parseDashboardOrigins({
      dashboard_origins: [
        "https://robin.langchain.dev",
        "https://openswe.langchain.dev/",
        "file:///etc/passwd",
        "not a url",
        7,
      ],
    }),
    ["https://robin.langchain.dev", "https://openswe.langchain.dev"],
  );
  assert.deepEqual(
    DesktopEnvironments.parseDashboardOrigins({ environments: [] }),
    [],
  );
  assert.deepEqual(DesktopEnvironments.parseDashboardOrigins(null), []);
});

test("asks a backend once, then again after the answer ages out", async () => {
  const { environments, requests, advance } = backend([
    { dashboard_origins: ["https://openswe.example"] },
    { dashboard_origins: ["https://robin.example", "https://openswe.example"] },
  ]);
  const url = "https://openswe.example/";
  assert.deepEqual(environments.knownDashboardOrigins(url), []);
  assert.deepEqual(await environments.dashboardOrigins(url), [
    "https://openswe.example",
  ]);
  assert.deepEqual(await environments.dashboardOrigins(url), [
    "https://openswe.example",
  ]);
  assert.deepEqual(environments.knownDashboardOrigins(url), [
    "https://openswe.example",
  ]);
  assert.equal(requests.length, 1);
  advance(1_000);
  assert.deepEqual(await environments.dashboardOrigins(url), [
    "https://robin.example",
    "https://openswe.example",
  ]);
  assert.deepEqual(environments.knownDashboardOrigins(url), [
    "https://robin.example",
    "https://openswe.example",
  ]);
  assert.deepEqual(requests, [
    "https://openswe.example/dashboard/api/desktop/environments",
    "https://openswe.example/dashboard/api/desktop/environments",
  ]);
});

test("asks again right after a failed request", async () => {
  const { environments, requests } = backend([
    new Error("offline"),
    { dashboard_origins: ["https://robin.example"] },
  ]);
  const url = "https://openswe.example/";
  await assert.rejects(environments.dashboardOrigins(url), /offline/);
  assert.deepEqual(await environments.dashboardOrigins(url), [
    "https://robin.example",
  ]);
  assert.equal(requests.length, 2);
});
