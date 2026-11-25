/** @vitest-environment jsdom */

import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { ChatComposer, buildCommandItems } from "./ChatComposer"
import { api } from "@/lib/api"
import { replaceTextRange } from "./composerTrigger"
import type { ChatComposerProps } from "./ChatComposer"
import { AgentThreadStreamBoundary } from "@/features/agents/lib/provider/useIsInAgentThreadStream"
import { ThreadSourceProvider } from "@/features/agents/lib/threadSource/ThreadSourceProvider"

const stream = {
  isLoading: false,
  threadId: "thread-1",
  messages: [],
  toolCalls: [],
  isThreadLoading: false,
  hydrationPromise: Promise.resolve(),
  error: null,
  submit: vi.fn(),
  disconnect: vi.fn(),
  getThread: () => null,
}

vi.mock("@langchain/react", () => ({
  useStream: () => stream,
  useChannelEffect: () => {},
  useSubmissionQueue: () => ({
    entries: [],
    size: 0,
    cancel: vi.fn(),
    clear: vi.fn(),
  }),
}))

vi.mock("@/lib/langgraph-client", () => ({
  createDashboardClient: () => ({}),
  dashboardFetch: fetch,
}))

const cancelThread = vi.fn((threadId: string) =>
  Promise.resolve({ id: threadId, status: "interrupted" })
)

vi.mock("@/features/agents/lib/api", () => ({
  agentsApi: { cancelThread: (threadId: string) => cancelThread(threadId) },
  AgentsApiError: class AgentsApiError extends Error {},
}))

vi.mock("@/lib/appCommands", () => ({
  useRegisterAppCommands: vi.fn(),
}))

afterEach(() => cleanup())

beforeEach(() => {
  stream.isLoading = false
  stream.disconnect.mockClear()
  cancelThread.mockClear()
})

function renderComposer(
  running: boolean,
  props: Partial<ChatComposerProps> = {}
) {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <AgentThreadStreamBoundary>
        <ThreadSourceProvider threadId="thread-1" transcript={false}>
          <section aria-label="Chat" data-chat-drop-zone>
            <ChatComposer
              activeRun={{ threadId: "thread-1", running }}
              {...props}
            />
          </section>
        </ThreadSourceProvider>
      </AgentThreadStreamBoundary>
    </QueryClientProvider>,
    { wrapper: TooltipProvider }
  )
}

describe("ChatComposer focus", () => {
  it("autofocuses on initial load", async () => {
    renderComposer(false, { autoFocus: true })
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole("textbox", { name: "Message" })
      )
    )
  })

  it("autofocuses after navigating from a focused link", async () => {
    render(<a href="/agents/thread-1">Thread</a>)
    screen.getByRole("link", { name: "Thread" }).focus()
    renderComposer(false, { autoFocus: true })

    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole("textbox", { name: "Message" })
      )
    )
  })

  it("preserves focus in file search", () => {
    render(<input aria-label="Search files" autoFocus />)
    const search = screen.getByRole("textbox", { name: "Search files" })
    renderComposer(false, { autoFocus: true })

    expect(document.activeElement).toBe(search)
  })

  it("preserves focus inside a dialog", () => {
    render(
      <div role="dialog" aria-label="Settings">
        <button autoFocus>Close</button>
      </div>
    )
    const close = screen.getByRole("button", { name: "Close" })
    renderComposer(false, { autoFocus: true })

    expect(document.activeElement).toBe(close)
  })
})

