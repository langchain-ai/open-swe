const { readFileSync } = require("node:fs")
const { homedir } = require("node:os")
const path = require("node:path")
const { spawn } = require("node:child_process")
const {
  OpenAiOAuthManager,
  decodeJwtPayload,
} = require("../desktop/build/openai-oauth.cjs")

const root = path.resolve(__dirname, "..")
const authFile =
  process.env.CHATGPT_AUTH_FILE ||
  path.join(
    process.env.CODEX_HOME || path.join(homedir(), ".codex"),
    "auth.json"
  )

class SharedSessionBroker extends OpenAiOAuthManager {
  readCredentials() {
    const { tokens } = JSON.parse(readFileSync(authFile, "utf8"))
    if (!tokens?.access_token || !tokens?.account_id)
      throw new Error(
        "No local ChatGPT OAuth session. Sign in before starting the dev server."
      )
    return { accessToken: tokens.access_token, accountId: tokens.account_id }
  }

  async accessToken() {
    // The sign-in application owns token refresh; never rotate its refresh token here.
    this.credentials = this.readCredentials()
    const expires = decodeJwtPayload(this.credentials.accessToken)?.exp
    if (!expires || expires * 1000 <= Date.now())
      throw new Error(
        "The local ChatGPT session expired. Refresh your sign-in and retry."
      )
    return this.credentials.accessToken
  }
}

async function main() {
  const broker = new SharedSessionBroker({})
  await broker.accessToken()
  const apiPort = process.env.E2E_PORT || "2024"
  const uiPort = process.env.UI_PORT || "3010"
  const apiUrl = `http://127.0.0.1:${apiPort}`
  const uiUrl = `http://127.0.0.1:${uiPort}`
  const env = {
    ...process.env,
    ...(await broker.startBroker()),
    E2E_REAL_LLM: "1",
    E2E_PORT: apiPort,
    E2E_BASE: apiUrl,
    LANGSMITH_HOST_API_URL: apiUrl,
    LANGSMITH_GATEWAY_ENABLED: "false",
    OPENAI_API_KEY: "",
    DASHBOARD_BASE_URL: uiUrl,
    DASHBOARD_ALLOWED_ORIGINS: uiUrl,
    E2E_HARNESS: apiUrl,
    VITE_DASHBOARD_API_BASE_URL: "",
    VITE_EXPERIMENTAL_ASSISTANT_UI: "true",
  }
  const children = []
  let stopping = false
  async function stop(code) {
    if (stopping) return
    stopping = true
    for (const child of children) {
      if (child.pid) {
        try {
          process.kill(-child.pid, "SIGTERM")
        } catch {}
      }
    }
    await broker.close()
    process.exitCode = code
  }
  process.once("SIGINT", () => void stop(0))
  process.once("SIGTERM", () => void stop(0))
  function start(command, args) {
    const child = spawn(command, args, {
      cwd: root,
      env,
      stdio: "inherit",
      detached: true,
    })
    children.push(child)
    child.once("error", (error) => {
      console.error(error.message)
      void stop(1)
    })
    child.once("exit", (code) => void stop(code ?? 1))
  }
  start("uv", [
    "run",
    "langgraph",
    "dev",
    "--config",
    "tests/e2e/langgraph.e2e.json",
    "--host",
    "127.0.0.1",
    "--port",
    apiPort,
    "--no-browser",
    "--allow-blocking",
    "--no-reload",
  ])
  start("pnpm", [
    "--filter",
    "open-swe-dashboard",
    "exec",
    "vite",
    "dev",
    "--host",
    "127.0.0.1",
    "--port",
    uiPort,
    "--strictPort",
  ])
  console.log(
    `ChatGPT OAuth dev server: ${uiUrl}\nReal model; GitHub and Slack use test fixtures. Ctrl+C stops both servers.`
  )
}

main().catch((error) => {
  console.error(error.message)
  process.exitCode = 1
})
