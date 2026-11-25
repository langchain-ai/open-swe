import { useContext, useEffect, useRef } from "react"
import { useNavigate } from "@tanstack/react-router"
import { useSidebarCollapsed } from "@/components/sidebar-layout"
import { EmbeddedThreadContext } from "@/features/agents/lib/embeddedThread"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { Icon } from "@langchain/macaw-components/Icon"
import { LaptopRegularIcon } from "@langchain/macaw-components/icons"

const REQUESTS_QUERY = "local-access-requests"

const VERBS: Record<string, string> = {
  read: "read a file",
  write: "write a file",
  import: "copy a file into its sandbox",
  execute: "run a command",
}

/**
 * The thread's local actions waiting on the person. Deciding happens in the desktop
 * app's own approval panel, which Review opens; this page can't answer a request.
 * The panel stays away while the person is writing in the composer, so a keystroke
 * can't land on it.
 */
export function LocalAccessRequests({
  threadId,
  composerWriting,
}: {
  threadId: string
  composerWriting: boolean
}) {
  const desktop = window.openSweDesktop
  const embedded = useContext(EmbeddedThreadContext)
  const collapsed = useSidebarCollapsed()
  const navigate = useNavigate()
  const region = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!desktop?.setLocalAccessThread) return
    const thread = region.current?.closest<HTMLElement>("[data-chat-drop-zone]")
    if (!thread) return
    const update = () => {
      if (
        composerWriting ||
        embedded ||
        (!collapsed && window.innerWidth < 768)
      ) {
        void desktop.setLocalAccessThread?.(threadId, null)
        return
      }
      const { x, y, width, height } = thread.getBoundingClientRect()
      void desktop.setLocalAccessThread?.(threadId, { x, y, width, height })
    }
    const observer = new ResizeObserver(update)
    observer.observe(thread)
    window.addEventListener("resize", update)
    update()
    return () => {
      observer.disconnect()
      window.removeEventListener("resize", update)
      void desktop.setLocalAccessThread?.(threadId, null)
    }
  }, [collapsed, composerWriting, desktop, embedded, threadId])
  const queryClient = useQueryClient()
  const requests = useQuery({
    queryKey: [REQUESTS_QUERY, threadId],
    queryFn: () =>
      desktop?.localAccessRequests?.(threadId) ?? Promise.resolve([]),
    enabled: Boolean(desktop?.localAccessRequests),
  })
  // Requests start and end in the desktop app, which says when they change.
  useEffect(
    () =>
      desktop?.onLocalAccessRequestsChanged?.(
        () =>
          void queryClient.invalidateQueries({
            queryKey: [REQUESTS_QUERY, threadId],
          })
      ),
    [desktop, queryClient, threadId]
  )
  const pending = requests.data ?? []
  return (
    <div
      ref={region}
      className={
        pending.length
          ? "mx-auto flex w-full max-w-3xl shrink-0 flex-col gap-space-2 px-space-4 pt-space-3"
          : "contents"
      }
    >
      {pending.map((request) => (
        <Banner
          key={request.id}
          intent={request.concern ? "warning" : "info"}
          icon={
            <Icon
              icon={LaptopRegularIcon}
              size="md"
              className="text-icon-brand"
            />
          }
          action={
            <Button
              color="primary"
              size="md"
              onClick={() => {
                if (embedded) {
                  void navigate({
                    to: "/agents/$threadId",
                    params: { threadId },
                  })
                } else {
                  void desktop?.reviewLocalAccessRequest?.(request.id)
                }
              }}
            >
              {embedded ? "Open thread to review" : "Review"}
            </Button>
          }
        >
          Robin is waiting for your OK to {VERBS[request.kind] ?? "act"} on this
          Mac
          {request.concern
            ? `. Its reviewer wants you to look: ${request.concern}.`
            : "."}
        </Banner>
      ))}
    </div>
  )
}
