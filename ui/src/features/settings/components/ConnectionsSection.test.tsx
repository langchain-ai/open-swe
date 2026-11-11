/** @vitest-environment jsdom */
import { cleanup, render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, expect, it } from "vitest"

import { ConnectionsSection } from "./ConnectionsSection"
import type { SessionUser } from "@/lib/api"

afterEach(cleanup)

it.each([null, "developer"])(
  "offers Slack connection with GitHub linked as %s",
  (github_login) => {
    const user: SessionUser = {
      login: "person",
      auth_provider: "langsmith",
      github_login,
      email: "person@example.com",
      avatar_url: null,
      is_admin: false,
      slack_oauth_enabled: true,
    }
    const client = new QueryClient()
    render(
      <QueryClientProvider client={client}>
        <ConnectionsSection user={user} />
      </QueryClientProvider>
    )
    expect(screen.getByRole("button", { name: "Connect" })).toBeTruthy()
    expect(screen.getByText("LangSmith")).toBeTruthy()
    expect(screen.queryByRole("button", { name: "Link GitHub" }) !== null).toBe(
      github_login === null
    )
    client.clear()
  }
)