describe("ChatComposer file drops", () => {
  it("only attaches files dropped in the chat area", async () => {
    renderComposer(false)
    const file = new File(["Quarterly report"], "report.txt", {
      type: "text/plain",
    })

    render(<aside aria-label="Threads">Thread sidebar</aside>)
    const sidebar = screen.getByRole("complementary", { name: "Threads" })
    fireEvent.dragEnter(sidebar, { dataTransfer: { types: ["Files"] } })
    fireEvent.drop(sidebar, {
      dataTransfer: { types: ["Files"], files: [file] },
    })
    expect(screen.queryByText("Drop to attach")).toBeNull()
    expect(screen.queryByText("report.txt")).toBeNull()

    fireEvent.dragEnter(screen.getByRole("region", { name: "Chat" }), {
      dataTransfer: { types: ["Files"] },
    })
    expect(screen.getByRole("status").textContent).toBe("Drop to attach")

    fireEvent.drop(screen.getByRole("region", { name: "Chat" }), {
      dataTransfer: { types: ["Files"], files: [file] },
    })

    expect(screen.queryByText("Drop to attach")).toBeNull()
    expect(await screen.findByText("report.txt")).toBeTruthy()
    expect(document.activeElement).toBe(
      screen.getByRole("textbox", { name: "Message" })
    )
  })

  it("does not duplicate files dropped onto the composer", async () => {
    renderComposer(false)
    const file = new File(["Quarterly report"], "report.txt", {
      type: "text/plain",
    })

    fireEvent.dragEnter(screen.getByRole("region", { name: "Chat" }), {
      dataTransfer: { types: ["Files"] },
    })
    fireEvent.drop(screen.getByRole("textbox", { name: "Message" }), {
      dataTransfer: { types: ["Files"], files: [file], getData: () => "" },
    })

    expect(screen.queryByText("Drop to attach")).toBeNull()
    expect(await screen.findByText("report.txt")).toBeTruthy()
    expect(screen.getAllByText("report.txt")).toHaveLength(1)
  })

  it("keeps the overlay across child targets and clears it on exit or cancellation", () => {
    renderComposer(false)
    fireEvent.dragEnter(screen.getByRole("region", { name: "Chat" }), {
      dataTransfer: { types: ["text/plain"] },
    })
    expect(screen.queryByText("Drop to attach")).toBeNull()

    const dataTransfer = { types: ["Files"] }
    const editor = screen.getByRole("textbox", { name: "Message" })
    fireEvent.dragEnter(screen.getByRole("region", { name: "Chat" }), {
      dataTransfer,
    })
    fireEvent.dragEnter(editor, { dataTransfer })
    fireEvent.dragLeave(screen.getByRole("region", { name: "Chat" }), {
      dataTransfer,
    })
    expect(screen.getByRole("status").textContent).toBe("Drop to attach")

    fireEvent.dragLeave(editor, { dataTransfer })
    expect(screen.queryByText("Drop to attach")).toBeNull()

    fireEvent.dragEnter(screen.getByRole("region", { name: "Chat" }), {
      dataTransfer,
    })
    fireEvent.blur(window)
    expect(screen.queryByText("Drop to attach")).toBeNull()
  })

  it("does not attach chat drops when disabled", () => {
    renderComposer(false, { disabled: true })
    const drop = new Event("drop", { bubbles: true, cancelable: true })
    Object.defineProperty(drop, "dataTransfer", {
      value: { types: ["Files"], files: [new File(["report"], "report.txt")] },
    })

    fireEvent.dragEnter(screen.getByRole("region", { name: "Chat" }), {
      dataTransfer: { types: ["Files"] },
    })
    expect(screen.queryByText("Drop to attach")).toBeNull()
    fireEvent(screen.getByRole("region", { name: "Chat" }), drop)

    expect(drop.defaultPrevented).toBe(false)
    expect(screen.queryByText("report.txt")).toBeNull()
  })
})

describe("ChatComposer model selection", () => {
  it("hides the picker and model command when selection is disabled", async () => {
    Object.defineProperty(Range.prototype, "getBoundingClientRect", {
      configurable: true,
      value: () => new DOMRect(),
    })
    Object.defineProperty(Range.prototype, "getClientRects", {
      configurable: true,
      value: () => [],
    })
    const models = [
      {
        id: "openai:test",
        label: "Test",
        efforts: ["low"],
        default_effort: "low",
        supports_images: true,
      },
    ]
    renderComposer(false, {
      models,
      modelSelectionEnabled: false,
      restoreDraft: { key: 1, text: "/model", images: [] },
    })
    expect(screen.queryByRole("combobox", { name: "Model" })).toBeNull()
    expect(screen.queryByRole("option", { name: /Pick a model/ })).toBeNull()
    cleanup()

    renderComposer(false, {
      models,
      modelSelectionEnabled: true,
      restoreDraft: { key: 2, text: "/model", images: [] },
    })
    expect(screen.getByRole("combobox", { name: "Model" })).toBeTruthy()
    expect(
      await screen.findByRole("option", { name: /Pick a model/ })
    ).toBeTruthy()
  })
})

