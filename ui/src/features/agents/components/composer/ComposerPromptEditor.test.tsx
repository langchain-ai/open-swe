/** @vitest-environment jsdom */

import { useRef, useState } from "react"
import { act, cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { SkillPromptText } from "../SkillBadge"
import { ComposerPromptEditor } from "./ComposerPromptEditor"
import type { ComposerPromptEditorHandle } from "./ComposerPromptEditor"
import { TooltipProvider } from "@/components/ui/tooltip"

afterEach(() => cleanup())

/** Drives the editor the way `ChatComposer` does: controlled value plus cursor. */
function Harness({
  initialValue = "",
  skillNames,
}: {
  initialValue?: string
  skillNames?: ReadonlySet<string>
}) {
  const [value, setValue] = useState(initialValue)
  const editorRef = useRef<ComposerPromptEditorHandle | null>(null)

  return (
    <TooltipProvider>
      <ComposerPromptEditor
        editorRef={editorRef}
        onChange={setValue}
        placeholder="Ask anything"
        skillNames={skillNames}
        value={value}
      />
      <output data-testid="prompt-value">{value}</output>
    </TooltipProvider>
  )
}

describe("ComposerPromptEditor", () => {
  it("renders plain text as text", () => {
    render(<Harness initialValue="rename the handler" />)
    expect(screen.getByTestId("composer-editor").textContent).toBe(
      "rename the handler"
    )
  })

  it("renders a selected skill as a badge while preserving its command", () => {
    render(
      <Harness
        initialValue="/autopilot fix this"
        skillNames={new Set(["autopilot"])}
      />
    )

    expect(screen.getByText("/autopilot")).toBeTruthy()
    expect(screen.getByTestId("composer-editor").textContent).toBe(
      "/autopilot fix this"
    )
  })

  it("renders a draft skill after skills load", async () => {
    const { rerender } = render(<Harness initialValue="/autopilot fix this" />)
    expect(screen.queryByText("/autopilot")).toBeNull()

    await act(() => {
      rerender(
        <Harness
          initialValue="/autopilot fix this"
          skillNames={new Set(["autopilot"])}
        />
      )
      return Promise.resolve()
    })

    expect(screen.getByText("/autopilot")).toBeTruthy()
  })

  it("does not badge slash paths in ordinary message text", () => {
    render(<SkillPromptText text="check /workspace" />)
    expect(screen.queryByText("/workspace")).toBeNull()
    expect(screen.getByText("check /workspace")).toBeTruthy()
  })

  it("renders a file link as a chip whose text content is the original source", () => {
    render(<Harness initialValue="fix [app.tsx](ui/src/app.tsx) now" />)

    const editor = screen.getByTestId("composer-editor")
    // The chip shows only the basename…
    expect(screen.getByText("app.tsx")).toBeTruthy()
    // …but the prompt the agent receives is still the full markdown link.
    expect(editor.textContent).toContain("app.tsx")
    expect(editor.textContent).toContain("fix")
    expect(editor.textContent).toContain("now")
  })

  it("applies a controlled value change without echoing it back as a user edit", async () => {
    const onChange = vi.fn()
    function Controlled({ value }: { value: string }) {
      const editorRef = useRef<ComposerPromptEditorHandle | null>(null)
      return (
        <TooltipProvider>
          <ComposerPromptEditor
            editorRef={editorRef}
            onChange={onChange}
            placeholder="Ask anything"
            value={value}
          />
        </TooltipProvider>
      )
    }

    const { rerender } = render(<Controlled value="" />)
    await act(() => {
      rerender(<Controlled value="look at [a.ts](src/a.ts) " />)
      return Promise.resolve()
    })

    expect(screen.getByText("a.ts")).toBeTruthy()
    expect(onChange).not.toHaveBeenCalled()
  })
})
