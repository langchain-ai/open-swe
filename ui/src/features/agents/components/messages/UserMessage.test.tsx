/** @vitest-environment jsdom */
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import { UserMessage } from "./UserMessage"
import { ImageSourceProvider } from "@/features/agents/lib/imageSource"
import type { Message } from "@/features/agents/lib/types"

const THREAD_ID = "11111111-2222-4333-8444-555555555555"

function message(chunk: Message["chunks"][number]): Message {
  return {
    id: "m1",
    author: "user",
    timestamp: "2026-01-01T00:00:00Z",
    chunks: [chunk],
  } as Message
}

afterEach(cleanup)

describe("UserMessage images", () => {
  it("loads offloaded images through the dashboard image route", () => {
    render(
      <UserMessage
        message={message({
          kind: "image",
          fileId: "abc",
          mimeType: "image/png",
        })}
        threadId={THREAD_ID}
      />
    )
    expect(screen.getByRole("img").getAttribute("src")).toBe(
      `/dashboard/api/threads/${THREAD_ID}/images/abc`
    )
  })

  it("keeps inline bytes as a data URL", () => {
    render(
      <UserMessage
        message={message({
          kind: "image",
          base64: "AAAA",
          mimeType: "image/png",
        })}
      />
    )
    expect(screen.getByRole("img").getAttribute("src")).toBe(
      "data:image/png;base64,AAAA"
    )
  })

  it("asks the provided resolver when one is mounted", async () => {
    render(
      <ImageSourceProvider value={async (fileId) => `resolved:${fileId}`}>
        <UserMessage
          message={message({
            kind: "image",
            fileId: "abc",
            mimeType: "image/png",
          })}
          threadId={THREAD_ID}
        />
      </ImageSourceProvider>
    )
    await waitFor(() =>
      expect(screen.getByRole("img").getAttribute("src")).toBe("resolved:abc")
    )
  })
})
