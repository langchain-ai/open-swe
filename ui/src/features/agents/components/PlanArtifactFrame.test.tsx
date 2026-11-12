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
  it("stamps the viewer theme and opens a head of its own, policy first", () => {
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
    const parsed = new DOMParser().parseFromString(shelled, "text/html")
    expect(parsed.body.innerHTML).toBe("<h1>Plan</h1>")
  })

  it("opens the body before a fragment's first script and keeps a full document's own parts", () => {
    for (const source of [
      '<script>document.body.append("x")</script>',
      '<script>const template = "<body>"; document.body.append("x")</script>',
      '<!-- <head> --><script>document.body.append("x")</script>',
    ]) {
      const fragment = new DOMParser().parseFromString(
        withArtifactShell(source, "light"),
        "text/html"
      )
      expect(fragment.body.querySelector("script")).not.toBeNull()
    }

    const full = new DOMParser().parseFromString(
      withArtifactShell(
        '<!doctype html><html lang="fr"><head><title>Rapport</title><script id="config" type="application/json">{}</script></head><body class="deck">x</body></html>',
        "dark"
      ),
      "text/html"
    )
    expect(full.title).toBe("Rapport")
    expect(full.head.querySelector("#config")).not.toBeNull()
    expect(full.head.firstElementChild?.getAttribute("http-equiv")).toBe(
      "Content-Security-Policy"
    )
    expect(full.documentElement.getAttribute("lang")).toBe("fr")
    expect(full.documentElement.getAttribute("data-theme")).toBe("dark")
    expect(full.body.className).toBe("deck")
  })

  it("keeps the policy in force whatever the artifact's markup", () => {
    const parsed = new DOMParser().parseFromString(
      withArtifactShell(
        "<!-- <head> --><script>fetch('/x')</script><html><head></head></html>",
        "light"
      ),
      "text/html"
    )

    expect(
      parsed.head
        .querySelector('meta[http-equiv="Content-Security-Policy"]')
        ?.getAttribute("content")
    ).toContain("connect-src 'none'")
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