describe("ChatComposer stop button", () => {
  it("offers to stop a run this client never joined", async () => {
    renderComposer(true)

    fireEvent.click(screen.getByRole("button", { name: "Stop run" }))

    await waitFor(() => expect(cancelThread).toHaveBeenCalledWith("thread-1"))
    expect(stream.disconnect).toHaveBeenCalled()
  })

  it("cancels server-side even while streaming, since stop() may know no run id", async () => {
    stream.isLoading = true
    renderComposer(false)

    fireEvent.click(screen.getByRole("button", { name: "Stop run" }))

    await waitFor(() => expect(cancelThread).toHaveBeenCalledWith("thread-1"))
  })

  it("keeps the run live when cancellation fails", async () => {
    cancelThread.mockRejectedValueOnce(new Error("502"))
    renderComposer(true)

    fireEvent.click(screen.getByRole("button", { name: "Stop run" }))

    await waitFor(() => expect(cancelThread).toHaveBeenCalled())
    // No false "stopped" state: the stream stays connected so status polling
    // (which only runs while the cached status is `running`) keeps going.
    expect(stream.disconnect).not.toHaveBeenCalled()
    expect(screen.getByRole("button", { name: "Stop run" })).toBeTruthy()
  })

  it("stops the run on Escape", async () => {
    renderComposer(true)

    fireEvent.keyDown(document.body, { key: "Escape" })

    await waitFor(() => expect(cancelThread).toHaveBeenCalledWith("thread-1"))
  })

  it("leaves Escape to an open overlay", () => {
    renderComposer(true)
    const dialog = document.createElement("div")
    dialog.setAttribute("role", "dialog")
    document.body.appendChild(dialog)

    fireEvent.keyDown(dialog, { key: "Escape" })

    expect(cancelThread).not.toHaveBeenCalled()
  })

  it("ignores Escape when no run is live", () => {
    renderComposer(false)

    fireEvent.keyDown(document.body, { key: "Escape" })

    expect(cancelThread).not.toHaveBeenCalled()
  })

  it("shows only the stop action while a live run has no queued message", () => {
    renderComposer(true)

    expect(screen.getByRole("button", { name: "Stop run" })).toBeTruthy()
    expect(screen.queryByRole("button", { name: "Queue message" })).toBeNull()
  })

  it("shows the send button when no run is live", () => {
    renderComposer(false)

    expect(screen.getByRole("button", { name: "Send message" })).toBeTruthy()
    expect(screen.queryByRole("button", { name: "Queue message" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Stop run" })).toBeNull()
  })

  it("keeps Stop available while a new thread is being created", async () => {
    const onStop = vi.fn()
    renderComposer(true, { disabled: true, onStop })
    const stop = screen.getByRole("button", { name: "Stop run" })
    expect(stop.hasAttribute("disabled")).toBe(false)
    fireEvent.click(stop)
    await waitFor(() => expect(onStop).toHaveBeenCalledOnce())
  })

  it("stops a direct run on Escape while steer is shown", () => {
    const onStop = vi.fn()
    renderComposer(true, {
      onStop,
      restoreDraft: { key: 99, text: "Please check the tests", images: [] },
    })

    expect(screen.getByRole("button", { name: "Steer agent" })).toBeTruthy()
    fireEvent.keyDown(document.body, { key: "Escape" })

    expect(onStop).toHaveBeenCalledOnce()
  })
})

describe("ChatComposer drafts", () => {
  it("restores independent mode drafts and attachments across remounts, but not after sending", async () => {
    Object.defineProperty(Range.prototype, "getBoundingClientRect", {
      configurable: true,
      value: () => new DOMRect(),
    })
    const image = {
      kind: "image" as const,
      base64: "aGVsbG8=",
      mimeType: "image/png",
      fileName: "draft.png",
    }
    renderComposer(false, {
      draftKey: "draft-user:new:home",
      restoreDraft: { key: 1, text: "Home draft", images: [image] },
    })
    await waitFor(() =>
      expect(screen.getByRole("textbox").textContent).toContain("Home draft")
    )
    cleanup()
    renderComposer(false, {
      draftKey: "draft-user:new:code",
      restoreDraft: { key: 2, text: "Code draft", images: [] },
    })
    await waitFor(() =>
      expect(screen.getByRole("textbox").textContent).toContain("Code draft")
    )
    cleanup()
    renderComposer(false, { draftKey: "other-user:new:home" })
    expect(screen.getByRole("textbox").textContent).toBe("")
    cleanup()
    const onSubmit = vi.fn()
    renderComposer(false, { draftKey: "draft-user:new:home", onSubmit })
    expect(screen.getByRole("textbox").textContent).toContain("Home draft")
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" })
    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith(
        { text: "Home draft", images: [image], replies: [] },
        expect.anything()
      )
    )
    cleanup()
    renderComposer(false, { draftKey: "draft-user:new:code" })
    expect(screen.getByRole("textbox").textContent).toContain("Code draft")
    cleanup()
    renderComposer(false, { draftKey: "draft-user:new:home" })
    expect(screen.getByRole("textbox").textContent).toBe("")
  })

  it("replaces an untouched suggestion and its images, even after a remount", async () => {
    Object.defineProperty(Range.prototype, "getBoundingClientRect", {
      configurable: true,
      value: () => new DOMRect(),
    })
    const shot = (fileName: string) => ({
      kind: "image" as const,
      base64: "aGVsbG8=",
      mimeType: "image/png",
      fileName,
    })
    const draftKey = "draft-user:thread:suggested"
    renderComposer(false, {
      draftKey,
      restoreDraft: {
        key: 1,
        text: "About the header",
        images: [shot("before.png"), shot("after.png")],
        replacesSuggestion: true,
      },
    })
    await waitFor(() =>
      expect(screen.getByRole("textbox").textContent).toBe("About the header")
    )
    cleanup()
    const next = [1, 2, 3, 4, 5, 6].map((n) => shot(`footer-${n}.png`))
    const onSubmit = vi.fn()
    renderComposer(false, {
      draftKey,
      onSubmit,
      restoreDraft: {
        key: 1,
        text: "About the footer",
        images: next,
        replacesSuggestion: true,
      },
    })
    await waitFor(() =>
      expect(screen.getByRole("textbox").textContent).toBe("About the footer")
    )
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" })
    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith(
        { text: "About the footer", images: next, replies: [] },
        expect.anything()
      )
    )
  })

  it("keeps an oversized attachment draft until enough images are removed", async () => {
    Object.defineProperty(Range.prototype, "getBoundingClientRect", {
      configurable: true,
      value: () => new DOMRect(),
    })
    const onSubmit = vi.fn()
    const image = {
      kind: "image" as const,
      base64: "A".repeat(12 * 1024 * 1024),
      mimeType: "image/png",
      fileName: "large.png",
    }
    renderComposer(false, {
      onSubmit,
      restoreDraft: {
        key: 1,
        text: "Keep this draft",
        images: Array.from({ length: 6 }, () => image),
      },
    })
    expect((await screen.findByRole("alert")).textContent).toContain("50MB")
    expect(
      screen
        .getByRole("button", { name: "Send message" })
        .hasAttribute("disabled")
    ).toBe(true)
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" })
    expect(onSubmit).not.toHaveBeenCalled()
    expect(screen.getByRole("textbox").textContent).toBe("Keep this draft")
    const remove = screen.getAllByRole("button", {
      name: "Remove large.png",
    })[0]
    if (!remove) throw new Error("Missing attachment removal button")
    fireEvent.click(remove)
    expect(screen.queryByRole("alert")).toBeNull()
    expect(
      screen
        .getByRole("button", { name: "Send message" })
        .hasAttribute("disabled")
    ).toBe(false)
  })

  it("keeps every unsent image when a cancelled queued message comes back", async () => {
    Object.defineProperty(Range.prototype, "getBoundingClientRect", {
      configurable: true,
      value: () => new DOMRect(),
    })
    const shot = (fileName: string) => ({
      kind: "image" as const,
      base64: "aGVsbG8=",
      mimeType: "image/png",
      fileName,
    })
    const draftKey = "draft-user:thread:queued"
    const unsent = [1, 2, 3].map((n) => shot(`unsent-${n}.png`))
    renderComposer(false, {
      draftKey,
      restoreDraft: { key: 1, text: "Unsent", images: unsent },
    })
    await waitFor(() =>
      expect(screen.getByRole("textbox").textContent).toBe("Unsent")
    )
    cleanup()
    const queued = [1, 2, 3].map((n) => shot(`queued-${n}.png`))
    const onSubmit = vi.fn()
    renderComposer(false, {
      draftKey,
      onSubmit,
      restoreDraft: { key: 2, text: "Queued", images: queued },
    })
    await waitFor(() =>
      expect(screen.getByRole("textbox").textContent).toContain("Queued")
    )
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" })
    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith(
        expect.objectContaining({ images: [...queued, ...unsent] }),
        expect.anything()
      )
    )
  })
})

