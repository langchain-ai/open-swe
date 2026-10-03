/** @vitest-environment jsdom */
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react"
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

const DISCLOSURE = "[data-transcript-disclosure]"
const TRIGGER = `${DISCLOSURE} > [data-slot="collapsible-trigger"]`

function isOpen(disclosure: Element) {
  return (
    disclosure.querySelector(TRIGGER)?.getAttribute("aria-expanded") === "true"
  )
}

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

function toolExchange(
  calls: NonNullable<AIMessage["tool_calls"]>
): BaseMessage[] {
  return [
    new AIMessage({ id: "calls", content: "", tool_calls: calls }),
    ...calls.map(
      (call) =>
        new ToolMessage({
          tool_call_id: call.id!,
          content: `Result ${call.id}`,
        })
    ),
  ]
}

describe("native transcript", () => {
  it.each([
    ["read_file", "Read"],
    ["edit_file", "Edit"],
  ])(
    "groups consecutive %s calls without dropping their details",
    (name, label) => {
      const calls = Array.from({ length: 10 }, (_, index) => ({
        id: `file-${index}`,
        name,
        args:
          name === "read_file"
            ? { file_path: "src/service.ts", offset: index * 20, limit: 20 }
            : {
                file_path: "src/service.ts",
                old_string: `before ${index}`,
                new_string: `after ${index}`,
              },
      }))
      render(<Transcript messages={toolExchange(calls)} />)
      fireEvent.click(screen.getByText("Show activity"))
      const summary = screen.getByText(`${label} src/service.ts · 10 calls`)
      const group = summary.closest<HTMLElement>(DISCLOSURE)!
      expect(isOpen(group)).toBe(false)
      fireEvent.click(summary)
      expect(isOpen(group)).toBe(true)
      for (const call of calls) {
        const result = within(group).getByText(`Result ${call.id}`)
        const details = result.closest<HTMLElement>(DISCLOSURE)!
        fireEvent.click(within(details).getByText(name))
        expect(isOpen(details)).toBe(true)
        expect(
          within(details).getByText(JSON.stringify(call.args, null, 2), {
            normalizer: (text) => text,
          })
        ).toBeTruthy()
      }
    }
  )

  it("keeps different files, operations, and intervening calls in order", () => {
    const calls = [
      { id: "read-a", name: "read_file", args: { file_path: "a.ts" } },
      { id: "read-b", name: "read_file", args: { path: "a.ts" } },
      { id: "other-a", name: "read_file", args: { file_path: "b.ts" } },
      { id: "other-b", name: "read_file", args: { file_path: "b.ts" } },
      { id: "edit-a", name: "edit_file", args: { file_path: "a.ts" } },
      { id: "edit-b", name: "edit_file", args: { file_path: "a.ts" } },
      { id: "execute", name: "execute", args: { command: "pwd" } },
      { id: "read-c", name: "read_file", args: { file_path: "a.ts" } },
      { id: "read-d", name: "read_file", args: { file_path: "a.ts" } },
    ]
    const { container } = render(<Transcript messages={toolExchange(calls)} />)
    const summaries = [...container.querySelectorAll(TRIGGER)].map(
      (element) => element.textContent
    )
    expect(summaries.filter((text) => text?.includes("·"))).toEqual([
      "Read a.ts · 2 calls",
      "Read b.ts · 2 calls",
      "Edit a.ts · 2 calls",
      "Read a.ts · 2 calls",
    ])
    const results = [...container.querySelectorAll("pre")]
      .map((element) => element.textContent)
      .filter((text) => text?.startsWith("Result "))
    expect(results).toEqual(calls.map((call) => `Result ${call.id}`))
  })

  it("leaves failures and calls without a file path out of file groups", () => {
    const messages = toolExchange([
      { id: "before", name: "read_file", args: { file_path: "a.ts" } },
      { id: "failed", name: "read_file", args: { file_path: "a.ts" } },
      { id: "after", name: "read_file", args: { file_path: "a.ts" } },
      { id: "missing", name: "read_file", args: {} },
      { id: "empty", name: "read_file", args: { file_path: "" } },
    ])
    messages[2] = new ToolMessage({
      tool_call_id: "failed",
      content: "Permission denied",
      status: "error",
    })
    render(<Transcript messages={messages} />)
    expect(screen.queryByText(/· \d+ calls/)).toBeNull()
    const failure = screen.getByText("Permission denied").closest(DISCLOSURE)!
    expect(isOpen(failure)).toBe(true)
    expect(failure.parentElement?.closest(DISCLOSURE)).toBeNull()
    expect(screen.getByText("Result missing")).toBeTruthy()
    expect(screen.getByText("Result empty")).toBeTruthy()
  })

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
    const failure = screen.getByText("Command failed").closest(DISCLOSURE)!
    expect(isOpen(failure)).toBe(true)
    expect(failure.parentElement?.closest(DISCLOSURE)).toBeNull()
    const report = await screen.findByTitle("My report")
    expect(report.closest(DISCLOSURE)).toBeNull()
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
