import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useStreamContext as useAgentThreadStream } from "@langchain/react"
import { CircleAlert, FolderOpen, X } from "lucide-react"
import { Link } from "@tanstack/react-router"

import type { ImageChunk } from "@/features/agents/lib/types"
import { Alert, AlertDescription } from "@/components/ui/alert"
import {
  AgentPanelShell,
  PANEL_MIN_CHAT_WIDTH,
} from "@/features/agents/components/AgentPanelShell"
import { AgentPromptBar } from "@/features/agents/components/AgentPromptBar"
import {
  DiffFilesView,
  toPanelFiles,
} from "@/features/agents/components/DiffFilesView"
import { Messages } from "@/features/agents/components/messages"
import { TerminalPanel } from "@/features/agents/components/TerminalPanel"
import {
  useDesktopLocalThread,
  useLocalThreadDiff,
  useRefreshLocalThreads,
} from "@/features/agents/lib/desktopLocal"
import {
  readStoredPanelCollapsed,
  writeStoredPanelCollapsed,
} from "@/features/agents/lib/gitPanelPreferences"
import { streamMessagesToUi } from "@/features/agents/lib/streamMessagesToUi"
import { messageArrivalTimestamp } from "@/features/agents/lib/messageTimestamps"
import { useIsMobile } from "@/lib/useIsMobile"
import { cn } from "@/lib/utils"

const LOCAL_PANEL_TABS = [
  ["changes", "Changes"],
  ["terminal", "Terminal"],
] as const

type LocalPanelTab = (typeof LOCAL_PANEL_TABS)[number][0]

