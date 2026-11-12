import { expect, it } from "vitest"

import type { FrontendReviewRun } from "./capture"
import { pageLink, scenarioLinks } from "./links"

it("keeps source-page links on their origin and rejects credential or executable URLs", () => {
  expect(
    pageLink(
      "https://preview.example.com",
      "/projects/42?tab=traces&filter=success#details"
    )
  ).toBe(
    "https://preview.example.com/projects/42?tab=traces&filter=success#details"
  )
  expect(pageLink("http://localhost:3000/", "/projects")).toBe(
    "http://localhost:3000/projects"
  )
  expect(pageLink("http://preview.example.com", "/projects")).toBeUndefined()
  expect(pageLink("javascript:alert(1)", "/projects")).toBeUndefined()
  expect(
    pageLink("https://user:password@example.com", "/projects")
  ).toBeUndefined()
  expect(
    pageLink("https://preview.example.com", "//other.example.com")
  ).toBeUndefined()
  expect(
    pageLink("https://preview.example.com", "/\\other.example.com")
  ).toBeUndefined()
})

it("links a scenario's route on production, preview and base without repeating one", () => {
  const run = {
    base_url: "https://app.example.com",
    head_url: "https://pr-7.preview.example.com",
    report: { production_url: "https://app.example.com" },
  } as FrontendReviewRun
  expect(scenarioLinks(run, "/projects/42?tab=traces")).toEqual([
    {
      label: "Production",
      href: "https://app.example.com/projects/42?tab=traces",
    },
    {
      label: "Preview",
      href: "https://pr-7.preview.example.com/projects/42?tab=traces",
    },
  ])
  expect(
    scenarioLinks({ ...run, report: null }, "/").map(({ label }) => label)
  ).toEqual(["Preview", "Base"])
})
