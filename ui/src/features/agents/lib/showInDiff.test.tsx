/** @vitest-environment jsdom */
import { describe, expect, it, vi } from "vitest"
import { renderHook } from "@testing-library/react"
import { AIMessage, ToolMessage } from "@langchain/core/messages"
import type { BaseMessage } from "@langchain/core/messages"

import type { ShowInDiffTarget } from "@/features/agents/lib/showInDiff"
import {
  parseShowInDiffArtifact,
  useShowInDiffRequests,
  useShowInDiffScopeFallback,
} from "@/features/agents/lib/showInDiff"

function request(toolCallId: string, artifact: unknown): ToolMessage {
  return new ToolMessage({
    content: "",
    tool_call_id: toolCallId,
    artifact,
  })
}

const target = { type: "show_in_diff", path: "agent/server.py", line: 42 }

describe("parseShowInDiffArtifact", () => {
  it("reads path, line and side", () => {
    expect(parseShowInDiffArtifact({ ...target, side: "old" })).toEqual({
      path: "agent/server.py",
      line: 42,
      side: "old",
    })
  })

  it("defaults a missing or unusable line to the whole file", () => {
    expect(
      parseShowInDiffArtifact({ type: "show_in_diff", path: "a.py" })
    ).toEqual({ path: "a.py", line: null, side: "new" })
    expect(
      parseShowInDiffArtifact({ type: "show_in_diff", path: "a.py", line: "3" })
    ).toEqual({ path: "a.py", line: null, side: "new" })
  })

  it("ignores artifacts from other tools", () => {
    expect(parseShowInDiffArtifact({ type: "output_iframe" })).toBeNull()
    expect(
      parseShowInDiffArtifact({ type: "show_in_diff", path: " " })
    ).toBeNull()
    expect(parseShowInDiffArtifact(null)).toBeNull()
  })
})

describe("useShowInDiffRequests", () => {
  it("consumes the transcript it loads with, then reports new requests", () => {
    const onShow = vi.fn()
    const history: Array<BaseMessage> = [request("call-1", target)]
    const { rerender } = renderHook(
      ({ messages }: { messages: Array<BaseMessage> }) =>
        useShowInDiffRequests(messages, true, onShow),
      { initialProps: { messages: history } }
    )
    expect(onShow).not.toHaveBeenCalled()

    rerender({
      messages: [
        ...history,
        new AIMessage("here"),
        request("call-2", { ...target, path: "ui/src/app.tsx", line: 7 }),
      ],
    })
    expect(onShow).toHaveBeenCalledTimes(1)
    expect(onShow).toHaveBeenCalledWith({
      path: "ui/src/app.tsx",
      line: 7,
      side: "new",
    })
  })

  it("waits for hydration before treating a request as new", () => {
    const onShow = vi.fn()
    const { rerender } = renderHook(
      ({ ready, messages }: { ready: boolean; messages: Array<BaseMessage> }) =>
        useShowInDiffRequests(messages, ready, onShow),
      { initialProps: { ready: false, messages: [] as Array<BaseMessage> } }
    )
    rerender({ ready: false, messages: [request("call-1", target)] })
    expect(onShow).not.toHaveBeenCalled()

    rerender({ ready: true, messages: [request("call-1", target)] })
    expect(onShow).not.toHaveBeenCalled()

    rerender({
      ready: true,
      messages: [request("call-1", target), request("call-2", target)],
    })
    expect(onShow).toHaveBeenCalledTimes(1)
  })

  it("reports only the last request of a batch", () => {
    const onShow = vi.fn()
    const { rerender } = renderHook(
      ({ messages }: { messages: Array<BaseMessage> }) =>
        useShowInDiffRequests(messages, true, onShow),
      { initialProps: { messages: [] as Array<BaseMessage> } }
    )
    rerender({
      messages: [
        request("call-1", target),
        request("call-2", { ...target, path: "b.py", line: 1 }),
      ],
    })
    expect(onShow).toHaveBeenCalledTimes(1)
    expect(onShow).toHaveBeenCalledWith({ path: "b.py", line: 1, side: "new" })
  })
})

describe("useShowInDiffScopeFallback", () => {
  const reveal: ShowInDiffTarget = {
    path: "agent/server.py",
    line: 42,
    side: "new",
  }

  function render(
    props: Partial<Parameters<typeof useShowInDiffScopeFallback>[0]>
  ) {
    const onScopeChange = vi.fn()
    const initialProps = {
      target: reveal,
      files: [] as Array<{ filePath: string }>,
      loaded: true,
      scope: "working-tree" as const,
      branchScopeAvailable: true,
      onScopeChange,
      ...props,
    }
    const view = renderHook(
      (next: typeof initialProps) => useShowInDiffScopeFallback(next),
      { initialProps }
    )
    return { ...view, onScopeChange, initialProps }
  }

  it("switches scope when the loaded scope lacks the file", () => {
    const { onScopeChange } = render({})
    expect(onScopeChange).toHaveBeenCalledWith("branch")
  })

  it("stays put when the file is in the loaded scope", () => {
    const { onScopeChange } = render({
      files: [{ filePath: "agent/server.py" }],
    })
    expect(onScopeChange).not.toHaveBeenCalled()
  })

  it("waits for the scope's diff before deciding", () => {
    const { onScopeChange, rerender, initialProps } = render({ loaded: false })
    expect(onScopeChange).not.toHaveBeenCalled()
    rerender({ ...initialProps, loaded: true })
    expect(onScopeChange).toHaveBeenCalledWith("branch")
  })

  it("switches only once per request", () => {
    const { onScopeChange, rerender, initialProps } = render({})
    rerender({ ...initialProps, scope: "branch" })
    expect(onScopeChange).toHaveBeenCalledTimes(1)
  })

  it("does not reach for a branch scope the thread cannot show", () => {
    const { onScopeChange } = render({ branchScopeAvailable: false })
    expect(onScopeChange).not.toHaveBeenCalled()
  })
})
