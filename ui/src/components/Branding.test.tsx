// @vitest-environment jsdom

import { act, cleanup, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, expect, it, vi } from "vitest"
import { Favicon, LogoMark } from "./Branding"

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it("keeps the favicon and logos in sync when runtime branding arrives and changes", async () => {
  let respond!: (response: Response) => void
  const fetch = vi.fn().mockReturnValue(
    new Promise<Response>((resolve) => {
      respond = resolve
    })
  )
  vi.stubGlobal("fetch", fetch)
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <Favicon />
      <LogoMark alt="Open SWE" className="size-14 grayscale" />
    </QueryClientProvider>
  )
  const logo = screen.getByRole("img", { name: "Open SWE" })
  const favicon = () => document.head.querySelector('link[rel="icon"]')
  expect(logo.getAttribute("src")).toBe("/logo-mark.png")
  expect(favicon()?.getAttribute("href")).toBe("/favicon.png")

  await act(async () => {
    respond(Response.json({ environment: "preview" }))
  })
  await waitFor(() => {
    expect(logo.getAttribute("src")).toBe("/logo-mark-preview.png")
    expect(favicon()?.getAttribute("href")).toBe("/favicon-preview.png")
    expect(logo.className).toContain("grayscale-0")
  })

  fetch.mockResolvedValue(Response.json({ environment: "production" }))
  await act(async () => {
    await client.invalidateQueries({ queryKey: ["branding"] })
  })
  await waitFor(() => {
    expect(logo.getAttribute("src")).toBe("/logo-mark.png")
    expect(favicon()?.getAttribute("href")).toBe("/favicon.png")
    expect(document.head.querySelectorAll('link[rel="icon"]')).toHaveLength(1)
    expect(logo.className).toBe("size-14 grayscale")
  })
  client.clear()
})
