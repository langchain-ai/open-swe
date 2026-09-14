/** @vitest-environment jsdom */

import { describe, expect, it } from "vitest"

import { redactStorage, redactUrl } from "./debugSnapshot"

describe("debug snapshot redaction", () => {
  it("redacts credential-shaped query parameters and keeps the rest", () => {
    expect(
      redactUrl("https://openswe.dev/login?code=abc123&state=xyz&next=/agents")
    ).toBe(
      "https://openswe.dev/login?code=%5Bredacted%5D&state=%5Bredacted%5D&next=%2Fagents"
    )
    expect(redactUrl("https://openswe.dev/agents?page=1")).toBe(
      "https://openswe.dev/agents?page=1"
    )
  })

  it("keeps relative request urls relative", () => {
    expect(redactUrl("/dashboard/api/threads?access_token=secret")).toBe(
      "/dashboard/api/threads?access_token=%5Bredacted%5D"
    )
  })

  it("redacts secret-looking storage keys and truncates the rest", () => {
    const storage = new Map([
      ["open-swe-theme", "dark"],
      ["open-swe.github-token", "ghp_realtoken"],
      ["open-swe.notes", "n".repeat(400)],
    ])
    const keys = [...storage.keys()]
    const fake = {
      length: keys.length,
      key: (index: number) => keys[index] ?? null,
      getItem: (key: string) => storage.get(key) ?? null,
    } as Storage

    const redacted = redactStorage(fake)
    expect(redacted["open-swe-theme"]).toBe("dark")
    expect(redacted["open-swe.github-token"]).toBe("[redacted]")
    expect(redacted["open-swe.notes"]).toBe(`${"n".repeat(200)}…(400)`)
  })
})
