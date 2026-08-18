/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import {
  PlanArtifactFrame,
  withViewerPolicy,
} from "@/features/agents/components/PlanArtifactFrame"

const html =
  '<!doctype html><html><head><title>Plan</title></head><body style="background:#fff;color:#111">Plan</body></html>'

describe("PlanArtifactFrame", () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it("renders the artifact in an opaque sandbox", () => {
    render(<PlanArtifactFrame html={html} />)

    const frame = screen.getByTestId("plan-artifact-frame")
    expect(frame.getAttribute("sandbox")).toBe("")
    expect(frame.getAttribute("referrerpolicy")).toBe("no-referrer")
    expect(frame.hasAttribute("allow")).toBe(false)
  })

  it("injects a restrictive policy that permits only Google Fonts", () => {
    const result = withViewerPolicy(html, "light")

    expect(result).toContain("default-src 'none'")
    expect(result).toContain(
      "style-src 'unsafe-inline' https://fonts.googleapis.com"
    )
    expect(result).toContain("font-src https://fonts.gstatic.com data:")
    expect(result).toContain("connect-src 'none'")
    expect(result).toContain("form-action 'none'")
  })

  it("stamps the resolved viewer theme on the artifact root", () => {
    expect(withViewerPolicy(html, "dark")).toContain(
      '<html data-theme="dark" data-viewer-theme="dark"'
    )
    expect(withViewerPolicy(html, "light")).toContain(
      '<html data-theme="light" data-viewer-theme="light"'
    )
  })

  it("wraps fragments in a policy-controlled document", () => {
    const result = withViewerPolicy("<h1>Fallback</h1>", "dark")

    expect(result).toContain("<!doctype html>")
    expect(result).toContain('<html data-theme="dark"')
    expect(result).toContain("<body><h1>Fallback</h1></body>")
  })
})
