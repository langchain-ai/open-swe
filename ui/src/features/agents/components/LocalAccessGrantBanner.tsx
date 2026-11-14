import { useEffect } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { Icon } from "@langchain/macaw-components/Icon"
import { LaptopRegularIcon } from "@langchain/macaw-components/icons"
import { toast } from "sonner"

type Grant = { folder: string; until: number }

const GRANTS_QUERY = "local-access-grants"

/**
 * The folders this thread may act in on this Mac without asking, from the desktop
 * app's own grants, with a way to end them. Shows nothing outside the desktop app.
 */
export function LocalAccessGrantBanner({ threadId }: { threadId: string }) {
  const desktop = window.openSweDesktop
  const queryClient = useQueryClient()
  const grants = useQuery({
    queryKey: [GRANTS_QUERY, threadId],
    queryFn: () =>
      desktop?.localAccessGrants?.(threadId) ?? Promise.resolve<Grant[]>([]),
    enabled: Boolean(desktop?.localAccessGrants),
  })
  // Grants start in a native dialog and end on a timer: the desktop says when they change.
  useEffect(
    () =>
      desktop?.onLocalAccessGrantsChanged?.(
        () =>
          void queryClient.invalidateQueries({
            queryKey: [GRANTS_QUERY, threadId],
          })
      ),
    [desktop, queryClient, threadId]
  )
  const revoke = useMutation({
    mutationFn: () =>
      desktop?.revokeLocalAccessGrants?.(threadId) ?? Promise.resolve(),
    onMutate: () => {
      const previous = queryClient.getQueryData<Grant[]>([
        GRANTS_QUERY,
        threadId,
      ])
      queryClient.setQueryData<Grant[]>([GRANTS_QUERY, threadId], [])
      return { previous }
    },
    onError: (_error, _vars, context) => {
      queryClient.setQueryData([GRANTS_QUERY, threadId], context?.previous)
      toast.error("Could not end local access for this thread.")
    },
  })
  // The desktop lists live grants only, and says when one expires.
  const live = grants.data ?? []
  if (!live.length) return null
  const folders = live.map((grant) => grant.folder)
  const until = new Date(Math.max(...live.map((grant) => grant.until)))
  return (
    <div className="mx-auto w-full max-w-3xl shrink-0 px-space-4 pt-space-3">
      <Banner
        intent="info"
        icon={
          <Icon
            icon={LaptopRegularIcon}
            size="md"
            className="text-icon-brand"
          />
        }
        action={
          <Button
            color="secondary"
            variant="underlined"
            size="md"
            onClick={() => revoke.mutate()}
          >
            Revoke
          </Button>
        }
      >
        Until{" "}
        {until.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })},
        Robin can change files in {folders.join(", ")} on this Mac and run
        commands there that its reviewer finds routine, without asking. Commands
        can reach the rest of your Mac; anything flagged still asks.
      </Banner>
    </div>
  )
}
