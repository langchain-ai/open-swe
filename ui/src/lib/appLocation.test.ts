/** @vitest-environment jsdom */

import { beforeEach, describe, expect, it } from "vitest"

import {
  getLastAppLocation,
  getLastSectionLocation,
  rememberAppLocation,
} from "./appLocation"

beforeEach(() => {
  window.sessionStorage.clear()
})

describe("app location", () => {
  it.each([
    "/agents/thread-1?view=diff#latest",
    "/assistant",
    "/assistant?noRepo=true",
    "/assistant/thread-1",
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
    "/assistant-external",
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

describe("section location", () => {
  it("restores the last location within each section", () => {
    rememberAppLocation("/agents/reviews/acme/api/42")
    rememberAppLocation("/incidents?view=inactive")
    rememberAppLocation("/agents/thread-1")

    expect(getLastSectionLocation("/agents/reviews")).toBe(
      "/agents/reviews/acme/api/42"
    )
    expect(getLastSectionLocation("/incidents")).toBe(
      "/incidents?view=inactive"
    )
  })

  it("defaults to the section root", () => {
    rememberAppLocation("/agents/reviews-external")

    expect(getLastSectionLocation("/agents/reviews")).toBe("/agents/reviews")
  })
})
