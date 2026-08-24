const fs = require("node:fs");
const http = require("node:http");
const { createHash, randomBytes, timingSafeEqual } = require("node:crypto");

const AUTH_ISSUER = "https://auth.openai.com";
const TOKEN_URL = `${AUTH_ISSUER}/oauth/token`;
const API_BASE_URL = "https://chatgpt.com/backend-api/codex";
const OAUTH_APPLICATION_ID = "app_EMoamEEZ73f0CkXaXp7hrann";
const CALLBACK_PORTS = [1455, 1457];
const LOGIN_TIMEOUT_MS = 5 * 60 * 1000;
const REFRESH_WINDOW_MS = 5 * 60 * 1000;
const FALLBACK_REFRESH_AGE_MS = 7 * 24 * 60 * 60 * 1000;

const SIGNED_IN_PAGE = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Open SWE</title>
<style>:root{color-scheme:light dark}body{font:16px/1.5 system-ui,-apple-system,sans-serif;margin:0;min-height:100vh;display:grid;place-items:center;text-align:center;padding:2rem}h1{font-size:1.25rem;margin:0 0 .5rem}p{margin:0;opacity:.7}</style>
</head><body><main><h1>You're signed in</h1><p>You can close this tab and return to Open SWE.</p></main></body></html>`;

const FAILED_PAGE = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Open SWE</title>
<style>:root{color-scheme:light dark}body{font:16px/1.5 system-ui,-apple-system,sans-serif;margin:0;min-height:100vh;display:grid;place-items:center;text-align:center;padding:2rem}h1{font-size:1.25rem;margin:0 0 .5rem}p{margin:0;opacity:.7}</style>
</head><body><main><h1>Sign-in failed</h1><p>Return to Open SWE and try again.</p></main></body></html>`;

function decodeJwtPayload(token) {
  if (typeof token !== "string") return null;
  const payload = token.split(".")[1];
  if (!payload) return null;
  try {
    return JSON.parse(Buffer.from(payload, "base64url").toString("utf8"));
  } catch {
    return null;
  }
}

function accountIdFromTokens(tokens) {
  if (typeof tokens?.accountId === "string" && tokens.accountId)
    return tokens.accountId;
  for (const token of [tokens?.idToken, tokens?.accessToken]) {
    const claims = decodeJwtPayload(token);
    const auth = claims?.["https://api.openai.com/auth"];
    const accountId = auth?.chatgpt_account_id ?? claims?.chatgpt_account_id;
    if (typeof accountId === "string" && accountId) return accountId;
  }
  return null;
}

function tokenExpiresAt(token) {
  const expiration = decodeJwtPayload(token)?.exp;
  return typeof expiration === "number" ? expiration * 1000 : null;
}

function validCredentials(value) {
  if (!value || typeof value !== "object") return null;
  const credentials = {
    accessToken: value.accessToken,
    refreshToken: value.refreshToken,
    idToken: value.idToken,
    accountId: accountIdFromTokens(value),
    refreshedAt: value.refreshedAt,
  };
  if (
    typeof credentials.accessToken !== "string" ||
    !credentials.accessToken ||
    typeof credentials.refreshToken !== "string" ||
    !credentials.refreshToken ||
    typeof credentials.idToken !== "string" ||
    !credentials.idToken ||
    typeof credentials.accountId !== "string" ||
    !credentials.accountId
  ) {
    return null;
  }
  if (typeof credentials.refreshedAt !== "number") credentials.refreshedAt = 0;
  return credentials;
}

function buildAuthorizeUrl({ redirectUri, challenge, state }) {
  const url = new URL("/oauth/authorize", AUTH_ISSUER);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("client_id", OAUTH_APPLICATION_ID);
  url.searchParams.set("redirect_uri", redirectUri);
  url.searchParams.set(
    "scope",
    "openid profile email offline_access api.connectors.read api.connectors.invoke",
  );
  url.searchParams.set("code_challenge", challenge);
  url.searchParams.set("code_challenge_method", "S256");
  url.searchParams.set("id_token_add_organizations", "true");
  url.searchParams.set("codex_cli_simplified_flow", "true");
  url.searchParams.set("state", state);
  url.searchParams.set("originator", "open_swe_desktop");
  return url.toString();
}

async function listen(server, ports = CALLBACK_PORTS) {
  let lastError;
  for (const port of ports) {
    try {
      await new Promise<void>((resolve, reject) => {
        const onError = (error) => {
          server.removeListener("listening", onListening);
          reject(error);
        };
        const onListening = () => {
          server.removeListener("error", onError);
          resolve();
        };
        server.once("error", onError);
        server.once("listening", onListening);
        server.listen(port, "127.0.0.1");
      });
      return server.address().port;
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError || new Error("Could not open a local sign-in listener");
}

async function beginOpenAiLogin(options: any = {}) {
  const verifier = randomBytes(32).toString("base64url");
  const challenge = createHash("sha256").update(verifier).digest("base64url");
  const state = randomBytes(32).toString("base64url");
  let resolveResult;
  let rejectResult;
  let finished = false;
  const result = new Promise((resolve, reject) => {
    resolveResult = resolve;
    rejectResult = reject;
  });

  let timer = null;
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, "http://localhost");
    if (url.pathname !== "/auth/callback") {
      response.writeHead(404).end();
      return;
    }
    const code = url.searchParams.get("code");
    const returnedState = url.searchParams.get("state");
    const providerError =
      url.searchParams.get("error_description") ||
      url.searchParams.get("error");
    const valid = Boolean(code && returnedState === state && !providerError);
    response.writeHead(200, {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-store",
    });
    response.end(valid ? SIGNED_IN_PAGE : FAILED_PAGE);
    if (providerError) finish(new Error(`Sign-in failed: ${providerError}`));
    else if (returnedState !== state)
      finish(new Error("Sign-in state did not match"));
    else if (!code) finish(new Error("Sign-in returned no authorization code"));
    else finish(null, { code, verifier, redirectUri });
  });

  function finish(error, value?) {
    if (finished) return;
    finished = true;
    if (timer) clearTimeout(timer);
    timer = null;
    server.closeAllConnections();
    server.close();
    if (error) rejectResult(error);
    else resolveResult(value);
  }

  const port = await listen(server, options.ports);
  const redirectUri = `http://localhost:${port}/auth/callback`;
  timer = setTimeout(
    () => finish(new Error("Sign-in timed out")),
    LOGIN_TIMEOUT_MS,
  );
  timer.unref();

  return {
    url: buildAuthorizeUrl({ redirectUri, challenge, state }),
    port,
    state,
    verifier,
    result,
    cancel: () => finish(new Error("Sign-in was canceled")),
  };
}

