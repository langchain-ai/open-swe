import "dotenv/config";
import express from "express";
import type { Request, Response } from "express";
import fs from "fs";
import os from "os";
import path from "path";
import jwt from "jsonwebtoken";

const CLIENT_ID = process.env.GITHUB_APP_CLIENT_ID || "";
const CLIENT_SECRET = process.env.GITHUB_APP_CLIENT_SECRET || "";
const PORT = process.env.PORT || 3000;
const CALLBACK_URL =
  process.env.GITHUB_CALLBACK_URL ||
  `http://localhost:${PORT}/api/auth/github/callback`;

const TOKEN_PATH = path.join(
  os.homedir(),
  ".open-swe-cli",
  "github_token.json",
);

const APP_ID = process.env.GITHUB_APP_ID || "";
const GITHUB_PRIVATE_KEY = process.env.GITHUB_APP_PRIVATE_KEY || "";

let accessToken: string | null = null;
let serverStarted = false;

function saveToken(tokenData: any) {
  const dir = path.dirname(TOKEN_PATH);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  }
  fs.writeFileSync(TOKEN_PATH, JSON.stringify(tokenData, null, 2), {
    mode: 0o600,
  });
}

function loadToken() {
  if (fs.existsSync(TOKEN_PATH)) {
    return JSON.parse(fs.readFileSync(TOKEN_PATH, "utf8"));
  }
  return null;
}

const app = express();

// 1. Start OAuth flow
app.get("/api/auth/github/login", (_req: Request, res: Response) => {
  const state = Math.random().toString(36).substring(2);
  const githubAuthUrl = `https://github.com/login/oauth/authorize?client_id=${CLIENT_ID}&redirect_uri=${encodeURIComponent(CALLBACK_URL)}&state=${state}`;
  res.redirect(githubAuthUrl);
});

// 2. Handle OAuth callback
app.get("/api/auth/github/callback", async (req: Request, res: Response) => {
  const code = req.query.code as string;
  const installationId = req.query.installation_id as string | undefined;
  let tokenData: any = loadToken() || {};

  // If OAuth code is present, exchange for access token
  if (code) {
    // Exchange code for access token
    const tokenRes = await fetch(
      "https://github.com/login/oauth/access_token",
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          client_id: CLIENT_ID,
          client_secret: CLIENT_SECRET,
          code,
          redirect_uri: CALLBACK_URL,
        }),
      },
    );
    const fetchedTokenData = await tokenRes.json();
    if (fetchedTokenData.error) {
      return res
        .status(400)
        .send("Error exchanging code for token: " + fetchedTokenData.error);
    }
    accessToken = fetchedTokenData.access_token;
    tokenData.access_token = accessToken;
    // Store the token in a config file
    try {
      saveToken(tokenData);
    } catch (err) {
      console.error("Failed to store token in config file:", err);
    }
  }

  // If installation_id is present, save it
  if (installationId) {
    tokenData.installation_id = installationId;
    try {
      saveToken(tokenData);
    } catch (err) {
      console.error("Failed to store installation_id in config file:", err);
    }
    // Immediately fetch the installation access token to test the fetch and print info
    await getInstallationAccessToken();
  }

  if (code || installationId) {
    res.send("Authentication successful! You can close this window.");
  } else {
    res.status(400).send("Missing code or installation_id parameter");
  }
});

export function startAuthServer() {
  if (serverStarted) return;
  serverStarted = true;
  app.listen(PORT, () => {});
}

export function getAccessToken() {
  // Try to get from memory first, then from config file
  if (accessToken) return accessToken;
  const tokenData = loadToken();
  return tokenData ? tokenData.access_token : null;
}

export function getInstallationId() {
  const tokenData = loadToken();
  return tokenData ? tokenData.installation_id : null;
}

export async function getInstallationAccessToken(): Promise<string | null> {
  // Load installation_id from config file
  const tokenData = loadToken();
  const installationId = tokenData?.installation_id;
  if (!installationId) {
    console.error("No installation_id found in config file.");
    return null;
  }
  if (!APP_ID || !GITHUB_PRIVATE_KEY) {
    console.error("GITHUB_APP_ID or GITHUB_APP_PRIVATE_KEY not set.");
    return null;
  }
  // Use the key contents from the env var, replacing escaped newlines
  const privateKey = GITHUB_PRIVATE_KEY.replace(/\\n/g, "\n");
  const now = Math.floor(Date.now() / 1000);
  const payload = {
    iat: now - 60,
    exp: now + 10 * 60,
    iss: APP_ID,
  };
  const jwtToken = jwt.sign(payload, privateKey, { algorithm: "RS256" });
  const res = await fetch(
    `https://api.github.com/app/installations/${installationId}/access_tokens`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${jwtToken}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "open-swe-cli",
      },
    },
  );
  if (!res.ok) {
    console.error(
      "Failed to fetch installation access token:",
      await res.text(),
    );
    return null;
  }
  const data = await res.json();

  return data.token;
}