describe("ChatComposer options", () => {
  it("asks someone without GitHub to link it before choosing a repository", async () => {
    const onRepoChange = vi.fn()
    renderComposer(false, {
      repos: [{ full_name: "acme/api" }],
      onRepoChange,
      gitHubLinkUser: {
        login: "ada",
        auth_provider: "langsmith",
        github_login: null,
        email: "ada@example.com",
        avatar_url: null,
        is_admin: false,
      },
    })

    fireEvent.click(screen.getByRole("button", { name: "Select repository" }))

    expect(
      await screen.findByText("Connect GitHub to work in a repository")
    ).toBeTruthy()
    expect(screen.queryByText("acme/api")).toBeNull()
    expect(onRepoChange).not.toHaveBeenCalled()
  })

  it("offers attachments without a plan-mode toggle", async () => {
    renderComposer(false)

    fireEvent.keyDown(screen.getByRole("button", { name: "Add attachments" }), {
      key: "Enter",
    })

    expect(
      await screen.findByRole("menuitem", { name: "Upload files" })
    ).toBeTruthy()
    expect(screen.queryByRole("menuitem", { name: /plan mode/i })).toBeNull()
  })

  it("attaches files of any type, such as a typeless JSONL", async () => {
    renderComposer(false)
    const input =
      document.querySelector<HTMLInputElement>('input[type="file"]')!

    fireEvent.change(input, {
      target: { files: [new File(['{"a":1}\n'], "trace.jsonl")] },
    })

    expect(await screen.findByText("trace.jsonl")).toBeTruthy()
    expect(screen.queryByText(/skipped/)).toBeNull()
  })
})

