import { useCallback, useMemo, useState } from "react"
import { CircleAlert, FolderOpen, X } from "lucide-react"
import { Link } from "@tanstack/react-router"

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
  readStoredPanelCollapsed,
  writeStoredPanelCollapsed,
} from "@/features/agents/lib/gitPanelPreferences"
import {
  useDesktopAcpSession,
  useLocalSessionDiff,
} from "@/features/agents/lib/desktopAcp"
import { useIsMobile } from "@/lib/useIsMobile"
import { cn } from "@/lib/utils"

const LOCAL_PANEL_TABS = [
  ["changes", "Changes"],
  ["terminal", "Terminal"],
] as const

type LocalPanelTab = (typeof LOCAL_PANEL_TABS)[number][0]

export function LocalAgentThreadView({ sessionId }: { sessionId: string }) {
  const { session, messages, loaded } = useDesktopAcpSession(sessionId)
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

  const isRunning =
    session?.status === "running" || session?.status === "starting"
  const diff = useLocalSessionDiff(
    sessionId,
    !panelCollapsed && panelTab === "changes" && Boolean(session),
    isRunning
  )
  const files = useMemo(
    () => toPanelFiles(diff.data?.files ?? []),
    [diff.data?.files]
  )

  if (!session) {
    return (
      <div className="flex min-w-0 flex-1 flex-col items-center justify-center gap-3 text-xs text-muted-foreground">
        {loaded
          ? "This local session is no longer running."
          : "Loading local Deep Agents Code session…"}
        {loaded && (
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
            // The collapsed panel floats a fixed expand button in the top-right
            // corner; clear it so it never covers the "This Mac" label.
            panelCollapsed && "pr-14"
          )}
        >
          <FolderOpen className="size-3.5" />
          <span className="truncate" title={session.cwd}>
            {session.cwd}
          </span>
          <span className="ml-auto shrink-0">This Mac</span>
        </div>
        {session.status === "error" && (
          <div className="mx-auto w-full max-w-3xl px-4 pt-3">
            <Alert variant="error">
              <CircleAlert />
              <AlertDescription>
                Deep Agents Code stopped. Start a new local session to continue.
              </AlertDescription>
            </Alert>
          </div>
        )}
        <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
          <Messages
            contentWidthClass="max-w-3xl"
            isStreaming={isRunning}
            isThinking={isRunning}
            messages={messages}
            onOpenFile={handleOpenFile}
            streamIsLoading={isRunning}
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
                activeRun={{ threadId: session.id, running: isRunning }}
                busy={isRunning}
                compact
                disabled={session.status === "error"}
                onStop={() =>
                  window.openSweDesktop?.cancelAcpSession(session.id)
                }
                onSubmit={async (prompt, images) => {
                  const terminalContext = terminalContexts.join("\n\n")
                  setTerminalContexts([])
                  await window.openSweDesktop?.promptAcpSession({
                    sessionId: session.id,
                    prompt: terminalContext
                      ? `${prompt}\n\nTerminal selection:\n\`\`\`\n${terminalContext}\n\`\`\``
                      : prompt,
                    images,
                  })
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
              localSessionId={session.id}
              cwd={session.cwd}
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
