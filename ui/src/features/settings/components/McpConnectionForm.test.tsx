/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { McpConnectionForm } from "./McpConnectionForm"
import type { McpSave } from "./McpConnectionForm"

afterEach(cleanup)

function mount(editor: Parameters<typeof McpConnectionForm>[0]["editor"]) {
  const onSave = vi.fn(async (_value: McpSave) => {})
  render(
    <McpConnectionForm
      editor={editor}
      localAvailable
      pending={false}
      onSave={onSave}
      onClose={() => {}}
    />
  )
  return onSave
}

it("edits a local bearer server as a token, not a JSON blob", async () => {
  const onSave = mount({
    source: "local",
    record: {
      name: "github",
      transport: "streamable_http",
      enabled: true,
      url: "https://api.example/mcp",
      headers: { Authorization: "Bearer saved-token" },
    },
  })
  const token = screen.getByLabelText("Bearer token") as HTMLInputElement
  expect(token.value).toBe("saved-token")
  fireEvent.change(token, { target: { value: "new-token" } })
  fireEvent.click(screen.getByRole("button", { name: "Save server" }))
  await vi.waitFor(() => expect(onSave).toHaveBeenCalled())
  const saved = onSave.mock.calls[0]?.[0]
  expect(saved?.source).toBe("local")
  expect(saved?.record.headers).toEqual({ Authorization: "Bearer new-token" })
})

it("reports malformed JSON readably and does not save", async () => {
  const onSave = mount({ source: "local" })
  fireEvent.change(screen.getByLabelText("Name"), {
    target: { value: "files" },
  })
  fireEvent.change(screen.getByLabelText("Transport"), {
    target: { value: "stdio" },
  })
  fireEvent.change(screen.getByLabelText("Command"), {
    target: { value: "npx" },
  })
  fireEvent.change(screen.getByLabelText("Arguments (JSON array)"), {
    target: { value: "[-y" },
  })
  fireEvent.click(screen.getByRole("button", { name: "Save server" }))
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Arguments must be a JSON array of strings."
  )
  expect(onSave).not.toHaveBeenCalled()
})

it("sends only the listed passthrough variables for stdio servers", async () => {
  const onSave = mount({ source: "local" })
  fireEvent.change(screen.getByLabelText("Name"), {
    target: { value: "files" },
  })
  fireEvent.change(screen.getByLabelText("Transport"), {
    target: { value: "stdio" },
  })
  fireEvent.change(screen.getByLabelText("Command"), {
    target: { value: "npx" },
  })
  fireEvent.change(screen.getByLabelText(/Environment passthrough/), {
    target: { value: "MY_KEY, OTHER" },
  })
  fireEvent.click(screen.getByRole("button", { name: "Save server" }))
  await vi.waitFor(() => expect(onSave).toHaveBeenCalled())
  const saved = onSave.mock.calls[0]?.[0]
  expect(saved?.source).toBe("local")
  if (saved?.source !== "local") throw new Error("expected a local save")
  expect(saved.record.env_passthrough).toEqual(["MY_KEY", "OTHER"])
})
