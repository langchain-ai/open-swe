/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { ShowFileCard } from "./ShowFileCard"
import { subscribeComposerInsert } from "@/features/agents/lib/composerInsert"

vi.mock("@/features/agents/utils/diffUtils", () => ({
  useDiffOptions: () => ({
    theme: { light: "pierre-light", dark: "pierre-dark" },
    themeType: "light",
    overflow: "scroll",
    unsafeCSS: "",
  }),
}))

vi.mock("./Markdown", () => ({
  Markdown: ({ content }: { content: string }) => (
    <pre data-testid="markdown">{content}</pre>
  ),
}))

vi.mock("@pierre/diffs/react", () => ({
  File: ({
    file,
    options,
  }: {
    file: { name: string; contents: string }
    options: {
      onLineSelectionEnd: (range: { start: number; end: number }) => void
    }
  }) => (
    <div data-testid="pierre-file">
      <pre>{file.contents}</pre>
      <button
        type="button"
        onClick={() => options.onLineSelectionEnd({ start: 3, end: 2 })}
      >
        select 2-3
      </button>
    </div>
  ),
  PatchDiff: ({
    patch,
    options,
  }: {
    patch: string
    options: {
      onLineSelectionEnd: (range: {
        start: number
        end: number
        side: "additions" | "deletions"
      }) => void
    }
  }) => (
    <div data-testid="pierre-patch">
      <pre>{patch}</pre>
      <button
        type="button"
        onClick={() =>
          options.onLineSelectionEnd({ start: 2, end: 3, side: "additions" })
        }
      >
        select added
      </button>
    </div>
  ),
}))

afterEach(() => {
  cleanup()
})

describe("ShowFileCard", () => {
  it("renders images inline from the artifact bytes", () => {
    render(
      <ShowFileCard
        display={{
          type: "show_file",
          kind: "image",
          path: "shot.png",
          filename: "shot.png",
          title: "Screenshot",
          mimeType: "image/png",
          contentBase64: "AAAA",
        }}
      />
    )
    expect(screen.getByAltText("Screenshot").getAttribute("src")).toBe(
      "data:image/png;base64,AAAA"
    )
    expect(screen.queryByRole("button", { name: /comment/i })).toBeNull()
  })

  it("renders mermaid files through the markdown diagram renderer", () => {
    render(
      <ShowFileCard
        display={{
          type: "show_file",
          kind: "diagram",
          path: ".open-swe/artifacts/flow.mmd",
          filename: "flow.mmd",
          title: "Request flow",
          content: "graph TD\n  A --> B",
        }}
      />
    )
    expect(screen.getByTestId("markdown").textContent).toBe(
      "```mermaid\ngraph TD\n  A --> B\n```"
    )
    expect(screen.queryByRole("button", { name: /comment/i })).toBeNull()
  })

  it("renders html previews in a sandboxed iframe with a download button", () => {
    render(
      <ShowFileCard
        display={{
          type: "show_file",
          kind: "html",
          path: ".open-swe/artifacts/chart.html",
          filename: "chart.html",
          title: "Chart",
          previewUrl: "https://downloads.example/inline?token=secret",
          downloadUrl: "https://downloads.example/attachment?token=secret",
        }}
      />
    )
    const iframe = screen.getByTitle("Chart")
    expect(iframe.getAttribute("src")).toBe(
      "https://downloads.example/inline?token=secret"
    )
    expect(iframe.getAttribute("sandbox")).toBe("allow-scripts allow-downloads")
    expect(screen.getByRole("button", { name: "Download HTML" })).toBeTruthy()
    expect(screen.queryByRole("button", { name: /comment/i })).toBeNull()
  })

  it("renders markdown files through the markdown renderer", () => {
    render(
      <ShowFileCard
        display={{
          type: "show_file",
          kind: "markdown",
          path: "docs/plan.md",
          filename: "plan.md",
          title: "Plan",
          content: "# Plan\n\n- step",
        }}
      />
    )
    expect(screen.getByTestId("markdown").textContent).toBe("# Plan\n\n- step")
  })

  it("pre-selects the requested range and quotes the selection into the composer", () => {
    const inserted: Array<string> = []
    const unsubscribe = subscribeComposerInsert((text) => inserted.push(text))
    render(
      <ShowFileCard
        display={{
          type: "show_file",
          kind: "text",
          path: "src/app.py",
          filename: "app.py",
          title: "Gating",
          content: "a\nb\nc\nd\n",
          totalLines: 4,
          startLine: 2,
          endLine: 4,
        }}
      />
    )
    expect(screen.getByText("src/app.py")).toBeTruthy()
    fireEvent.click(screen.getByRole("button", { name: "Comment on L2-4" }))
    expect(inserted).toEqual([
      "> `src/app.py:2-4`\n> ```py\n> b\n> c\n> d\n> ```\n\n",
    ])

    fireEvent.click(screen.getByText("select 2-3"))
    fireEvent.click(screen.getByRole("button", { name: "Comment on L2-3" }))
    expect(inserted[1]).toBe("> `src/app.py:2-3`\n> ```py\n> b\n> c\n> ```\n\n")
    unsubscribe()
  })

  it("splits patches per file and quotes selected diff lines", () => {
    const inserted: Array<string> = []
    const unsubscribe = subscribeComposerInsert((text) => inserted.push(text))
    const patch = [
      "diff --git a/x.py b/x.py",
      "--- a/x.py",
      "+++ b/x.py",
      "@@ -1,2 +1,3 @@",
      " one",
      "+two",
      "+three",
      "diff --git a/y.py b/y.py",
      "--- a/y.py",
      "+++ b/y.py",
      "@@ -1 +1 @@",
      "-old",
      "+new",
      "",
    ].join("\n")
    render(
      <ShowFileCard
        display={{
          type: "show_file",
          kind: "diff",
          path: ".open-swe/artifacts/changes.patch",
          filename: "changes.patch",
          title: "Changes",
          content: patch,
        }}
      />
    )
    expect(screen.getAllByTestId("pierre-patch")).toHaveLength(2)
    expect(
      screen.getByRole("button", { name: "Comment" }).hasAttribute("disabled")
    ).toBe(true)

    fireEvent.click(screen.getAllByText("select added")[0]!)
    fireEvent.click(screen.getByRole("button", { name: "Comment on L2-3" }))
    expect(inserted).toEqual([
      "> `x.py:2-3`\n> ```diff\n> +two\n> +three\n> ```\n\n",
    ])
    unsubscribe()
  })
})