function bearerMatches(request, secret) {
  const value = request.headers.authorization;
  const expected = `Bearer ${secret}`;
  if (typeof value !== "string" || value.length !== expected.length)
    return false;
  return timingSafeEqual(Buffer.from(value), Buffer.from(expected));
}

class OpenAiOAuthManager {
  options: any;
  fetch: any;
  now: () => number;
  credentials: any;
  credentialsSource: "app" | "shared" | null;
  loginFlow: any;
  refreshing: Promise<any> | null;
  broker: any;
  brokerUrl: string | null;
  brokerSecret: string;

  constructor(options) {
    this.options = options;
    this.fetch = options.fetchImpl || fetch;
    this.now = options.now || Date.now;
    const sharedCredentials = this.readSharedCredentials();
    this.credentials =
      sharedCredentials && !this.needsRefresh(sharedCredentials)
        ? sharedCredentials
        : null;
    this.credentialsSource = this.credentials ? "shared" : null;
    this.loginFlow = null;
    this.refreshing = null;
    this.broker = null;
    this.brokerUrl = null;
    this.brokerSecret = randomBytes(32).toString("base64url");
  }

  readSharedCredentials() {
    if (!this.options.sharedCredentialsPath) return null;
    try {
      const value = JSON.parse(
        fs.readFileSync(this.options.sharedCredentialsPath, "utf8"),
      );
      const tokens = value?.tokens;
      return validCredentials({
        accessToken: tokens?.access_token,
        refreshToken: tokens?.refresh_token,
        idToken: tokens?.id_token,
        accountId: tokens?.account_id,
        refreshedAt: Date.parse(value?.last_refresh || "") || 0,
      });
    } catch {
      return null;
    }
  }

  saveCredentials(value) {
    const credentials = validCredentials(value);
    if (!credentials)
      throw new Error(
        "The sign-in response did not include usable credentials",
      );
    this.credentials = credentials;
    this.credentialsSource = "app";
    return credentials;
  }

  clearCredentials() {
    this.credentials = null;
    this.credentialsSource = null;
  }

  syncSharedCredentials() {
    if (this.credentialsSource === "app") return;
    this.credentials = this.readSharedCredentials();
    this.credentialsSource = this.credentials ? "shared" : null;
  }

  status() {
    this.syncSharedCredentials();
    return {
      signedIn: Boolean(
        this.credentials &&
        (this.credentialsSource !== "shared" ||
          !this.needsRefresh(this.credentials)),
      ),
    };
  }

  backendEnv() {
    if (!this.brokerUrl) return {};
    return {
      OPEN_SWE_OPENAI_OAUTH_BROKER_URL: this.brokerUrl,
      OPEN_SWE_OPENAI_OAUTH_BROKER_TOKEN: this.brokerSecret,
    };
  }

