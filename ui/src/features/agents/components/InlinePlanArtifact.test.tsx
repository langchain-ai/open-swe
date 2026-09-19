/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { InlinePlanArtifact } from "./InlinePlanArtifact"

const mocks = vi.hoisted(() => ({
  status: "ready",
}))

vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({
    data: {
      status: mocks.status,
      html: "<h1>Report</h1>",
      markdown: "",
    },
  }),
}))
vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => vi.fn(),
}))
vi.mock("@/features/agents/components/PlanArtifactFrame", () => ({
  PlanArtifactFrame: ({ title }: { title: string }) => <div title={title} />,
}))

afterEach(() => {
  cleanup()
  mocks.status = "ready"
})

describe("InlinePlanArtifact", () => {
  it("uses artifact terminology for shared HTML reports", () => {
    mocks.status = "shared"
    render(<InlinePlanArtifact threadId="thread-1" />)

    expect(
      screen.getByRole("button", { name: "Open artifact in the conversation" })
        .textContent
    ).toContain("Open artifact")
    expect(screen.getByTitle("Artifact preview")).toBeTruthy()
  })

  it("keeps plan terminology for implementation plans", () => {
    render(<InlinePlanArtifact threadId="thread-1" />)

    expect(
      screen.getByRole("button", { name: "Open plan in the conversation" })
        .textContent
    ).toContain("Open plan")
    expect(screen.getByTitle("Plan preview")).toBeTruthy()
  })
})
