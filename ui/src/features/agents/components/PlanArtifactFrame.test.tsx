/** @vitest-environment jsdom */

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { PlanArtifactFrame } from "./PlanArtifactFrame"
import { withArtifactShell } from "@/features/agents/lib/artifactShell"

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe("withArtifactShell", () => {
  it("stamps the viewer theme and injects the policy into an existing head", () => {
    const shelled = withArtifactShell(
      "<!doctype html><html><head><title>Plan</title></head><body>x</body></html>",
      "dark"
    )

    expect(shelled).toContain(
      '<html data-theme="dark" data-viewer-theme="dark">'
    )
    expect(shelled).toContain(
      '<head><meta http-equiv="Content-Security-Policy"'
    )
    expect(shelled).toContain("script-src 'unsafe-inline'")
    expect(shelled).toContain("<style>:root{color-scheme:dark}</style>")
    expect(shelled).toContain("connect-src 'none'")
  })

  it("wraps a fragment that arrived without a head", () => {
    const shelled = withArtifactShell("<h1>Plan</h1>", "light")

    expect(shelled.startsWith('<!doctype html><html data-theme="light"')).toBe(
      true
    )
    expect(shelled).toContain("<style>:root{color-scheme:light}</style>")
    expect(shelled).toContain("<body><h1>Plan</h1></body>")
  })
})

describe("PlanArtifactFrame", () => {
  it("requires confirmation for untrusted destinations and preserves the displayed address", () => {
    const port = {
      onmessage: null as ((event: { data: unknown }) => void) | null,
      start: vi.fn(),
      close: vi.fn(),
      postMessage: vi.fn(),
    }
    vi.stubGlobal(
      "MessageChannel",
      class {
        port1 = port
        port2 = {}
      }
    )
    const open = vi.spyOn(window, "open").mockReturnValue(null)
    render(<PlanArtifactFrame html="<h1>Report</h1>" />)
    fireEvent.load(screen.getByTitle("Artifact"))
    act(() =>
      port.onmessage?.({
        data: { type: "open-external", url: "javascript:alert(1)" },
      })
    )
    expect(screen.queryByRole("dialog")).toBeNull()
    act(() =>
      port.onmessage?.({
        data: { type: "open-external", url: "https://github.com/org/repo" },
      })
    )
    expect(screen.getByRole("dialog").textContent).toContain(
      "https://github.com/org/repo"
    )
    act(() =>
      port.onmessage?.({
        data: { type: "open-external", url: "https://evil.example/replaced" },
      })
    )
    expect(screen.getByRole("dialog").textContent).not.toContain("evil.example")
    expect(open).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole("button", { name: "Open link" }))
    expect(open).toHaveBeenCalledWith(
      "https://github.com/org/repo",
      "_blank",
      "noopener,noreferrer"
    )
    expect(screen.queryByRole("dialog")).toBeNull()
  })

  it("renders the artifact in a script-enabled sandbox", () => {
    render(<PlanArtifactFrame html="<h1>Plan</h1>" />)

    const iframe = screen.getByTitle("Artifact")
    const sandbox = iframe.getAttribute("sandbox")!.split(" ")
    expect(sandbox).toEqual(["allow-scripts", "allow-downloads"])
    expect(iframe.getAttribute("srcdoc")).toContain("<h1>Plan</h1>")
    expect(iframe.getAttribute("referrerpolicy")).toBe("no-referrer")
  })
})
