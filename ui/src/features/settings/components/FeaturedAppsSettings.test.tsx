/** @vitest-environment jsdom */

import type { PluginPermission } from "@openswe/plugin-sdk"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import type { App } from "@/features/plugins/lib/api"
import type { ScopedSettings } from "@/features/settings/lib/settingsScope"
import type { FeaturedApp } from "@/lib/api"
import { FeaturedAppsSettings } from "./FeaturedAppsSettings"

const apps = vi.hoisted(() => ({ data: [] as Array<App> }))
vi.mock("@/features/plugins/lib/queries", () => ({
  useApps: () => ({ data: apps.data, isPending: false }),
  // The approved release, as the app's newest but one when there is one.
  useAppVersion: (id: string | undefined) => ({
    data: apps.data
      .flatMap((app) => (app.latest ? [app.latest] : []))
      .map((latest) => ({
        ...latest,
        id: id ?? "",
        manifest: { ...latest.manifest, permissions: ["storage"] },
      }))[0],
  }),
}))

afterEach(cleanup)

const app = (
  id: string,
  name: string,
  listed: boolean,
  number = 1,
  permissions: Array<PluginPermission> = ["storage"]
): App => ({
  id,
  name,
  mine: false,
  listed,
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
      description: `${name} does things.`,
      sidebar: { label: name, icon: "puzzle-piece" },
      permissions,
    },
  },
})

const scoped = (
  featured: Array<FeaturedApp>,
  save = vi.fn()
): ScopedSettings => ({
  scope: { kind: "instance" },
  data: { featured_apps: featured } as ScopedSettings["data"],
  saved: undefined,
  isPending: false,
  inherits: () => false,
  save,
  reset: vi.fn(),
})

it("adds a listed app for everyone at its newest release, and offers no unlisted one", () => {
  apps.data = [app("a", "design-qa", true), app("b", "draft", false)]
  const save = vi.fn()
  render(<FeaturedAppsSettings settings={scoped([], save)} editable />)

  expect(screen.queryByText("DRAFT")).toBeNull()
  fireEvent.click(
    screen.getByRole("switch", { name: "Add design-qa for everyone" })
  )
  // Nothing is granted until the admin approves its permissions for everyone.
  expect(save).not.toHaveBeenCalled()
  expect(screen.getByText("Add DESIGN-QA for everyone?")).toBeTruthy()
  fireEvent.click(screen.getByRole("button", { name: "Add for everyone" }))
  expect(save).toHaveBeenCalledWith({
    featured_apps: [{ app: "a", release: "a-1", repositories: [] }],
  })
})

it("updates a release asking for nothing more without asking", () => {
  apps.data = [app("a", "design-qa", true, 2)]
  const save = vi.fn()
  render(
    <FeaturedAppsSettings
      settings={scoped(
        [{ app: "a", release: "a-1", repositories: ["acme/web"] }],
        save
      )}
      editable
    />
  )

  expect(screen.getByText(/Release 2 is out/)).toBeTruthy()
  fireEvent.click(screen.getByRole("button", { name: "Update" }))
  expect(save).toHaveBeenCalledWith({
    featured_apps: [{ app: "a", release: "a-2", repositories: ["acme/web"] }],
  })
})

it("saves the repositories a featured app reads for everyone", () => {
  apps.data = [app("a", "design-qa", true)]
  const save = vi.fn()
  render(
    <FeaturedAppsSettings
      settings={scoped([{ app: "a", release: "a-1", repositories: [] }], save)}
      editable
    />
  )

  fireEvent.change(
    screen.getByLabelText("Repositories design-qa reads for everyone"),
    { target: { value: "acme/web, acme/api" } }
  )
  fireEvent.click(screen.getByRole("button", { name: "Save repositories" }))
  expect(save).toHaveBeenCalledWith({
    featured_apps: [
      { app: "a", release: "a-1", repositories: ["acme/web", "acme/api"] },
    ],
  })
})

it("lets an admin turn off an app they can no longer see", () => {
  apps.data = []
  const save = vi.fn()
  render(
    <FeaturedAppsSettings
      settings={scoped([{ app: "gone", release: "gone-1" }], save)}
      editable
    />
  )

  fireEvent.click(
    screen.getByRole("switch", { name: "Add app gone for everyone" })
  )
  expect(save).toHaveBeenCalledWith({ featured_apps: [] })
})

it("asks the admin to approve what a newer release adds before everyone gets it", () => {
  apps.data = [app("a", "design-qa", true, 2, ["storage", "agent"])]
  const save = vi.fn()
  render(
    <FeaturedAppsSettings
      settings={scoped([{ app: "a", release: "a-1", repositories: [] }], save)}
      editable
    />
  )

  fireEvent.click(screen.getByRole("button", { name: "Update" }))
  expect(save).not.toHaveBeenCalled()
  expect(screen.getByText("Give everyone release 2?")).toBeTruthy()
  fireEvent.click(screen.getByRole("button", { name: "Approve and update" }))
  expect(save).toHaveBeenCalledWith({
    featured_apps: [{ app: "a", release: "a-2", repositories: [] }],
  })
})
