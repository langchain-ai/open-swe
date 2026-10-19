import { Button } from "@langchain/macaw-components/Button"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Text } from "@langchain/macaw-components/Text"
import { XIcon } from "@langchain/macaw-components/icons"
import { ArrowSquareOutIcon } from "@phosphor-icons/react/dist/ssr/ArrowSquareOut"
import { Link } from "@tanstack/react-router"
import { useCallback, useMemo, useState } from "react"

import { SideSheet } from "@/components/SideSheet"
import { AgentThreadPage } from "@/features/agents/components/AgentThreadPage"
import { PlanArtifactFrame } from "@/features/agents/components/PlanArtifactFrame"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { ChatExcerptsContext } from "@/features/agents/components/composer/ChatExcerptChips"
import { RightPanelShell } from "@/features/agents/components/panel/RightPanelShell"
import { RIGHT_PANEL_SHEET_CLASS_NAME } from "@/features/agents/components/panel/rightPanelLayout"
import type { CodeExcerpt } from "@/features/agents/utils/codeExcerpt"
import type { PlanData, PlanTextAnchor } from "@/lib/plan"
import { useMediaQuery } from "@/lib/useIsMobile"

async function copyToClipboard(text: string): Promise<boolean> {
  const nav = navigator as { clipboard?: Clipboard }
  try {
    if (window.isSecureContext && nav.clipboard) {
      await nav.clipboard.writeText(text)
      return true
    }
  } catch {
    /* fall through */
  }
  try {
    const textarea = document.createElement("textarea")
    textarea.value = text
    textarea.setAttribute("readonly", "")
    textarea.style.position = "fixed"
    textarea.style.top = "-9999px"
    document.body.appendChild(textarea)
    textarea.select()
    textarea.setSelectionRange(0, text.length)
    const copied = document.execCommand("copy")
    document.body.removeChild(textarea)
    return copied
  } catch {
    return false
  }
}

