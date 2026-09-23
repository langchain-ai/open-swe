import { afterEach, describe, expect, test } from "bun:test"
import { mkdtemp, mkdir, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"

import { readBackend, readConfig } from "../src/config.ts"

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

describe("readConfig", () => {
  test("takes the session from the environment with no stored login", async () => {
    process.env["HOME"] = await mkdtemp(join(tmpdir(), "open-swe-home-"))
    process.env["OPEN_SWE_BACKEND_URL"] = "http://127.0.0.1:2027"
    process.env["OPEN_SWE_SESSION"] = "jwt-from-env"

    expect(await readConfig()).toEqual({
      backend: "http://127.0.0.1:2027",
      session: "jwt-from-env",
    })
  })

  test("is unauthenticated when only a backend is known", async () => {
    process.env["HOME"] = await mkdtemp(join(tmpdir(), "open-swe-home-"))
    process.env["OPEN_SWE_BACKEND_URL"] = "http://127.0.0.1:2027"
    delete process.env["OPEN_SWE_SESSION"]

    expect(await readConfig()).toBeNull()
  })
})
