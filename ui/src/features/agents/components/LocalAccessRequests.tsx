import { useEffect } from "react"
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
 */
export function LocalAccessRequests({ threadId }: { threadId: string }) {
  const desktop = window.openSweDesktop
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
  if (!pending.length) return null
  return (
    <div className="mx-auto flex w-full max-w-3xl shrink-0 flex-col gap-space-2 px-space-4 pt-space-3">
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
              onClick={() =>
                void desktop?.reviewLocalAccessRequest?.(request.id)
              }
            >
              Review
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
