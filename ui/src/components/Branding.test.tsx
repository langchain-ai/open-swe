// @vitest-environment jsdom

import { act, cleanup, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, expect, it, vi } from "vitest"
import { Favicon, LogoMark } from "./Branding"

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it("updates the environment favicon while keeping the theme-colored bird", async () => {
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
      <LogoMark alt="Robin" className="size-14" />
    </QueryClientProvider>
  )
  const logo = screen.getByRole("img", { name: "Robin" })
  const favicon = () => document.head.querySelector('link[rel="icon"]')
  expect(logo.style.maskImage).toBe('url("/bird-mark.png")')
  expect(favicon()?.getAttribute("href")).toBe("/favicon.png")

  await act(async () => {
    respond(Response.json({ environment: "preview" }))
  })
  await waitFor(() => {
    expect(logo.style.maskImage).toBe('url("/bird-mark.png")')
    expect(favicon()?.getAttribute("href")).toBe("/favicon-preview.png")
  })

  fetch.mockResolvedValue(Response.json({ environment: "production" }))
  await act(async () => {
    await client.invalidateQueries({ queryKey: ["branding"] })
  })
  await waitFor(() => {
    expect(logo.style.maskImage).toBe('url("/bird-mark.png")')
    expect(favicon()?.getAttribute("href")).toBe("/favicon.png")
    expect(document.head.querySelectorAll('link[rel="icon"]')).toHaveLength(1)
  })
  client.clear()
})