export function PlanReview({ plan }: { plan: PlanData }) {
  const [excerpts, setExcerpts] = useState<Array<CodeExcerpt>>([])
  const [chatOpen, setChatOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const wide = useMediaQuery("(min-width: 1100px)")
  const format = plan.html.trim() ? "html" : "markdown"
  const content = format === "html" ? plan.html : plan.markdown
  const removeExcerpt = useCallback((index: number) => {
    setExcerpts((current) => current.filter((_, i) => i !== index))
  }, [])
  const clearExcerpts = useCallback(() => {
    setExcerpts((current) =>
      current.filter((excerpt) => !excerpts.includes(excerpt))
    )
  }, [excerpts])
  const selectionActions = useMemo(
    () => ({ excerpts, removeExcerpt, clearExcerpts }),
    [excerpts, removeExcerpt, clearExcerpts]
  )
  const attachSelection = useCallback((anchor: PlanTextAnchor) => {
    const before = (anchor.context_before ?? anchor.prefix)
      .split("\n")
      .filter((line) => line.trim())
      .slice(-2)
    const selected = anchor.exact.split("\n")
    const after = (anchor.context_after ?? anchor.suffix)
      .split("\n")
      .filter((line) => line.trim())
      .slice(0, 2)
    const snippet = [
      ...(before.length
        ? [
            "Context before (not selected):",
            ...before.map((line) => `  | ${line}`),
          ]
        : []),
      "Selected text:",
      ...selected.map((line, i) => `> ${i + 1} | ${line}`),
      ...(after.length
        ? [
            "Context after (not selected):",
            ...after.map((line) => `  | ${line}`),
          ]
        : []),
    ].join("\n")
    setExcerpts((current) =>
      current.some((excerpt) => excerpt.snippet === snippet)
        ? current
        : [
            ...current,
            {
              path: "Artifact",
              lineLabel: "selection",
              language: "text",
              snippet,
            },
          ]
    )
    setChatOpen(true)
  }, [])

  const copyPlan = useCallback(async () => {
    setError(null)
    if (await copyToClipboard(content)) {
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } else {
      setError(`Couldn't copy the artifact ${format} to the clipboard.`)
    }
  }, [content, format])

  return (
    <ChatExcerptsContext.Provider value={selectionActions}>
      <main
        data-testid="plan-review"
        className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-surface-level-1 text-primary"
      >
        <header className="flex shrink-0 flex-wrap items-center justify-between gap-space-3 border-b border-default p-space-3 md:px-space-4">
          <div data-testid="plan-summary" className="min-w-0">
            <Text as="h1" variant="h2" weight="semibold" color="primary">
              Artifact
            </Text>
            <p className="text-xs text-secondary">
              Select text to attach it to your next message.
            </p>
          </div>
          <div
            data-testid="plan-actions"
            className="flex items-center gap-space-2"
          >
            <Button
              data-testid="copy-plan"
              color="secondary"
              variant="outlined"
              disabled={!content.trim()}
              onClick={() => void copyPlan()}
            >
              {copied
                ? "Copied!"
                : `Copy ${format === "html" ? "HTML" : "Markdown"}`}
            </Button>
            {!wide && (
              <Button
                color="secondary"
                variant="outlined"
                onClick={() => setChatOpen(true)}
              >
                Chat
              </Button>
            )}
          </div>
        </header>
        {error && (
          <p
            role="alert"
            className="px-space-4 pt-space-2 text-xs text-error-secondary"
          >
            {error}
          </p>
        )}
        <div className="flex min-h-0 w-full flex-1">
          <section
            data-testid="plan-document"
            aria-label="Artifact"
            className="flex min-h-0 min-w-0 flex-1 overflow-hidden"
          >
            {format === "html" ? (
              <PlanArtifactFrame
                html={content}
                onTextSelected={attachSelection}
                className="h-full min-h-0 min-w-0 flex-1"
              />
            ) : (
              <div
                data-testid="plan-markdown"
                className="h-full w-full overflow-y-auto p-space-4 md:p-space-5"
              >
                <Markdown content={content} />
              </div>
            )}
          </section>
          {wide && (
            <RightPanelShell
              mode="inline"
              widthStorageKey="open-swe.artifact-panel.width"
              defaultWidth={440}
            >
              <ArtifactChat threadId={plan.threadId} />
            </RightPanelShell>
          )}
        </div>
        {!wide && (
          <SideSheet
            side="right"
            title="Artifact chat"
            open={chatOpen}
            onClose={() => setChatOpen(false)}
            className={RIGHT_PANEL_SHEET_CLASS_NAME}
          >
            <ArtifactChat
              threadId={plan.threadId}
              onClose={() => setChatOpen(false)}
            />
          </SideSheet>
        )}
      </main>
    </ChatExcerptsContext.Provider>
  )
}

function ArtifactChat({
  threadId,
  onClose,
}: {
  threadId: string
  onClose?: () => void
}) {
  return (
    <aside
      aria-label="Artifact chat"
      data-testid="artifact-chat"
      className="flex h-full min-h-0 flex-col bg-surface-level-1"
    >
      <div className="flex h-11 shrink-0 items-center gap-space-2 border-b border-default px-space-3">
        <Text as="h2" variant="h5" weight="semibold">
          Chat
        </Text>
        <span className="flex-1" />
        <Button
          size="xs"
          color="secondary"
          variant="plain"
          rightDecorator={ArrowSquareOutIcon}
          as={<Link to="/agents/$threadId" params={{ threadId }} />}
        >
          Full thread
        </Button>
        {onClose && (
          <IconButton
            icon={XIcon}
            label="Close chat"
            size="sm"
            color="secondary"
            variant="plain"
            onClick={onClose}
          />
        )}
      </div>
      <div className="flex min-h-0 flex-1 flex-col [&>*]:min-h-0 [&>*]:flex-1">
        <AgentThreadPage threadId={threadId} ownsTitle={false} embedded />
      </div>
    </aside>
  )
}