  async exchangeCode({ code, verifier, redirectUri }) {
    const body = new URLSearchParams({
      grant_type: "authorization_code",
      code,
      redirect_uri: redirectUri,
      client_id: OAUTH_APPLICATION_ID,
      code_verifier: verifier,
    });
    const response = await this.fetch(TOKEN_URL, {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body,
    });
    if (!response.ok)
      throw new Error(`The sign-in token exchange failed (${response.status})`);
    const payload = await response.json();
    return this.saveCredentials({
      accessToken: payload.access_token,
      refreshToken: payload.refresh_token,
      idToken: payload.id_token,
      refreshedAt: this.now(),
    });
  }

  async login(openExternal, options: any = {}) {
    this.loginFlow?.cancel();
    const flow = await beginOpenAiLogin(options);
    this.loginFlow = flow;
    try {
      await openExternal(flow.url);
      const callback: any = await flow.result;
      await this.exchangeCode(callback);
      await this.startBroker();
      return this.status();
    } finally {
      if (this.loginFlow === flow) this.loginFlow = null;
    }
  }

  needsRefresh(credentials) {
    const expiresAt = tokenExpiresAt(credentials.accessToken);
    if (expiresAt !== null) return expiresAt <= this.now() + REFRESH_WINDOW_MS;
    return credentials.refreshedAt <= this.now() - FALLBACK_REFRESH_AGE_MS;
  }

  async refresh() {
    const current = this.credentials;
    if (!current) throw new Error("Sign in to use OpenAI models");
    const response = await this.fetch(TOKEN_URL, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        client_id: OAUTH_APPLICATION_ID,
        grant_type: "refresh_token",
        refresh_token: current.refreshToken,
      }),
    });
    if (!response.ok) {
      if (response.status === 400 || response.status === 401)
        this.clearCredentials();
      throw new Error(
        `The OpenAI session could not be refreshed (${response.status})`,
      );
    }
    const payload = await response.json();
    const accessToken = payload.access_token;
    if (typeof accessToken !== "string" || !accessToken) {
      throw new Error("The OpenAI refresh response included no access token");
    }
    const idToken = payload.id_token || current.idToken;
    const refreshedAccountId = accountIdFromTokens({ idToken, accessToken });
    if (refreshedAccountId && refreshedAccountId !== current.accountId) {
      this.clearCredentials();
      throw new Error(
        "The OpenAI account changed during token refresh; sign in again",
      );
    }
    return this.saveCredentials({
      accessToken,
      refreshToken: payload.refresh_token || current.refreshToken,
      idToken,
      accountId: current.accountId,
      refreshedAt: this.now(),
    });
  }

  async accessToken() {
    this.syncSharedCredentials();
    if (!this.credentials) throw new Error("Sign in to use OpenAI models");
    if (this.credentialsSource === "shared") {
      if (this.needsRefresh(this.credentials)) {
        throw new Error("Your local OpenAI session has expired; sign in again");
      }
      return this.credentials.accessToken;
    }
    if (this.needsRefresh(this.credentials)) {
      if (!this.refreshing) {
        this.refreshing = this.refresh().finally(() => {
          this.refreshing = null;
        });
      }
      await this.refreshing;
    }
    return this.credentials.accessToken;
  }

  async startBroker() {
    if (this.broker) return this.backendEnv();
    const server = http.createServer(async (request, response) => {
      if (request.method !== "GET" || request.url !== "/token") {
        response.writeHead(404).end();
        return;
      }
      if (!bearerMatches(request, this.brokerSecret)) {
        response.writeHead(401, { "cache-control": "no-store" }).end();
        return;
      }
      try {
        const accessToken = await this.accessToken();
        const accountId = this.credentials?.accountId;
        if (!accountId)
          throw new Error("The OpenAI session included no account ID");
        response.writeHead(200, {
          "content-type": "application/json",
          "cache-control": "no-store",
        });
        response.end(
          JSON.stringify({ access_token: accessToken, account_id: accountId }),
        );
      } catch (error) {
        response.writeHead(401, {
          "content-type": "application/json",
          "cache-control": "no-store",
        });
        response.end(JSON.stringify({ error: error.message }));
      }
    });
    const port = await listen(server, [0]);
    this.broker = server;
    this.brokerUrl = `http://127.0.0.1:${port}/token`;
    return this.backendEnv();
  }

  async close() {
    this.loginFlow?.cancel();
    this.loginFlow = null;
    const server = this.broker;
    this.broker = null;
    this.brokerUrl = null;
    if (!server) return;
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  }
}

module.exports = {
  API_BASE_URL,
  AUTH_ISSUER,
  OAUTH_APPLICATION_ID,
  OpenAiOAuthManager,
  accountIdFromTokens,
  beginOpenAiLogin,
  buildAuthorizeUrl,
  decodeJwtPayload,
};
