/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { api, type RepositorySettings } from "@/lib/api"
import { WorkspaceRepositoriesSection } from "./WorkspaceRepositoriesSection"

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

it("keeps an earlier row locked while a later row's save is also pending", async () => {
  const rows: Array<RepositorySettings> = [
    { repo: "acme/api", may_start_threads: false },
    { repo: "acme/web", may_start_threads: false },
  ]
  vi.spyOn(api, "listWorkspaceRepositories").mockResolvedValue(rows)
  vi.spyOn(api, "configureWorkspaceRepository").mockReturnValue(
    new Promise<RepositorySettings>(() => {})
  )
  render(
    <QueryClientProvider client={new QueryClient()}>
      <WorkspaceRepositoriesSection slug="oss" canEdit />
    </QueryClientProvider>
  )
  const switchFor = async (repo: string) =>
    (await screen.findAllByRole("switch")).find(
      (element) =>
        element.getAttribute("aria-label") === `Let ${repo} start threads`
    )!
  const apiSwitch = await switchFor("acme/api")
  const webSwitch = await switchFor("acme/web")

  fireEvent.click(apiSwitch)
  fireEvent.click(webSwitch)

  await vi.waitFor(() =>
    expect(webSwitch.hasAttribute("data-disabled")).toBe(true)
  )
  expect(apiSwitch.hasAttribute("data-disabled")).toBe(true)
  fireEvent.click(apiSwitch)
  expect(api.configureWorkspaceRepository).toHaveBeenCalledTimes(2)
})
