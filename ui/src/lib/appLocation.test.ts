/** @vitest-environment jsdom */

import { beforeEach, describe, expect, it } from "vitest"

import { getLastAppLocation, rememberAppLocation } from "./appLocation"

beforeEach(() => {
  window.sessionStorage.clear()
})

describe("app location", () => {
  it("restores the last thread location", () => {
    rememberAppLocation("/agents/thread-1?view=diff#latest")

    expect(getLastAppLocation()).toBe("/agents/thread-1?view=diff#latest")
  })

  it("ignores locations outside the app", () => {
    rememberAppLocation("/agents/thread-1")
    rememberAppLocation("/my-settings")

    expect(getLastAppLocation()).toBe("/agents/thread-1")
  })

  it("defaults to the app home", () => {
    expect(getLastAppLocation()).toBe("/agents")
  })
})
