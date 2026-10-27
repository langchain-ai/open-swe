import { expect, it, vi } from "vitest"

vi.mock("@/lib/dashboard-fetch", () => ({
  dashboardApiUrl: (path: string) => `/dashboard/api${path}`,
  dashboardForwardedHeaders: () => ({}),
}))
import { evidenceMediaUrl } from "./api"

it("only loads evidence through the authenticated report media API", () => {
  const path =
    "/dashboard/api/reviews/langchain-ai/langchainplus/43396/frontend-reviews/00000000-0000-0000-0000-000000000001/media/before.png"
  expect(evidenceMediaUrl(path)).toBe(path)
  const dottedRepo = path.replace("langchainplus", "foo..bar")
  expect(evidenceMediaUrl(dottedRepo)).toBe(dottedRepo)
  expect(() => evidenceMediaUrl("https://example.com/before.png")).toThrow(
    /authenticated/
  )
  expect(() =>
    evidenceMediaUrl("/dashboard/api/reviews/a/b/1/frontend-reviews/../secrets")
  ).toThrow(/authenticated/)
  expect(() => evidenceMediaUrl(path.replace("before.png", "%2e%2e"))).toThrow(
    /authenticated/
  )
  expect(() =>
    evidenceMediaUrl(path.replace("before.png", "%2e%2e%2fsecrets"))
  ).toThrow(/authenticated/)
  expect(() =>
    evidenceMediaUrl(path.replace("before.png", "%5csecrets"))
  ).toThrow(/authenticated/)
})