describe("ChatComposer people autocomplete", () => {
  it("dismisses an empty people menu with Escape without stopping a run", async () => {
    vi.spyOn(api, "listSlackUsers").mockResolvedValue({ users: [] })
    renderComposer(true, {
      restoreDraft: { key: 1, text: "@nobody", images: [] },
    })
    await screen.findByText("No matching people or files.")
    fireEvent.keyDown(screen.getByTestId("composer-editor"), {
      key: "Escape",
      code: "Escape",
    })
    await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull())
    expect(cancelThread).not.toHaveBeenCalled()
    vi.restoreAllMocks()
  })

  it("inserts a canonical Slack mention by keyboard and submits it", async () => {
    Object.defineProperty(Range.prototype, "getBoundingClientRect", {
      configurable: true,
      value: () => new DOMRect(),
    })
    Object.defineProperty(Range.prototype, "getClientRects", {
      configurable: true,
      value: () => [],
    })
    vi.spyOn(api, "listSlackUsers").mockResolvedValue({
      users: [
        {
          id: "U123BOB",
          name: "bob",
          display_name: "Bob",
          real_name: "Bob Smith",
        },
      ],
    })
    vi.spyOn(api, "slackUserName").mockResolvedValue({ name: "Bob" })
    const onSubmit = vi.fn()
    renderComposer(false, {
      onSubmit,
      restoreDraft: { key: 1, text: "Ask @bob", images: [] },
    })
    await screen.findByRole("option", { name: /@Bob .*@bob/ })
    const editor = screen.getByTestId("composer-editor")
    fireEvent.keyDown(editor, { key: "Tab", code: "Tab" })
    await screen.findByText("@Bob")
    fireEvent.keyDown(editor, { key: "Enter", code: "Enter" })
    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith(
        expect.objectContaining({ text: "Ask <@U123BOB>" }),
        expect.anything()
      )
    )
    vi.restoreAllMocks()
  })

  it("inserts a highlighted file mention on Enter instead of sending", async () => {
    vi.spyOn(api, "listSlackUsers").mockResolvedValue({ users: [] })
    const onSubmit = vi.fn()
    renderComposer(false, {
      onSubmit,
      mentionPaths: ["docs/README.md"],
      restoreDraft: { key: 1, text: "Review @README", images: [] },
    })
    await screen.findByRole("option", { name: /README\.md/ })
    fireEvent.keyDown(screen.getByRole("textbox"), {
      key: "Enter",
      code: "Enter",
    })
    await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull())
    expect(onSubmit).not.toHaveBeenCalled()
    vi.restoreAllMocks()
  })

  it("ranks people whose names contain the query ahead of fuzzy matches", () => {
    const person = (id: string, name: string, realName: string) => ({
      id,
      name,
      display_name: realName,
      real_name: realName,
    })
    const users = [
      person("U01", "a_velichkevich", "A Velichkevich"),
      ...["Ben", "Benny", "Beni", "Bena", "Bev", "Ken", "Kev", "Vin"].map(
        (first, index) => person(`U1${index}`, first.toLowerCase(), first)
      ),
      person("U99", "kevin.lee", "Kevin Lee"),
    ]

    const labels = buildCommandItems(
      { kind: "path", query: "kevin", rangeStart: 0, rangeEnd: 6 },
      [],
      [],
      true,
      false,
      [],
      [],
      users
    ).map((item) => item.label)

    expect(labels[0]).toBe("@Kevin Lee")
  })
})

