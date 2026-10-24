/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

const recordClientError = vi.fn()
vi.mock("@/lib/clientErrors", () => ({
  recordClientError: (...args: unknown[]) => recordClientError(...args),
}))

import { PlanArtifactFrame } from "./PlanArtifactFrame"
import { withArtifactShell } from "@/features/agents/lib/artifactShell"

afterEach(cleanup)

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
  it("only accepts bounded error reports from its own iframe", () => {
    render(<PlanArtifactFrame html="<h1>Plan</h1>" />)
    const frame = screen.getByTitle("Artifact") as HTMLIFrameElement
    const data = {
      type: "openswe:client-error",
      version: 1,
      message: "Chart failed",
    }
    window.dispatchEvent(new MessageEvent("message", { source: window, data }))
    window.dispatchEvent(
      new MessageEvent("message", {
        source: frame.contentWindow,
        data: { ...data, message: "x".repeat(2001) },
      })
    )
    expect(recordClientError).not.toHaveBeenCalled()
    window.dispatchEvent(
      new MessageEvent("message", { source: frame.contentWindow, data })
    )
    expect(recordClientError).toHaveBeenCalledWith({
      title: "Embedded artifact error",
      error: expect.objectContaining({ message: "Chart failed" }),
    })
    for (let i = 0; i < 110; i++)
      window.dispatchEvent(
        new MessageEvent("message", { source: frame.contentWindow, data })
      )
    expect(recordClientError).toHaveBeenCalledTimes(100)
    recordClientError.mockClear()
  })

  it("renders the artifact in a script-enabled sandbox", () => {
    render(<PlanArtifactFrame html="<h1>Plan</h1>" />)

    const iframe = screen.getByTitle("Artifact")
    expect(iframe.getAttribute("sandbox")).toBe("allow-scripts allow-downloads")
    expect(iframe.getAttribute("srcdoc")).toContain("<h1>Plan</h1>")
    expect(iframe.getAttribute("referrerpolicy")).toBe("no-referrer")
  })
})
