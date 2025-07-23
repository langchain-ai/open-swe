import "dotenv/config";
import express from "express";
import type { Request, Response } from "express";
import fs from "fs";
import os from "os";
import path from "path";

const CLIENT_ID = process.env.GITHUB_CLIENT_ID || "";
const CLIENT_SECRET = process.env.GITHUB_CLIENT_SECRET || "";
const PORT = 3000;
const CALLBACK_URL = "http://localhost:3000/api/auth/github/callback";

const TOKEN_PATH = path.join(
  os.homedir(),
  ".open-swe-cli",
  "github_token.json",
);

let accessToken: string | null = null;

interface GitHubTokenResponse {
  access_token?: string;
  token_type?: string;
  scope?: string;
  error?: string;
  error_description?: string;
  error_uri?: string;
}

function saveToken(tokenData: GitHubTokenResponse) {
  const dir = path.dirname(TOKEN_PATH);
  try {
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
    }
    fs.writeFileSync(TOKEN_PATH, JSON.stringify(tokenData, null, 2), {
      mode: 0o600,
    });
  } catch (err) {
    console.error("Failed to save token:", err);
  }
}

function loadToken() {
  try {
    if (fs.existsSync(TOKEN_PATH)) {
      return JSON.parse(fs.readFileSync(TOKEN_PATH, "utf8"));
    }
  } catch (err) {
    console.error("Failed to load token:", err);
  }
  return null;
}

const app = express();

// 1. Start OAuth flow
app.get("/api/auth/github/login", (_req: Request, res: Response) => {
  const state = Math.random().toString(36).substring(2);
  const baseGithubAuthUrl = "https://github.com/login/oauth/authorize";
  const url = new URL(baseGithubAuthUrl);
  url.searchParams.set("client_id", CLIENT_ID);
  url.searchParams.set("redirect_uri", CALLBACK_URL);
  url.searchParams.set("state", state);
  return res.redirect(url.toString());
});

// 2. Handle OAuth callback
app.get("/api/auth/github/callback", async (req: Request, res: Response) => {
  const code = req.query.code as string;
  // Optionally validate state here
  if (!code) {
    return res.status(400).send("Missing code parameter");
  }
  const GITHUB_ACCESS_TOKEN_LOGIN_LINK =
    process.env.GITHUB_ACCESS_TOKEN_LOGIN_LINK ||
    "https://github.com/login/oauth/access_token";
  // Exchange code for access token
  const tokenRes = await fetch(GITHUB_ACCESS_TOKEN_LOGIN_LINK, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({
      client_id: CLIENT_ID,
      client_secret: CLIENT_SECRET,
      code,
      redirect_uri: CALLBACK_URL,
    }),
  });
  const tokenData = await tokenRes.json();
  if (tokenData.error) {
    return res
      .status(400)
      .send("Error exchanging code for token: " + tokenData.error);
  }
  accessToken = tokenData.access_token;
  // Store the token in a config file
  try {
    saveToken(tokenData);
  } catch (err) {
    console.error("Failed to store token in config file:", err);
  }
  return res.send("Authentication successful! You can close this window.");
});

export function startAuthServer() {
  app.listen(PORT, () => {});
}

export function getAccessToken() {
  // Try to get from memory first, then from config file
  if (accessToken) return accessToken;
  const tokenData = loadToken();
  return tokenData ? tokenData.access_token : null;
}
