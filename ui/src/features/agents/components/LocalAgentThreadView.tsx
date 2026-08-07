import { useCallback, useState } from "react"
import { CircleAlert, FolderOpen } from "lucide-react"
import { Link } from "@tanstack/react-router"

import { Alert, AlertDescription } from "@/components/ui/alert"
import {
  AgentPanelShell,
  PANEL_MIN_CHAT_WIDTH,
} from "@/features/agents/components/AgentPanelShell"
import { AgentPromptBar } from "@/features/agents/components/AgentPromptBar"
import { Messages } from "@/features/agents/components/messages"
import { TerminalPanel } from "@/features/agents/components/TerminalPanel"
import {
  readStoredPanelCollapsed,
  writeStoredPanelCollapsed,
} from "@/features/agents/lib/gitPanelPreferences"
import { useDesktopAcpSession } from "@/features/agents/lib/desktopAcp"
import { useIsMobile } from "@/lib/useIsMobile"
import { cn } from "@/lib/utils"

const LOCAL_PANEL_TABS = [["terminal", "Terminal"]] as const

export function LocalAgentThreadView({ sessionId }: { sessionId: string }) {
  const { session, messages, loaded } = useDesktopAcpSession(sessionId)
  const isMobile = useIsMobile()
  const [panelCollapsed, setPanelCollapsed] = useState(() =>
    readStoredPanelCollapsed(sessionId)
  )
  const handlePanelCollapsedChange = useCallback(
    (next: boolean) => {
      setPanelCollapsed(next)
      writeStoredPanelCollapsed(sessionId, next)
    },
    [sessionId]
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

  const isRunning =
    session.status === "running" || session.status === "starting"

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
            streamIsLoading={isRunning}
          />
          <div className="shrink-0 px-4 pb-4">
            <div className="mx-auto w-full max-w-3xl min-w-0">
              <AgentPromptBar
                activeRun={{ threadId: session.id, running: isRunning }}
                busy={isRunning}
                compact
                disabled={session.status === "error"}
                onStop={() =>
                  window.openSweDesktop?.cancelAcpSession(session.id)
                }
                onSubmit={async (prompt, images) => {
                  await window.openSweDesktop?.promptAcpSession({
                    sessionId: session.id,
                    prompt,
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
        activeTab="terminal"
        onTabChange={() => {}}
        collapsed={panelCollapsed}
        onCollapsedChange={handlePanelCollapsedChange}
      >
        {() => (
          <TerminalPanel id={`local-session:${session.id}`} cwd={session.cwd} />
        )}
      </AgentPanelShell>
    </div>
  )
}
