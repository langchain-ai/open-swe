/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import type {
  EnvironmentAuthProxyRules,
  EnvironmentAuthProxyRulesUpdate,
} from "@/lib/api"
import { EnvironmentAuthProxy } from "./EnvironmentAuthProxy"

const clients: Array<QueryClient> = []

afterEach(() => {
  cleanup()
  for (const client of clients) client.clear()
  clients.length = 0
  vi.restoreAllMocks()
})

function renderEditor() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  clients.push(client)
  render(
    <QueryClientProvider client={client}>
      <EnvironmentAuthProxy
        environmentSlug="default"
        environmentName="Default"
      />
    </QueryClientProvider>
  )
  return client
}

function response(body: unknown, init?: ResponseInit) {
  return new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
    ...init,
  })
}

describe("EnvironmentAuthProxy", () => {
  it("loads masked credential status and omits retained credentials from saves", async () => {
    const writes: EnvironmentAuthProxyRulesUpdate[] = []
    const urls: string[] = []
    let saved: EnvironmentAuthProxyRules = {
      rules: [
        {
          id: "github-api",
          host: "api.github.com",
          header: "Authorization",
          scheme: "bearer",
          has_credential: true,
        },
      ],
    }
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      urls.push(String(input))
      if (init?.method !== "PUT") return response(saved)
      const update = JSON.parse(
        String(init.body)
      ) as EnvironmentAuthProxyRulesUpdate
      writes.push(update)
      saved = {
        rules: update.rules.map(({ credential: _credential, ...rule }) => ({
          ...rule,
          has_credential: true,
        })),
      }
      return response(saved)
    })

    const client = renderEditor()
    expect(globalThis.fetch).not.toHaveBeenCalled()
    fireEvent.click(
      screen.getByRole("button", {
        name: "Configure authentication proxy for Default",
      })
    )

    expect(await screen.findByText("Credential configured")).toBeTruthy()
    expect(urls[0]).toContain("/dashboard/api/environments/default/auth-proxy")
    expect(
      (screen.getByLabelText("Credential for rule 1") as HTMLInputElement).value
    ).toBe("")
    fireEvent.click(screen.getByRole("button", { name: "Save rules" }))
    await waitFor(() => expect(writes).toHaveLength(1))
    expect(writes[0]).toEqual({
      rules: [
        {
          id: "github-api",
          host: "api.github.com",
          header: "Authorization",
          scheme: "bearer",
        },
      ],
    })

    const credential = screen.getByLabelText("Credential for rule 1")
    fireEvent.change(credential, { target: { value: "rotated-secret" } })
    fireEvent.click(screen.getByRole("button", { name: "Save rules" }))
    await waitFor(() => expect(writes).toHaveLength(2))
    expect(writes[1]?.rules[0]?.credential).toBe("rotated-secret")
    await waitFor(() => expect((credential as HTMLInputElement).value).toBe(""))
    expect(
      JSON.stringify(
        client
          .getMutationCache()
          .getAll()
          .map((mutation) => mutation.state.variables)
      )
    ).not.toContain("rotated-secret")
  })

  it("creates and deletes rules through full-list saves", async () => {
    const writes: EnvironmentAuthProxyRulesUpdate[] = []
    let saved: EnvironmentAuthProxyRules = { rules: [] }
    vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
      if (init?.method !== "PUT") return response(saved)
      const update = JSON.parse(
        String(init.body)
      ) as EnvironmentAuthProxyRulesUpdate
      writes.push(update)
      saved = {
        rules: update.rules.map(({ credential: _credential, ...rule }) => ({
          ...rule,
          has_credential: true,
        })),
      }
      return response(saved)
    })

    renderEditor()
    fireEvent.click(
      screen.getByRole("button", {
        name: "Configure authentication proxy for Default",
      })
    )
    expect(
      await screen.findByText("No authentication rules configured.")
    ).toBeTruthy()
    fireEvent.click(screen.getByRole("button", { name: "Add rule" }))
    expect(screen.queryByLabelText("Rule ID 1")).toBeNull()
    fireEvent.change(screen.getByLabelText("Host for rule 1"), {
      target: { value: "api.datadoghq.com" },
    })
    fireEvent.change(
      screen.getByLabelText("Authentication method for rule 1"),
      {
        target: { value: "api_key" },
      }
    )
    expect(
      (screen.getByLabelText("Header for rule 1") as HTMLInputElement).value
    ).toBe("X-API-Key")
    fireEvent.change(screen.getByLabelText("Credential for rule 1"), {
      target: { value: "new-secret" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save rules" }))
    await waitFor(() => expect(writes).toHaveLength(1))
    expect(writes[0]?.rules[0]).toMatchObject({
      host: "api.datadoghq.com",
      header: "X-API-Key",
      scheme: "api_key",
      credential: "new-secret",
    })
    expect(writes[0]?.rules[0]?.id).toMatch(/^rule-[a-f0-9]{27}$/)

    fireEvent.click(screen.getByRole("button", { name: "Remove rule 1" }))
    expect(screen.getByText("No authentication rules configured.")).toBeTruthy()
    fireEvent.click(screen.getByRole("button", { name: "Save rules" }))
    await waitFor(() => expect(writes).toHaveLength(2))
    expect(writes[1]).toEqual({ rules: [] })
  })

  it("keeps a credential draft editable after a failed save", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) =>
      init?.method === "PUT"
        ? response({ detail: "Settings unavailable" }, { status: 503 })
        : response({ rules: [] })
    )

    renderEditor()
    fireEvent.click(
      screen.getByRole("button", {
        name: "Configure authentication proxy for Default",
      })
    )
    await screen.findByText("No authentication rules configured.")
    fireEvent.click(screen.getByRole("button", { name: "Add rule" }))
    fireEvent.change(screen.getByLabelText("Host for rule 1"), {
      target: { value: "api.incident.io" },
    })
    const credential = screen.getByLabelText("Credential for rule 1")
    fireEvent.change(credential, { target: { value: "recoverable-secret" } })
    fireEvent.click(screen.getByRole("button", { name: "Save rules" }))

    expect((await screen.findByRole("alert")).textContent).toContain(
      "Settings unavailable"
    )
    expect((credential as HTMLInputElement).value).toBe("recoverable-secret")
    expect(
      (screen.getByLabelText("Host for rule 1") as HTMLInputElement).value
    ).toBe("api.incident.io")
    fireEvent.click(
      screen.getByRole("button", {
        name: "Close authentication proxy for Default",
      })
    )
    fireEvent.click(
      screen.getByRole("button", {
        name: "Configure authentication proxy for Default",
      })
    )
    await screen.findByText("No authentication rules configured.")
    expect(screen.queryByDisplayValue("recoverable-secret")).toBeNull()
  })

  it("rejects destinations that are not exact DNS hostnames", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(response({ rules: [] }))

    renderEditor()
    fireEvent.click(
      screen.getByRole("button", {
        name: "Configure authentication proxy for Default",
      })
    )
    await screen.findByText("No authentication rules configured.")
    fireEvent.click(screen.getByRole("button", { name: "Add rule" }))
    fireEvent.change(screen.getByLabelText("Host for rule 1"), {
      target: { value: "https://api.example.com:443" },
    })
    fireEvent.change(screen.getByLabelText("Credential for rule 1"), {
      target: { value: "secret" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save rules" }))

    expect((await screen.findByRole("alert")).textContent).toContain(
      "Enter an exact DNS hostname"
    )
    expect(
      fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT")
    ).toHaveLength(0)
  })
})
