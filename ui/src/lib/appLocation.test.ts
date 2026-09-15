/** @vitest-environment jsdom */

import { beforeEach, describe, expect, it } from "vitest"

import { getLastAppLocation, rememberAppLocation } from "./appLocation"

beforeEach(() => {
  window.sessionStorage.clear()
})

describe("app location", () => {
  it.each([
    "/agents/thread-1?view=diff#latest",
    "/incidents",
    "/incidents?view=inactive",
    "/incidents/incident-1",
  ])("restores the last app location: %s", (href) => {
    rememberAppLocation(href)

    expect(getLastAppLocation()).toBe(href)
  })

  it.each([
    "/my-settings",
    "/incidents-external",
    "https://example.com/incidents",
  ])("ignores locations outside the app: %s", (href) => {
    rememberAppLocation("/agents/thread-1")
    rememberAppLocation(href)

    expect(getLastAppLocation()).toBe("/agents/thread-1")
  })

  it("defaults to the app home", () => {
    expect(getLastAppLocation()).toBe("/agents")
  })
})
