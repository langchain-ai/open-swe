import { afterEach, describe, expect, test } from "bun:test"
import { mkdtemp, mkdir, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"

import { readBackend, readConfig } from "../src/config.ts"
import {
  ApiKeyCredential,
  GitHubActionsCredential,
  SessionCredential,
} from "../src/credentials.ts"

const SAVED = { ...process.env }

afterEach(() => {
  for (const key of Object.keys(process.env)) {
    if (!(key in SAVED)) delete process.env[key]
  }
  Object.assign(process.env, SAVED)
})

describe("readBackend", () => {
  test("prefers the environment, under the desktop app's own names", async () => {
    process.env["OPEN_SWE_BACKEND_URL"] = "http://from-backend-url:2027"
    process.env["OPEN_SWE_DESKTOP_URL"] = "http://from-desktop-url:2028"

    expect(await readBackend()).toBe("http://from-backend-url:2027")

    delete process.env["OPEN_SWE_BACKEND_URL"]
    expect(await readBackend()).toBe("http://from-desktop-url:2028")
  })

  test("falls back to the development backend the desktop app uses", async () => {
    delete process.env["OPEN_SWE_BACKEND_URL"]
    delete process.env["OPEN_SWE_DESKTOP_URL"]
    process.env["HOME"] = await mkdtemp(join(tmpdir(), "open-swe-home-"))

    expect(await readBackend()).toBe("http://localhost:2024")
  })

  test("reads the backend the desktop app was pointed at", async () => {
    delete process.env["OPEN_SWE_BACKEND_URL"]
    delete process.env["OPEN_SWE_DESKTOP_URL"]
    const home = await mkdtemp(join(tmpdir(), "open-swe-home-"))
    const support = join(home, "Library", "Application Support", "Open SWE")
    await mkdir(support, { recursive: true })
    await writeFile(
      join(support, "desktop-config.json"),
      JSON.stringify({ backendUrl: "https://desktop.example.com/" })
    )
    process.env["HOME"] = home

    expect(await readBackend()).toBe(
      process.platform === "darwin"
        ? "https://desktop.example.com/"
        : "http://localhost:2024"
    )
  })
})

const CREDENTIAL_VARS = [
  "OPEN_SWE_API_KEY",
  "OPEN_SWE_SESSION",
  "OPEN_SWE_OIDC_AUDIENCE",
  "ACTIONS_ID_TOKEN_REQUEST_URL",
  "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
]

async function isolated(): Promise<void> {
  for (const name of CREDENTIAL_VARS) delete process.env[name]
  process.env["HOME"] = await mkdtemp(join(tmpdir(), "open-swe-home-"))
  process.env["OPEN_SWE_BACKEND_URL"] = "http://127.0.0.1:2027/"
}

describe("readConfig", () => {
  test("takes the session from the environment with no stored login", async () => {
    await isolated()
    process.env["OPEN_SWE_SESSION"] = "jwt-from-env"

    const config = await readConfig()
    expect(config?.backend).toBe("http://127.0.0.1:2027")
    expect(config?.credential).toBeInstanceOf(SessionCredential)
    expect(await config?.credential.headers("http://127.0.0.1:2027")).toEqual({
      Cookie: "osw_session=jwt-from-env",
      Origin: "http://127.0.0.1:2027",
    })
  })

  test("prefers an API key, which is a machine", async () => {
    await isolated()
    process.env["OPEN_SWE_SESSION"] = "jwt-from-env"
    process.env["OPEN_SWE_API_KEY"] = "osk_abc"

    const credential = (await readConfig())?.credential
    expect(credential).toBeInstanceOf(ApiKeyCredential)
    expect(credential?.machine).toBe(true)
    expect(await credential?.headers("http://127.0.0.1:2027")).toEqual({
      Authorization: "Bearer osk_abc",
    })
  })

  test("uses the GitHub Actions job's identity when the job can mint one", async () => {
    await isolated()
    process.env["OPEN_SWE_SESSION"] = "jwt-from-env"
    process.env["ACTIONS_ID_TOKEN_REQUEST_URL"] = "http://127.0.0.1:1/token"
    process.env["ACTIONS_ID_TOKEN_REQUEST_TOKEN"] = "request-token"

    const credential = (await readConfig())?.credential
    expect(credential).toBeInstanceOf(GitHubActionsCredential)
    expect(credential?.machine).toBe(true)
  })

  test("is unauthenticated when only a backend is known", async () => {
    await isolated()

    expect(await readConfig()).toBeNull()
  })
})

describe("GitHubActionsCredential", () => {
  function jwt(exp: number): string {
    const payload = Buffer.from(JSON.stringify({ exp })).toString("base64url")
    return `header.${payload}.signature`
  }

  test("asks for the backend's audience and refetches a token about to expire", async () => {
    const asked: { audience: string | null; authorization: string | null }[] =
      []
    const expiries = [
      Math.floor(Date.now() / 1_000) + 30,
      Math.floor(Date.now() / 1_000) + 3_600,
    ]
    const server = Bun.serve({
      port: 0,
      fetch(request) {
        const url = new URL(request.url)
        asked.push({
          audience: url.searchParams.get("audience"),
          authorization: request.headers.get("authorization"),
        })
        return Response.json({ value: jwt(expiries[asked.length - 1] ?? 0) })
      },
    })
    try {
      const credential = new GitHubActionsCredential(
        `http://127.0.0.1:${server.port}/token?api-version=2.0`,
        "request-token",
        "https://open-swe.example.com"
      )
      const first = await credential.headers()
      const second = await credential.headers()
      const third = await credential.headers()

      expect(first).toEqual({
        Authorization: `Bearer ${jwt(expiries[0] ?? 0)}`,
      })
      expect(second).toEqual({
        Authorization: `Bearer ${jwt(expiries[1] ?? 0)}`,
      })
      expect(third).toEqual(second)
      expect(asked).toEqual([
        {
          audience: "https://open-swe.example.com",
          authorization: "Bearer request-token",
        },
        {
          audience: "https://open-swe.example.com",
          authorization: "Bearer request-token",
        },
      ])
    } finally {
      await server.stop(true)
    }
  })
})
