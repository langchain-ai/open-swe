const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
  AUTH_ISSUER,
  OAUTH_APPLICATION_ID,
  OpenAiOAuthManager,
  beginOpenAiLogin,
  buildAuthorizeUrl,
} = require("../build/openai-oauth.cjs");

test("desktop auth never invokes operating-system credential storage", () => {
  const source = ["main.cts", "openai-oauth.cts"]
    .map((name) =>
      fs.readFileSync(path.resolve(__dirname, "../src", name), "utf8"),
    )
    .join("\n");

  assert.doesNotMatch(
    source,
    /\bsafeStorage\b|openai-auth\.bin|\bencryptString\b|\bdecryptString\b/,
  );
});

function jwt(payload) {
  return `header.${Buffer.from(JSON.stringify(payload)).toString("base64url")}.signature`;
}

test("builds the native OAuth authorization request with PKCE", () => {
  const url = new URL(
    buildAuthorizeUrl({
      redirectUri: "http://localhost:1455/auth/callback",
      challenge: "test-key",
      state: "example",
    }),
  );
  assert.equal(url.origin, AUTH_ISSUER);
  assert.equal(url.pathname, "/oauth/authorize");
  assert.equal(url.searchParams.get("client_id"), OAUTH_APPLICATION_ID);
  assert.equal(url.searchParams.get("code_challenge"), "test-key");
  assert.equal(url.searchParams.get("code_challenge_method"), "S256");
  assert.equal(url.searchParams.get("state"), "example");
  assert.match(url.searchParams.get("scope"), /offline_access/);
});