function promptContent(text: string, images: Array<ImageChunk>) {
  const trimmed = text.trim()
  const imageBlocks = images.map((image) => ({
    type: "image",
    base64: image.base64,
    mime_type: image.mimeType,
    ...(image.fileName ? { file_name: image.fileName } : {}),
  }))
  return [...imageBlocks, ...(trimmed ? [{ type: "text", text: trimmed }] : [])]
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export function LocalAgentThreadView({ sessionId }: { sessionId: string }) {
  const stream = useAgentThreadStream()
  const threadQuery = useDesktopLocalThread(sessionId)
  const thread = threadQuery.data
  const refreshThreads = useRefreshLocalThreads()
  const initialPromptRef = useRef(false)
  const [error, setError] = useState<string | null>(null)
  const isMobile = useIsMobile()
  const [panelCollapsed, setPanelCollapsed] = useState(() =>
    readStoredPanelCollapsed(sessionId)
  )
  const [panelTab, setPanelTab] = useState<LocalPanelTab>("changes")
  const [revealFilePath, setRevealFilePath] = useState<string | null>(null)
  const [terminalContexts, setTerminalContexts] = useState<Array<string>>([])
  const handlePanelCollapsedChange = useCallback(
    (next: boolean) => {
      setPanelCollapsed(next)
      writeStoredPanelCollapsed(sessionId, next)
    },
    [sessionId]
  )
  const handleOpenFile = useCallback(
    (filePath: string) => {
      setRevealFilePath(filePath)
      setPanelTab("changes")
      handlePanelCollapsedChange(false)
    },
    [handlePanelCollapsedChange]
  )

  const isRunning = stream.isLoading || thread?.status === "running"
  const diff = useLocalThreadDiff(
    sessionId,
    !panelCollapsed && panelTab === "changes" && Boolean(thread),
    isRunning
  )
  const files = useMemo(
    () => toPanelFiles(diff.data?.files ?? []),
    [diff.data?.files]
  )
  const messages = useMemo(
    () =>
      streamMessagesToUi(
        stream.messages,
        stream.toolCalls,
        stream.subagents,
        messageArrivalTimestamp
      ),
    [stream.messages, stream.toolCalls, stream.subagents]
  )

  const updateStatus = useCallback(
    async (status: "idle" | "running" | "error") => {
      await window.openSweDesktop?.updateLocalThread({
        threadId: sessionId,
        status,
      })
      refreshThreads(sessionId)
    },
    [refreshThreads, sessionId]
  )

  const submit = useCallback(
    async (prompt: string, images: Array<ImageChunk>) => {
      if (!thread) return
      setError(null)
      await updateStatus("running")
      try {
        await stream.submit(
          {
            messages: [
              { type: "human", content: promptContent(prompt, images) },
            ],
          },
          {
            config: {
              configurable: {
                local_project_path: thread.cwd,
                ...(thread.modelId ? { agent_model_id: thread.modelId } : {}),
                ...(thread.effort ? { agent_effort: thread.effort } : {}),
              },
            },
          }
        )
        await updateStatus("idle")
      } catch (cause) {
        setError(errorMessage(cause))
        await updateStatus("error")
      }
    },
    [stream, thread, updateStatus]
  )

  useEffect(() => {
    if (!thread || initialPromptRef.current) return
    initialPromptRef.current = true
    void stream.hydrationPromise
      .then(() => window.openSweDesktop?.consumeLocalPrompt(sessionId))
      .then((pending) => {
        if (pending) return submit(pending.prompt, pending.images)
      })
      .catch((cause) => {
        setError(errorMessage(cause))
        void updateStatus("error")
      })
  }, [sessionId, stream.hydrationPromise, submit, thread, updateStatus])

  useEffect(() => {
    if (!stream.error) return
    setError(errorMessage(stream.error))
    void updateStatus("error")
  }, [stream.error, updateStatus])

  if (!thread) {
    return (
      <div className="flex min-w-0 flex-1 flex-col items-center justify-center gap-3 text-xs text-muted-foreground">
        {threadQuery.isPending
          ? "Loading local Open SWE session…"
          : threadQuery.error
            ? errorMessage(threadQuery.error)
            : "This local session no longer exists."}
        {!threadQuery.isPending && (
          <Link
            className="text-foreground underline underline-offset-4"
            to="/agents"
          >
            Start a new task
          </Link>
        )}
      </div>
    )
  }

  return (
    <div className="flex min-w-0 flex-1">
      <div
        className="flex min-w-0 flex-1 flex-col"
        style={isMobile ? undefined : { minWidth: PANEL_MIN_CHAT_WIDTH }}
      >
        <div
          className={cn(
            "mx-auto flex w-full max-w-3xl items-center gap-2 px-4 pt-3 text-xs text-muted-foreground",
            panelCollapsed && "pr-14"
          )}
        >
          <FolderOpen className="size-3.5" />
          <span className="truncate" title={thread.cwd}>
            {thread.cwd}
          </span>
          <span className="ml-auto shrink-0">This Mac</span>
        </div>
        {(error || thread.status === "error") && (
          <div className="mx-auto w-full max-w-3xl px-4 pt-3">
            <Alert variant="error">
              <CircleAlert />
              <AlertDescription>
                {error || "The local Open SWE agent stopped unexpectedly."}
              </AlertDescription>
            </Alert>
          </div>
        )}
        <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
          <Messages
            contentWidthClass="max-w-3xl"
            isStreaming={isRunning}
            isThinking={stream.isLoading}
            messages={messages}
            onOpenFile={handleOpenFile}
            streamIsLoading={stream.isLoading}
          />
          <div className="shrink-0 px-4 pb-4">
            <div className="mx-auto w-full max-w-3xl min-w-0">
              {terminalContexts.length > 0 && (
                <div className="mb-2 flex flex-wrap gap-1.5">
                  {terminalContexts.map((text, index) => (
                    <span
                      key={`${text.slice(0, 24)}:${index}`}
                      className="inline-flex max-w-full items-center gap-1 rounded-md border border-border bg-card px-2 py-1 text-[11px] text-muted-foreground"
                      title={text}
                    >
                      <span className="max-w-64 truncate">
                        Terminal selection
                      </span>
                      <button
                        type="button"
                        aria-label="Remove terminal selection"
                        onClick={() =>
                          setTerminalContexts((current) =>
                            current.filter(
                              (_, itemIndex) => itemIndex !== index
                            )
                          )
                        }
                      >
                        <X className="size-3" />
                      </button>
                    </span>
                  ))}
                </div>
              )}
              <AgentPromptBar
                activeRun={{ threadId: thread.id, running: isRunning }}
                busy={isRunning}
                compact
                onStop={async () => {
                  try {
                    await stream.stop()
                    await updateStatus("idle")
                  } catch (cause) {
                    setError(errorMessage(cause))
                    await updateStatus("error")
                  }
                }}
                onSubmit={async (prompt, images) => {
                  const terminalContext = terminalContexts.join("\n\n")
                  setTerminalContexts([])
                  await submit(
                    terminalContext
                      ? `${prompt}\n\nTerminal selection:\n\`\`\`\n${terminalContext}\n\`\`\``
                      : prompt,
                    images
                  )
                }}
                placeholder="Add a follow up"
              />
            </div>
          </div>
        </div>
      </div>
      <AgentPanelShell
        tabs={LOCAL_PANEL_TABS}
        activeTab={panelTab}
        onTabChange={setPanelTab}
        collapsed={panelCollapsed}
        onCollapsedChange={handlePanelCollapsedChange}
      >
        {({ fullScreen }) =>
          panelTab === "changes" ? (
            <DiffFilesView
              files={files}
              revealFilePath={revealFilePath}
              fullScreen={fullScreen}
              emptyLabel={localDiffEmptyLabel(
                diff.data?.status,
                diff.isPending
              )}
              truncated={diff.data?.truncated}
            />
          ) : (
            <TerminalPanel
              localSessionId={thread.id}
              cwd={thread.cwd}
              onOpenFile={handleOpenFile}
              onAddToChat={(text) =>
                setTerminalContexts((current) => [...current, text])
              }
            />
          )
        }
      </AgentPanelShell>
    </div>
  )
}

function localDiffEmptyLabel(
  status: string | undefined,
  isPending: boolean
): string {
  if (isPending) return "Reading changes…"
  if (status === "missing") return "This project is not a git repository."
  if (status === "error") return "Could not read this project's git changes."
  return "No changes yet."
}
