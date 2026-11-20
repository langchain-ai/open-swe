import { describe, expect, it } from "vitest"

import { parseRoute, routePath, type Route } from "./route"

describe("the People tab's route", () => {
  it.each<[string, Route]>([
    ["/people", { page: "inbox", scope: "people", index: 0 }],
    [
      "/people/nick-kim",
      { page: "inbox", scope: "people", index: 0, person: "nick-kim" },
    ],
    [
      "/people/nick-kim/3",
      { page: "inbox", scope: "people", index: 2, person: "nick-kim" },
    ],
  ])("reads %s and writes it back", (path, route) => {
    expect(parseRoute(path)).toEqual(route)
    expect(routePath(route)).toBe(path)
  })

  it("wins over a pull request in a repository owned by people", () => {
    expect(parseRoute("/people/app/12")).toEqual({
      page: "inbox",
      scope: "people",
      index: 11,
      person: "app",
    })
  })
})
