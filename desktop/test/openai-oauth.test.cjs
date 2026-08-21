const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
  AUTH_ISSUER,
  CLIENT_ID,
  OpenAiOAuthManager,
  beginOpenAiLogin,
  buildAuthorizeUrl,
} = require("../build/openai-oauth.cjs");

function jwt(payload) {
  return `header.${Buffer.from(JSON.stringify(payload)).toString("base64url")}.signature`;
}

test("builds the native OAuth authorization request with PKCE", () => {
  const url = new URL(
    buildAuthorizeUrl({
      redirectUri: "http://localhost:1455/auth/callback",
      challenge: "challenge",
      state: "state",
    }),
  );
  assert.equal(url.origin, AUTH_ISSUER);
  assert.equal(url.pathname, "/oauth/authorize");
  assert.equal(url.searchParams.get("client_id"), CLIENT_ID);
  assert.equal(url.searchParams.get("code_challenge"), "challenge");
  assert.equal(url.searchParams.get("code_challenge_method"), "S256");
  assert.equal(url.searchParams.get("state"), "state");
  assert.match(url.searchParams.get("scope"), /offline_access/);
});

test("accepts only the matching state on the loopback callback", async () => {
  const flow = await beginOpenAiLogin({ ports: [0] });
  const response = await fetch(
    `http://127.0.0.1:${flow.port}/auth/callback?code=authorization-code&state=${flow.state}`,
  );
  assert.equal(response.status, 200);
  assert.match(await response.text(), /You're signed in/);
  assert.deepEqual(await flow.result, {
    code: "authorization-code",
    verifier: flow.verifier,
    redirectUri: `http://localhost:${flow.port}/auth/callback`,
  });
});

test("stores encrypted credentials and refreshes them through the loopback broker", async (t) => {
  const directory = fs.mkdtempSync(
    path.join(os.tmpdir(), "open-swe-openai-auth-"),
  );
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const now = 2_000_000_000_000;
  const accountId = "account-123";
  const idToken = jwt({
    "https://api.openai.com/auth": { chatgpt_account_id: accountId },
  });
  const expiredAccessToken = jwt({ exp: now / 1000 - 10 });
  const freshAccessToken = jwt({ exp: now / 1000 + 3600 });
  const requests = [];
  const manager = new OpenAiOAuthManager({
    storagePath: path.join(directory, "auth.bin"),
    now: () => now,
    encryptString: (value) => Buffer.from(value).reverse(),
    decryptString: (value) => Buffer.from(value).reverse().toString("utf8"),
    fetchImpl: async (url, init) => {
      requests.push({ url, init });
      if (
        init.headers["content-type"] === "application/x-www-form-urlencoded"
      ) {
        return new Response(
          JSON.stringify({
            access_token: expiredAccessToken,
            refresh_token: "refresh-1",
            id_token: idToken,
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      return new Response(
        JSON.stringify({
          access_token: freshAccessToken,
          refresh_token: "refresh-2",
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    },
  });
  t.after(() => manager.close());

  await manager.exchangeCode({
    code: "authorization-code",
    verifier: "verifier",
    redirectUri: "http://localhost:1455/auth/callback",
  });
  assert.equal(manager.status().signedIn, true);
  assert.doesNotMatch(
    fs.readFileSync(path.join(directory, "auth.bin"), "utf8"),
    /refresh-1/,
  );

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
    client_id: CLIENT_ID,
    grant_type: "refresh_token",
    refresh_token: "refresh-1",
  });
});

test("rejects unauthenticated broker requests", async (t) => {
  const directory = fs.mkdtempSync(
    path.join(os.tmpdir(), "open-swe-openai-auth-"),
  );
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const manager = new OpenAiOAuthManager({
    storagePath: path.join(directory, "auth.bin"),
    encryptString: (value) => Buffer.from(value),
    decryptString: (value) => value.toString("utf8"),
  });
  t.after(() => manager.close());
  const env = await manager.startBroker();
  const response = await fetch(env.OPEN_SWE_OPENAI_OAUTH_BROKER_URL);
  assert.equal(response.status, 401);
});

test("clears credentials when refresh authorization is permanently rejected", async (t) => {
  const directory = fs.mkdtempSync(
    path.join(os.tmpdir(), "open-swe-openai-auth-"),
  );
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const now = 2_000_000_000_000;
  const manager = new OpenAiOAuthManager({
    storagePath: path.join(directory, "auth.bin"),
    now: () => now,
    encryptString: (value) => Buffer.from(value),
    decryptString: (value) => value.toString("utf8"),
    fetchImpl: async () => new Response(null, { status: 401 }),
  });
  t.after(() => manager.close());
  manager.saveCredentials({
    accessToken: jwt({ exp: now / 1000 - 10 }),
    refreshToken: "refresh-token",
    idToken: jwt({
      "https://api.openai.com/auth": { chatgpt_account_id: "account-123" },
    }),
    refreshedAt: now,
  });

  await assert.rejects(manager.accessToken(), /could not be refreshed \(401\)/);
  assert.equal(manager.status().signedIn, false);
  assert.equal(fs.existsSync(path.join(directory, "auth.bin")), false);
});
