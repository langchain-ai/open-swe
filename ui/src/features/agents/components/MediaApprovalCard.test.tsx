/** @vitest-environment jsdom */

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { MediaApprovalCard } from "./MediaApprovalCard"
import type { MediaRequest } from "@/features/agents/lib/types"

const mocks = vi.hoisted(() => ({
  mutateAsync: vi.fn(),
  useMediaRequests: vi.fn(),
  useMediaRequestDecision: vi.fn(),
}))

vi.mock("@/features/agents/lib/queries", () => ({
  useMediaRequests: mocks.useMediaRequests,
  useMediaRequestDecision: mocks.useMediaRequestDecision,
}))

const request: MediaRequest = {
  fingerprint: "a".repeat(64),
  status: "pending",
  owner: "langchain-ai",
  repo: "open-swe",
  pullNumber: 2815,
  pullTitle: "feat(github): add human-approved PR media uploads",
  fileName: "screenshot.png",
  contentType: "image/png",
  sizeBytes: 2048,
  digest: "b".repeat(64),
  requestedBy: "alice",
  requestedAt: "2026-09-15T20:00:00+00:00",
  expiresAtEpoch: Math.floor(Date.now() / 1000) + 3600,
  decidedBy: null,
  decidedAt: null,
  assetUrl: null,
  error: null,
}

beforeEach(() => {
  mocks.mutateAsync.mockResolvedValue({})
  mocks.useMediaRequests.mockReturnValue({
    data: { requests: [request] },
  })
  mocks.useMediaRequestDecision.mockReturnValue({
    mutateAsync: mocks.mutateAsync,
    isPending: false,
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("MediaApprovalCard", () => {
  it("renders the pending media request with destination, digest, and preview", () => {
    render(<MediaApprovalCard threadId="thread-1" />)

    expect(screen.getByTestId("media-approval-card")).toBeTruthy()
    expect(
      screen.getByText("Approve attaching screenshot.png to a pull request")
    ).toBeTruthy()
    const link = screen.getByRole("link", {
      name: /langchain-ai\/open-swe#2815/,
    })
    expect(link.getAttribute("href")).toBe(
      "https://github.com/langchain-ai/open-swe/pull/2815"
    )
    expect(screen.getByText(/SHA-256/)).toBeTruthy()
    expect(screen.getByText("alice")).toBeTruthy()
    const preview = screen.getByTestId("media-approval-preview")
    expect(preview.getAttribute("src")).toContain(
      `/pr-media/thread-1/${"a".repeat(64)}/preview`
    )
  })

  it("renders nothing when there are no pending requests", () => {
    mocks.useMediaRequests.mockReturnValue({
      data: { requests: [{ ...request, status: "completed" }] },
    })

    const { container } = render(<MediaApprovalCard threadId="thread-1" />)

    expect(container.firstChild).toBeNull()
  })

  it("submits the approve decision with the server fingerprint", async () => {
    render(<MediaApprovalCard threadId="thread-1" />)

    fireEvent.click(screen.getByTestId("media-approve"))

    await waitFor(() =>
      expect(mocks.mutateAsync).toHaveBeenCalledWith({
        fingerprint: "a".repeat(64),
        decision: "approve",
      })
    )
  })

  it("submits the reject decision", async () => {
    render(<MediaApprovalCard threadId="thread-1" />)

    fireEvent.click(screen.getByTestId("media-reject"))

    await waitFor(() =>
      expect(mocks.mutateAsync).toHaveBeenCalledWith({
        fingerprint: "a".repeat(64),
        decision: "reject",
      })
    )
  })

  it("shows a digest note instead of an image for video media", () => {
    mocks.useMediaRequests.mockReturnValue({
      data: {
        requests: [
          {
            ...request,
            fileName: "clip.mp4",
            contentType: "video/mp4",
          },
        ],
      },
    })

    render(<MediaApprovalCard threadId="thread-1" />)

    expect(screen.queryByTestId("media-approval-preview")).toBeNull()
    expect(screen.getByText(/Video preview is not available/)).toBeTruthy()
  })
})
