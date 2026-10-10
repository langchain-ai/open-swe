import { describe, expect, it } from "vitest"

import { describeWorkEntry, liveActivityLabel } from "./workEntry"
import type {
  Chunk,
  DiffData,
  ToolExecutionChunk,
} from "@/features/agents/lib/types"

const repoPath = "/workspace/open-swe"

function chunk(
  overrides: Partial<ToolExecutionChunk> = {}
): ToolExecutionChunk {
  return {
    kind: "tool-execution",
    toolCallId: "call_1",
    title: "read_file",
    toolKind: "read",
    status: "completed",
    ...overrides,
  }
}

function diff(overrides: Partial<DiffData> = {}): DiffData {
  return {
    filePath: `${repoPath}/ui/src/app.tsx`,
    originalContent: "a\n",
    newContent: "b\n",
    isNewFile: false,
    isBinary: false,
    isTruncated: false,
    totalLines: 1,
    ...overrides,
  }
}

describe("describeWorkEntry", () => {
  it("splits a read into a verb, file name, and full-path tooltip", () => {
    const fullPath = `${repoPath}/ui/src/AGENTS.md`
    const entry = describeWorkEntry(
      chunk({ input: { file_path: fullPath } }),
      repoPath
    )

    expect(entry.heading).toBe("Read")
    expect(entry.preview).toBe("AGENTS.md")
    expect(entry.previewTooltip).toBe(fullPath)
    expect(entry.icon).toBe("eye")
  })

  it("describes a completed edit from its diff rather than the raw tool title", () => {
    const entry = describeWorkEntry(
      chunk({ title: "edit_file", toolKind: "edit", diffData: diff() }),
      repoPath
    )

    expect(entry.heading).toBe("Edited")
    expect(entry.preview).toBe("app.tsx")
    expect(entry.previewTooltip).toBe(`${repoPath}/ui/src/app.tsx`)
    expect(entry.diffStats).toEqual({ additions: 1, deletions: 1 })
    expect(entry.icon).toBe("square-pen")
    // The diff is rendered as the row body, so there is no text fallback.
    expect(entry.expandedText).toBeNull()
  })

  it("distinguishes a created file from an edited one", () => {
    const entry = describeWorkEntry(
      chunk({ toolKind: "edit", diffData: diff({ isNewFile: true }) }),
      repoPath
    )

    expect(entry.heading).toBe("Created")
  })

  it("reports an in-flight edit in the present tense", () => {
    const entry = describeWorkEntry(
      chunk({ toolKind: "edit", status: "in_progress", diffData: diff() }),
      repoPath
    )

    expect(entry.heading).toBe("Editing")
    expect(entry.status).toBe("in_progress")
  })

  it("marks failed calls with the error tone", () => {
    const entry = describeWorkEntry(
      chunk({
        title: "shell",
        toolKind: "execute",
        status: "error",
        input: { command: "pnpm test" },
      }),
      repoPath
    )

    expect(entry.tone).toBe("error")
    expect(entry.icon).toBe("terminal")
    expect(entry.heading).toBe("Shell")
    expect(entry.preview).toBe("pnpm test")
  })

  it("falls back to tool locations when the input carries no argument", () => {
    const entry = describeWorkEntry(
      chunk({
        title: "search",
        toolKind: "search",
        input: {},
        locations: [{ path: `${repoPath}/a.ts` }, { path: `${repoPath}/b.ts` }],
      }),
      repoPath
    )

    expect(entry.preview).toBe("a.ts +1 more")
  })

  it("builds an expandable body from the command and its output", () => {
    const entry = describeWorkEntry(
      chunk({
        title: "shell",
        toolKind: "execute",
        input: { command: "ls" },
        output: "a.ts\nb.ts",
      }),
      repoPath
    )

    expect(entry.expandedText).toBe("ls\n\na.ts\nb.ts")
  })

  it("labels a described command by its description and keeps the command expandable", () => {
    const entry = describeWorkEntry(
      chunk({
        title: "execute",
        toolKind: "execute",
        input: {
          command: "git status --short",
          description: "Show working tree status",
        },
        output: "M a.ts",
      }),
      repoPath
    )

    expect(entry.heading).toBe("Shell")
    expect(entry.preview).toBe("Show working tree status")
    expect(entry.previewTooltip).toBe("git status --short")
    expect(entry.expandedText).toBe("git status --short\n\nM a.ts")
  })

  it("shows important tool text in the preview and keeps every argument expandable", () => {
    const reason =
      "This is a conversation between people, not a request.".repeat(100)
    const entry = describeWorkEntry(
      chunk({
        title: "slack_no_reply_needed",
        toolKind: "other",
        input: { reason, confirmation: "Internal confirmation" },
        output: '{"ok":true}',
      })
    )

    expect(entry.preview).toBe(`${reason.slice(0, 80)}...`)
    expect(entry.previewTooltip).toBe(reason)
    expect(entry.expandedText).toContain(`reason:\n${reason}`)
    expect(entry.expandedText).not.toContain("Internal confirmation")

    const report = describeWorkEntry(
      chunk({
        title: "report_platform_issue",
        toolKind: "other",
        input: {
          problem_description: "Sandbox disconnected",
          keywords: ["sandbox"],
        },
      })
    )
    expect(report.preview).toBe("Sandbox disconnected")
    expect(report.expandedText).toBe(
      "problem description:\nSandbox disconnected"
    )

    const task = describeWorkEntry(
      chunk({
        title: "task",
        toolKind: "task",
        input: {
          description: "Investigate the failure",
          instructions: "Keep the report concise",
        },
      })
    )
    expect(task.preview).toBe("Investigate the failure")
    expect(task.expandedText).toBe(
      "description:\nInvestigate the failure\n\ninstructions:\nKeep the report concise"
    )
  })

  it("preserves arguments when lazy output replaces the preview", async () => {
    const output = "Full output\n".repeat(500)
    const entry = describeWorkEntry(
      chunk({
        title: "report_platform_issue",
        toolKind: "other",
        input: { problem_description: "Sandbox disconnected" },
        output: "Partial output",
        loadOutput: async () => output,
      })
    )

    const loaded = await entry.loadExpandedText?.()
    expect(loaded).toBe(
      `problem description:\nSandbox disconnected\n\n${output.trim()}`
    )
    expect(loaded).not.toContain("Partial output")
  })

  it("keeps complete JSON tool output available for highlighted rendering", () => {
    const output = JSON.stringify({ value: "x".repeat(5000) })
    const entry = describeWorkEntry(chunk({ output }), repoPath)

    expect(entry.expandedText).toBe(JSON.stringify(JSON.parse(output), null, 2))
    expect(entry.expandedText).not.toContain("…")
  })
})