test("accepts only the matching state on the loopback callback", async () => {
  const flow = await beginOpenAiLogin({ ports: [0] });
  const response = await fetch(
    `http://127.0.0.1:${flow.port}/auth/callback?code=example&state=${flow.state}`,
  );
  assert.equal(response.status, 200);
  assert.match(await response.text(), /You're signed in/);
  assert.deepEqual(await flow.result, {
    code: "example",
    verifier: flow.verifier,
    redirectUri: `http://localhost:${flow.port}/auth/callback`,
  });
});

test("keeps credentials in memory and refreshes them through the loopback broker", async (t) => {
  const now = 2_000_000_000_000;
  const accountId = "example";
  const idToken = jwt({
    "https://api.openai.com/auth": { chatgpt_account_id: accountId },
  });
  const expiredAccessToken = jwt({ exp: now / 1000 - 10 });
  const freshAccessToken = jwt({ exp: now / 1000 + 3600 });
  const requests = [];
  const manager = new OpenAiOAuthManager({
    now: () => now,
    fetchImpl: async (url, init) => {
      requests.push({ url, init });
      if (
        init.headers["content-type"] === "application/x-www-form-urlencoded"
      ) {
        return new Response(
          JSON.stringify({
            access_token: expiredAccessToken,
            refresh_token: "test-token",
            id_token: idToken,
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      return new Response(
        JSON.stringify({
          access_token: freshAccessToken,
          refresh_token: "test-token-placeholder",
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    },
  });
  t.after(() => manager.close());

  await manager.exchangeCode({
    code: "example",
    verifier: "example",
    redirectUri: "http://localhost:1455/auth/callback",
  });
  assert.equal(manager.status().signedIn, true);

  const env = await manager.startBroker();
  const response = await fetch(env.OPEN_SWE_OPENAI_OAUTH_BROKER_URL, {
    headers: {
      Authorization: `Bearer ${env.OPEN_SWE_OPENAI_OAUTH_BROKER_TOKEN}`,
    },
  });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {
    access_token: freshAccessToken,
    account_id: accountId,
  });
  assert.equal(requests.length, 2);
  assert.deepEqual(JSON.parse(requests[1].init.body), {
    client_id: OAUTH_APPLICATION_ID,
    grant_type: "refresh_token",
    refresh_token: "test-token",
  });
});

test("rejects unauthenticated broker requests", async (t) => {
  const manager = new OpenAiOAuthManager({});
  t.after(() => manager.close());
  const env = await manager.startBroker();
  const response = await fetch(env.OPEN_SWE_OPENAI_OAUTH_BROKER_URL);
  assert.equal(response.status, 401);
});

test("reuses a valid shared local OpenAI login without copying it", async (t) => {
  const directory = fs.mkdtempSync(
    path.join(os.tmpdir(), "open-swe-shared-openai-auth-"),
  );
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const now = 2_000_000_000_000;
  const sharedPath = path.join(directory, "auth.json");
  fs.writeFileSync(
    sharedPath,
    JSON.stringify({
      tokens: {
        access_token: jwt({ exp: now / 1000 + 3600 }),
        refresh_token: "test-token",
        id_token: jwt({
          "https://api.openai.com/auth": {
            chatgpt_account_id: "example",
          },
        }),
        account_id: "example",
      },
      last_refresh: new Date(now).toISOString(),
    }),
    { mode: 0o600 },
  );
  const original = fs.readFileSync(sharedPath, "utf8");
  const manager = new OpenAiOAuthManager({
    sharedCredentialsPath: sharedPath,
    now: () => now,
  });
  t.after(() => manager.close());

  assert.equal(manager.status().signedIn, true);
  assert.match(await manager.accessToken(), /^header\./);
  assert.equal(fs.readFileSync(sharedPath, "utf8"), original);
});

test("stops using shared credentials when the shared login is removed", async (t) => {
  const directory = fs.mkdtempSync(
    path.join(os.tmpdir(), "open-swe-shared-openai-removed-"),
  );
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const now = 2_000_000_000_000;
  const sharedPath = path.join(directory, "auth.json");
  writeSharedCredentials(sharedPath, now, now / 1000 + 3600);
  const manager = new OpenAiOAuthManager({
    sharedCredentialsPath: sharedPath,
    now: () => now,
  });
  t.after(() => manager.close());

  assert.equal(manager.status().signedIn, true);
  fs.unlinkSync(sharedPath);

  assert.equal(manager.status().signedIn, false);
  await assert.rejects(manager.accessToken(), /Sign in to use OpenAI models/);
});

test("notices when an initially expired shared login is refreshed externally", async (t) => {
  const directory = fs.mkdtempSync(
    path.join(os.tmpdir(), "open-swe-shared-openai-refreshed-"),
  );
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const now = 2_000_000_000_000;
  const sharedPath = path.join(directory, "auth.json");
  writeSharedCredentials(sharedPath, now, now / 1000 - 10);
  const manager = new OpenAiOAuthManager({
    sharedCredentialsPath: sharedPath,
    now: () => now,
  });
  t.after(() => manager.close());

  assert.equal(manager.status().signedIn, false);
  writeSharedCredentials(sharedPath, now, now / 1000 + 3600);

  assert.equal(manager.status().signedIn, true);
  assert.match(await manager.accessToken(), /^header\./);
});

test("clears credentials when refresh authorization is permanently rejected", async (t) => {
  const now = 2_000_000_000_000;
  const manager = new OpenAiOAuthManager({
    now: () => now,
    fetchImpl: async () => new Response(null, { status: 401 }),
  });
  t.after(() => manager.close());
  manager.saveCredentials({
    accessToken: jwt({ exp: now / 1000 - 10 }),
    refreshToken: "test-token",
    idToken: jwt({
      "https://api.openai.com/auth": { chatgpt_account_id: "example" },
    }),
    refreshedAt: now,
  });

  await assert.rejects(manager.accessToken(), /could not be refreshed \(401\)/);
  assert.equal(manager.status().signedIn, false);
});

function writeSharedCredentials(sharedPath, refreshedAt, expiresAt) {
  fs.writeFileSync(
    sharedPath,
    JSON.stringify({
      tokens: {
        access_token: jwt({ exp: expiresAt }),
        refresh_token: "test-token",
        id_token: jwt({
          "https://api.openai.com/auth": {
            chatgpt_account_id: "example",
          },
        }),
        account_id: "example",
      },
      last_refresh: new Date(refreshedAt).toISOString(),
    }),
    { mode: 0o600 },
  );
}
