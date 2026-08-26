/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import { PlanArtifactFrame } from "./PlanArtifactFrame"
import { withArtifactShell } from "@/features/agents/lib/artifactShell"

afterEach(cleanup)

describe("withArtifactShell", () => {
  it("stamps the viewer theme and injects the policy into an existing head", () => {
    const shelled = withArtifactShell(
      "<!doctype html><html><head><title>Plan</title></head><body>x</body></html>",
      "dark"
    )

    expect(shelled).toContain('<html data-theme="dark" data-viewer-theme="dark">')
    expect(shelled).toContain("<head><meta http-equiv=\"Content-Security-Policy\"")
    expect(shelled).toContain("script-src 'unsafe-inline'")
    expect(shelled).toContain("connect-src 'none'")
  })

  it("wraps a fragment that arrived without a head", () => {
    const shelled = withArtifactShell("<h1>Plan</h1>", "light")

    expect(shelled.startsWith('<!doctype html><html data-theme="light"')).toBe(true)
    expect(shelled).toContain("<body><h1>Plan</h1></body>")
  })
})

describe("PlanArtifactFrame", () => {
  it("renders the artifact in a script-enabled sandbox", () => {
    render(<PlanArtifactFrame html="<h1>Plan</h1>" />)

    const iframe = screen.getByTitle("Plan artifact")
    expect(iframe.getAttribute("sandbox")).toBe("allow-scripts allow-downloads")
    expect(iframe.getAttribute("srcdoc")).toContain("<h1>Plan</h1>")
    expect(iframe.getAttribute("referrerpolicy")).toBe("no-referrer")
  })
})
