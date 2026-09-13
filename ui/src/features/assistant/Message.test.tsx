/** @vitest-environment jsdom */
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { AIMessage, HumanMessage, ToolMessage } from "@langchain/core/messages"
import type { BaseMessage } from "@langchain/core/messages"
import {
  AssistantRuntimeProvider,
  AuiConfig,
  Tools,
  ThreadPrimitive,
  useExternalMessageConverter,
  useExternalStoreRuntime,
} from "@assistant-ui/react"
import { convertLangChainBaseMessage } from "@assistant-ui/react-langchain"
import { AssistantMessage, toolkit } from "./Message"

vi.mock("@/features/agents/components/chat/Markdown", () => ({
  Markdown: ({ content }: { content: string }) => <div>{content}</div>,
}))
beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  HTMLElement.prototype.scrollTo = vi.fn()
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  Reflect.deleteProperty(HTMLElement.prototype, "scrollTo")
})

function Transcript({ messages }: { messages: BaseMessage[] }) {
  const converted = useExternalMessageConverter({
    messages,
    callback: convertLangChainBaseMessage,
    isRunning: false,
  })
  const runtime = useExternalStoreRuntime({
    messages: converted,
    onNew: async () => {},
  })
  const config = AuiConfig({ tools: Tools({ toolkit }) })
  return (
    <AssistantRuntimeProvider runtime={runtime} config={config}>
      <ThreadPrimitive.Root>
        <ThreadPrimitive.Viewport>
          <ThreadPrimitive.Messages>
            {() => <AssistantMessage />}
          </ThreadPrimitive.Messages>
        </ThreadPrimitive.Viewport>
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
  )
}

describe("native transcript", () => {
  it("keeps failures and HTML outputs visible outside collapsed activity", async () => {
    render(
      <Transcript
        messages={[
          new AIMessage({
            id: "calls",
            content: [{ type: "reasoning", reasoning: "Prepare output" }],
            tool_calls: [
              { id: "error", name: "execute", args: { command: "false" } },
              {
                id: "output",
                name: "output_iframe",
                args: { filename: "report.html" },
              },
            ],
          }),
          new ToolMessage({
            tool_call_id: "error",
            content: "Command failed",
            status: "error",
          }),
          new ToolMessage({
            tool_call_id: "output",
            content: "Report ready",
            artifact: {
              type: "output_iframe",
              title: "My report",
              filename: "report.html",
              html: "<p>Report</p>",
            },
          }),
        ]}
      />
    )
    expect(screen.getByText("Command failed").closest("details")?.open).toBe(
      true
    )
    expect(
      screen
        .getByText("Command failed")
        .closest("details")
        ?.parentElement?.closest("details")
    ).toBeNull()
    const report = await screen.findByTitle("My report")
    expect(report.closest("details")).toBeNull()
    expect(report.getAttribute("sandbox")).not.toContain("allow-same-origin")
  })

  it("retains intermediate text and interleaved reasoning and tools", () => {
    const { container } = render(
      <Transcript
        messages={[
          new HumanMessage({ id: "human", content: "Inspect this" }),
          new AIMessage({
            id: "first",
            content: [
              { type: "reasoning", reasoning: "First reasoning" },
              { type: "text", text: "First update" },
            ],
            tool_calls: [
              { id: "tool-a", name: "execute", args: { command: "pwd" } },
            ],
          }),
          new ToolMessage({
            id: "result-a",
            tool_call_id: "tool-a",
            name: "execute",
            content: "First result",
          }),
          new AIMessage({
            id: "second",
            content: [
              { type: "reasoning", reasoning: "Second reasoning" },
              { type: "text", text: "Second update" },
            ],
            tool_calls: [
              { id: "tool-b", name: "read_file", args: { path: "README.md" } },
            ],
          }),
          new ToolMessage({
            id: "result-b",
            tool_call_id: "tool-b",
            name: "read_file",
            content: "Second result",
          }),
          new AIMessage({ id: "final", content: "Final answer" }),
        ]}
      />
    )
    const text = container.textContent ?? ""
    const ordered = [
      "First reasoning",
      "First update",
      "execute",
      "First result",
      "Second reasoning",
      "Second update",
      "read_file",
      "Second result",
      "Final answer",
    ]
    for (const value of ordered) expect(text).toContain(value)
    for (const [index, value] of ordered.slice(1).entries()) {
      expect(text.indexOf(value)).toBeGreaterThan(text.indexOf(ordered[index]!))
    }
  })

  it("renders native image attachments on reload", () => {
    render(
      <Transcript
        messages={[
          new HumanMessage({
            id: "images",
            content: [
              {
                type: "image_url",
                image_url: { url: "data:image/jpeg;base64,aGVsbG8=" },
              },
            ],
          }),
        ]}
      />
    )
    expect(
      screen.getAllByRole("img").map((element) => element.getAttribute("src"))
    ).toEqual(["data:image/jpeg;base64,aGVsbG8="])
  })
})
