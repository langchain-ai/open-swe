import { ArrowLeftIcon } from "@langchain/macaw-components/icons"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { ArrowClockwiseIcon } from "@phosphor-icons/react/dist/ssr/ArrowClockwise"
import { ArrowSquareOutIcon } from "@phosphor-icons/react/dist/ssr/ArrowSquareOut"
import { ChatCircleDotsIcon } from "@phosphor-icons/react/dist/ssr/ChatCircleDots"
import { SidebarSimpleIcon } from "@phosphor-icons/react/dist/ssr/SidebarSimple"
import { useQuery } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { useEffect, useState } from "react"

import { LoadError } from "@/components/LoadError"
import { SideSheet } from "@/components/SideSheet"
import {
  useCollapseSidebarForPage,
  useSidebarToggleInset,
} from "@/components/sidebar-layout"
import { AgentThreadPage } from "@/features/agents/components/AgentThreadPage"
import { SandboxedHtmlFrame } from "@/features/agents/components/SandboxedHtmlFrame"
import { RightPanelShell } from "@/features/agents/components/panel/RightPanelShell"
import { RIGHT_PANEL_SHEET_CLASS_NAME } from "@/features/agents/components/panel/rightPanelLayout"
import { agentsApi } from "@/features/agents/lib/api"
import { agentThreadKeys, useAgentThread } from "@/features/agents/lib/queries"
import { useIsHydrated } from "@/lib/hydration"
import { pageTitle } from "@/lib/pageTitle"
import { useMediaQuery } from "@/lib/useIsMobile"
import { cn } from "@/lib/utils"

// The app has its own origin, so same-origin access reaches only itself. Popups
// carry LangSmith's sign-in, which a framed page cannot show.
const APP_SANDBOX = [
  "allow-scripts",
  "allow-same-origin",
  "allow-forms",
  "allow-modals",
  "allow-downloads",
  "allow-popups",
  "allow-popups-to-escape-sandbox",
].join(" ")
const APP_ALLOW = "clipboard-read; clipboard-write; fullscreen"

/** A sandbox app the agent shared, full size, with the thread's chat beside it. */
export function AppPreviewPage({
  threadId,
  port,
}: {
  threadId: string
  port: number
}) {
  useCollapseSidebarForPage()
  const leftInset = useSidebarToggleInset()
  const hydrated = useIsHydrated()
  const wideQuery = useMediaQuery("(min-width: 900px)")
  const wide = !hydrated || wideQuery
  // Unset until the person chooses, so the chat starts open only where it fits.
  const [chatChoice, setChatChoice] = useState<boolean | null>(null)
  const chatOpen = chatChoice ?? wide
  const [reloads, setReloads] = useState(0)

  const title = useAgentThread(threadId).data?.title
  const serviceUrl = useQuery({
    queryKey: agentThreadKeys.portServiceUrl(threadId, port),
    queryFn: () => agentsApi.sharePort(threadId, port),
    staleTime: Infinity,
    retry: false,
    refetchOnWindowFocus: false,
  })
  const url = serviceUrl.data?.url

  useEffect(() => {
    const documentTitle = pageTitle(title ? `${title} · :${port}` : `:${port}`)
    document.title = documentTitle
  }, [title, port])

  const chat = (
    <div className="flex h-full min-h-0 flex-col [&>*]:min-h-0 [&>*]:flex-1">
      <AgentThreadPage threadId={threadId} ownsTitle={false} />
    </div>
  )

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-surface-level-1 text-primary">
      <header
        className={cn(
          "flex h-11 shrink-0 items-center gap-space-2 border-b border-default pr-space-3",
          leftInset
        )}
      >
        <IconButton
          icon={ArrowLeftIcon}
          label="Back to thread"
          size="sm"
          color="secondary"
          variant="plain"
          asChild
        >
          <Link to="/agents/$threadId" params={{ threadId }} />
        </IconButton>
        <span className="min-w-0 truncate text-sm font-medium">
          {title ?? "App preview"}
        </span>
        <span className="shrink-0 text-xs text-secondary">Port {port}</span>
        <span className="flex-1" />
        <IconButton
          icon={ArrowClockwiseIcon}
          label="Reload app"
          size="sm"
          color="secondary"
          variant="plain"
          disabled={!url}
          onClick={() => setReloads((count) => count + 1)}
        />
        <IconButton
          icon={ArrowSquareOutIcon}
          label="Open app in a new tab"
          size="sm"
          color="secondary"
          variant="plain"
          disabled={!url}
          onClick={() => {
            if (url) window.open(url, "_blank", "noopener,noreferrer")
          }}
        />
        <IconButton
          icon={SidebarSimpleIcon}
          label={chatOpen ? "Hide chat" : "Show chat"}
          size="sm"
          color="secondary"
          variant="plain"
          iconClassName="-scale-x-100"
          aria-pressed={chatOpen}
          onClick={() => setChatChoice(!chatOpen)}
        />
      </header>
      <div className="flex min-h-0 flex-1">
        <section aria-label="App" className="relative min-w-0 flex-1">
          {url ? (
            <SandboxedHtmlFrame
              key={reloads}
              title={title ? `${title} app` : "App"}
              src={url}
              sandbox={APP_SANDBOX}
              allow={APP_ALLOW}
              className="h-full bg-surface-level-1"
            />
          ) : serviceUrl.isError ? (
            <LoadError
              title="Unable to open the app"
              context={`Thread: ${threadId} · port ${port}`}
              error={serviceUrl.error}
              retry={() => void serviceUrl.refetch()}
            />
          ) : (
            <div className="flex h-full items-center justify-center p-space-5">
              <Skeleton className="h-40 w-full max-w-md" />
            </div>
          )}
          {!chatOpen && (
            <IconButton
              icon={ChatCircleDotsIcon}
              label="Chat with the agent"
              color="primary"
              round
              className="right-space-4 bottom-space-4 absolute shadow-lg"
              onClick={() => setChatChoice(true)}
            />
          )}
        </section>
        {wide && chatOpen && (
          <RightPanelShell
            mode="inline"
            widthStorageKey="open-swe.app-preview-chat.width"
            defaultWidth={420}
          >
            {chat}
          </RightPanelShell>
        )}
      </div>
      {!wide && (
        <SideSheet
          side="right"
          title="Chat"
          open={chatOpen}
          onClose={() => setChatChoice(false)}
          className={RIGHT_PANEL_SHEET_CLASS_NAME}
        >
          {chat}
        </SideSheet>
      )}
    </div>
  )
}