describe("liveActivityLabel", () => {
  it("describes only the latest tool activity", () => {
    const chunks: Array<Chunk> = [
      { kind: "reasoning", text: "Inspecting" },
      chunk({
        toolCallId: "call_read",
        input: { file_path: `${repoPath}/ui/src/AGENTS.md` },
        status: "in_progress",
      }),
    ]

    expect(liveActivityLabel(chunks, repoPath)).toBe("Exploring · AGENTS.md")
  })

  it("keeps showing an active parallel tool until all tools finish", () => {
    const running = chunk({
      toolCallId: "running",
      toolKind: "execute",
      status: "in_progress",
      input: { command: "pnpm test" },
    })
    expect(liveActivityLabel([running, chunk()])).toBe("Running · pnpm test")
    expect(
      liveActivityLabel([{ ...running, status: "completed" }, chunk()])
    ).toBe("Thinking…")
  })

  it("switches to response status when final text starts streaming", () => {
    expect(
      liveActivityLabel([
        chunk({ toolKind: "execute", input: { command: "pnpm test" } }),
        { kind: "text", text: "The tests pass." },
      ])
    ).toBe("Writing response…")
  })

  it("surfaces approval and error states", () => {
    expect(liveActivityLabel([chunk({ status: "pending" })])).toBe(
      "Waiting for approval…"
    )
    expect(liveActivityLabel([chunk({ status: "error" })])).toBe(
      "Recovering from an error…"
    )
  })
})
