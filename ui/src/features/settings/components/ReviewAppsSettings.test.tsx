/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import type { App } from "@/features/plugins/lib/api"
import type { ScopedSettings } from "@/features/settings/lib/settingsScope"
import type { ReviewAppRelease } from "@/lib/api"
import { ReviewAppsSettings, ReviewBrowserSettings } from "./ReviewAppsSettings"

const apps = vi.hoisted(() => ({ data: [] as Array<App> }))
vi.mock("@/features/plugins/lib/queries", () => ({
  useApps: () => ({ data: apps.data, isPending: false }),
}))

afterEach(cleanup)

const app = (
  id: string,
  name: string,
  browser: boolean | null,
  number = 1
): App => ({
  id,
  name,
  mine: false,
  listed: true,
  latest: {
    id: `${id}-${number}`,
    number,
    origin: {},
    preview: false,
    created_at: null,
    manifest: {
      name,
      displayName: name.toUpperCase(),
      version: "1",
      description: "",
      sidebar: { label: name, icon: "puzzle-piece" },
      permissions: ["storage"],
      review:
        browser === null
          ? null
          : { instructions: "Capture.", writes: ["captures"], browser },
    },
  },
})

const scoped = (
  reviewApps: Array<ReviewAppRelease>,
  save = vi.fn()
): ScopedSettings => ({
  scope: { kind: "instance" },
  data: { review_apps: reviewApps } as ScopedSettings["data"],
  saved: undefined,
  isPending: false,
  inherits: () => false,
  save,
  reset: vi.fn(),
})

it("offers only apps with a review step and saves the list reviews work for", () => {
  apps.data = [app("a", "design-qa", true), app("b", "docs", null)]
  const save = vi.fn()
  render(<ReviewAppsSettings settings={scoped([], save)} editable />)

  expect(screen.getByText("DESIGN-QA")).toBeTruthy()
  expect(screen.queryByText("DOCS")).toBeNull()
  expect(
    screen.getByText(
      "In each review, browses the pull request's preview and saves to captures."
    )
  ).toBeTruthy()

  fireEvent.click(
    screen.getByRole("switch", { name: "Reviews work for design-qa" })
  )
  expect(save).toHaveBeenCalledWith({
    review_apps: [{ app: "a", release: "a-1" }],
  })
})

it("keeps reviews on the approved release until an admin updates it", () => {
  apps.data = [app("a", "design-qa", true, 2)]
  const save = vi.fn()
  render(
    <ReviewAppsSettings
      settings={scoped([{ app: "a", release: "a-1" }], save)}
      editable
    />
  )

  expect(
    screen.getByText(/Release 2 is out; update to follow it\./)
  ).toBeTruthy()
  fireEvent.click(screen.getByRole("button", { name: "Update" }))
  expect(save).toHaveBeenCalledWith({
    review_apps: [{ app: "a", release: "a-2" }],
  })
})

it("lets an admin turn off an app they can no longer see", () => {
  apps.data = []
  const save = vi.fn()
  render(
    <ReviewAppsSettings
      settings={scoped([{ app: "gone", release: "gone-1" }], save)}
      editable
    />
  )

  fireEvent.click(
    screen.getByRole("switch", { name: "Reviews work for app gone" })
  )
  expect(save).toHaveBeenCalledWith({ review_apps: [] })
})

it("saves where the review browser signs in, and the tool that signs it in", () => {
  const save = vi.fn()
  render(<ReviewBrowserSettings settings={scoped([], save)} editable />)

  fireEvent.change(screen.getByLabelText("Sign-in domains"), {
    target: { value: "review.example.com, dev.example.com" },
  })
  fireEvent.change(screen.getByLabelText("Session tool"), {
    target: { value: " homebase/sign_in " },
  })
  fireEvent.click(screen.getByRole("button", { name: "Save" }))

  expect(save).toHaveBeenCalledWith({
    review_browser_domains: ["review.example.com", "dev.example.com"],
    review_browser_session_tool: "homebase/sign_in",
  })
})
