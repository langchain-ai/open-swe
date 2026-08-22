/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { useRepos } from "./profile"
import { writeCachedRepos } from "./repoCache"
import type * as apiModule from "./api"
import type { ReposPayload } from "./api"

const sessionLogin = vi.hoisted(() => ({ current: "octocat" }))

vi.mock("./session", () => ({
  useSession: () => ({ data: { login: sessionLogin.current } }),
}))

const reposMock = vi.hoisted(() => vi.fn())

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof apiModule>()),
  api: { repos: reposMock },
}))

function payloadFor(login: string): ReposPayload {
  return {
    installations: [],
    repositories: [{ full_name: `${login}/api`, private: false }],
  }
}

function renderRepos() {
  const seen: Array<ReposPayload | undefined> = []
  function Probe() {
    seen.push(useRepos().data)
    return null
  }
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const view = render(
    <QueryClientProvider client={client}>
      <Probe />
    </QueryClientProvider>
  )
  return { seen, view }
}

afterEach(() => {
  window.localStorage.clear()
  reposMock.mockReset()
  sessionLogin.current = "octocat"
})

describe("useRepos", () => {
  it("serves the localStorage payload while the network call is in flight", async () => {
    writeCachedRepos("octocat", payloadFor("octocat"))
    reposMock.mockReturnValue(new Promise(() => {}))

    const { seen } = renderRepos()

    await waitFor(() => expect(seen.at(-1)).toEqual(payloadFor("octocat")))
  })

  it("never serves another login's cached payload", async () => {
    writeCachedRepos("octocat", payloadFor("octocat"))
    sessionLogin.current = "hubot"
    reposMock.mockReturnValue(new Promise(() => {}))

    const { seen } = renderRepos()

    await waitFor(() => expect(reposMock).toHaveBeenCalled())
    expect(seen.every((data) => data === undefined)).toBe(true)
  })
})