describe("ChatComposer skill autocomplete", () => {
  it("sends a trailing shell variable unless its fuzzy suggestion was explicitly chosen", async () => {
    const onSubmit = vi.fn()
    renderComposer(false, {
      onSubmit,
      skills: [
        {
          name: "patch-notes",
          description: "Write release notes",
          instructions: "",
        },
      ],
      restoreDraft: { key: 1, text: "check $PATH", images: [] },
    })
    await screen.findByRole("option", { name: /patch-notes/ })
    fireEvent.keyDown(screen.getByRole("textbox"), {
      key: "Enter",
      code: "Enter",
    })
    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith(
        expect.objectContaining({ text: "check $PATH" }),
        expect.anything()
      )
    )
  })

  it("accepts a fuzzy suggestion with arrow navigation then Enter", async () => {
    const onSubmit = vi.fn()
    renderComposer(false, {
      onSubmit,
      skills: [
        {
          name: "patch-notes",
          description: "Write release notes",
          instructions: "",
        },
      ],
      restoreDraft: { key: 1, text: "check $PATH", images: [] },
    })
    await screen.findByRole("option", { name: /patch-notes/ })
    const editor = screen.getByRole("textbox")
    fireEvent.keyDown(editor, { key: "ArrowDown", code: "ArrowDown" })
    fireEvent.keyDown(editor, { key: "Enter", code: "Enter" })
    await waitFor(() => expect(editor.textContent).toContain("/patch-notes"))
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it("completes a non-prefix slash suggestion on Enter instead of sending", async () => {
    const onSubmit = vi.fn()
    renderComposer(false, {
      onSubmit,
      skills: [{ name: "baby-sit", description: "Watch CI", instructions: "" }],
      restoreDraft: { key: 1, text: "/sit", images: [] },
    })
    await screen.findByRole("option", { name: /baby-sit/ })
    const editor = screen.getByRole("textbox")
    fireEvent.keyDown(editor, { key: "Enter", code: "Enter" })
    await waitFor(() => expect(editor.textContent).toContain("/baby-sit"))
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it.each([
    ["a path", "fix /src/models", {}],
    ["Cmd+Enter", "/sit", { metaKey: true }],
  ])(
    "sends a non-prefix slash suggestion literally for %s",
    async (_, text, modifiers) => {
      const onSubmit = vi.fn()
      renderComposer(false, {
        onSubmit,
        skills: [
          { name: "baby-sit", description: "Watch CI", instructions: "" },
          { name: "models", description: "List models", instructions: "" },
        ],
        restoreDraft: { key: 1, text, images: [] },
      })
      await screen.findByRole("option", { name: /baby-sit|models/ })
      fireEvent.keyDown(screen.getByRole("textbox"), {
        key: "Enter",
        code: "Enter",
        ...modifiers,
      })
      await waitFor(() =>
        expect(onSubmit).toHaveBeenCalledWith(
          expect.objectContaining({ text }),
          expect.anything()
        )
      )
    }
  )

  it("ranks command names ahead of descriptions and tolerates typos", () => {
    const skills = [
      {
        name: "review",
        description: "Review a pull request",
        instructions: "",
      },
      {
        name: "model-audit",
        description: "Inspect configuration",
        instructions: "",
      },
      { name: "unrelated", description: "Pick a model", instructions: "" },
    ]
    const trigger = {
      kind: "slash-command" as const,
      query: "MODEL",
      rangeStart: 0,
      rangeEnd: 6,
    }
    expect(
      buildCommandItems(trigger, [], skills)
        .slice(0, 3)
        .map((item) => item.label)
    ).toEqual(["/model", "/model-audit", "/unrelated"])
    expect(
      buildCommandItems({ ...trigger, query: "reviw" }, [], skills)[0]?.label
    ).toBe("/review")
    expect(
      buildCommandItems({ ...trigger, query: "audit" }, [], skills)[0]?.label
    ).toBe("/model-audit")
    expect(
      buildCommandItems(
        { ...trigger, query: "configuration", kind: "skill-command" },
        [],
        skills
      )[0]?.label
    ).toBe("/model-audit")
    expect(
      buildCommandItems({ ...trigger, query: "zzzzzz" }, [], skills)
    ).toEqual([])
  })

  it("omits the model command when no model picker is available", () => {
    const items = buildCommandItems(
      {
        kind: "slash-command",
        query: "model",
        rangeStart: 0,
        rangeEnd: 6,
      },
      [],
      [],
      false
    )

    expect(items).toEqual([])
  })

  it("shows only skills for the dollar picker while slash keeps both", () => {
    const skills = [
      {
        name: "baby-sit",
        description: "Monitor a pull request",
        instructions: "",
      },
    ]
    const dollarItems = buildCommandItems(
      {
        kind: "skill-command",
        query: "baby",
        rangeStart: 0,
        rangeEnd: 5,
      },
      [],
      skills
    )
    const slashItems = buildCommandItems(
      {
        kind: "slash-command",
        query: "",
        rangeStart: 0,
        rangeEnd: 1,
      },
      [],
      skills
    )

    expect(dollarItems).toEqual([
      expect.objectContaining({
        type: "skill",
        name: "baby-sit",
        label: "/baby-sit",
      }),
    ])
    expect(slashItems.some((item) => item.type === "slash-command")).toBe(true)
    expect(slashItems.some((item) => item.type === "skill")).toBe(true)
  })

  it("prefers a colliding skill and preserves surrounding prompt text", () => {
    const trigger = {
      kind: "slash-command" as const,
      query: "model",
      rangeStart: 7,
      rangeEnd: 13,
    }
    const items = buildCommandItems(
      trigger,
      [],
      [
        {
          name: "model",
          description: "Inspect a model",
          instructions: "",
        },
      ]
    )

    expect(items).toEqual([
      expect.objectContaining({ type: "skill", name: "model" }),
    ])
    expect(replaceTextRange("Please /model this", 7, 13, "/model ").text).toBe(
      "Please /model  this"
    )
  })
})
