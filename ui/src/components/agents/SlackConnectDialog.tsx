import { Dialog } from "@base-ui/react/dialog"
import { useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { IoLogoSlack } from "react-icons/io5"

import { Button } from "@/components/ui/button"
import { api, slackConnectUrl } from "@/lib/api"
import { useSession } from "@/lib/session"

/**
 * Modal shown on first login (and until connected) prompting the user to link
 * Slack. ``open`` is derived from the mapping query, so it appears once the data
 * resolves to "not connected" and closes itself once Slack is linked; dismissing
 * it hides it for the session.
 */
export function SlackConnectDialog() {
  const session = useSession()
  const mapping = useQuery({ queryKey: ["myMapping"], queryFn: api.myMapping })
  const [dismissed, setDismissed] = useState(false)

  const slackEnabled = session.data?.slack_oauth_enabled ?? false
  const connected = !!mapping.data?.slack_user_id
  const shouldShow =
    slackEnabled && !connected && !mapping.isLoading && !mapping.isError
  const open = shouldShow && !dismissed

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!next) setDismissed(true)
      }}
    >
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/50 data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0" />
        <Dialog.Popup className="fixed top-1/2 left-1/2 z-50 w-[min(28rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-lg bg-popover p-6 text-popover-foreground shadow-md ring-1 ring-foreground/10 data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95">
          <div className="flex flex-col gap-4">
            <div className="flex items-center gap-3">
              <IoLogoSlack className="size-6 shrink-0 text-muted-foreground" />
              <Dialog.Title className="text-base font-semibold">
                Connect your Slack account
              </Dialog.Title>
            </div>
            <Dialog.Description className="text-sm text-muted-foreground">
              Link Slack so Open SWE can act as you when you tag it — it opens
              pull requests and replies on your behalf. We use the email Slack
              verifies, which also lets Linear mentions resolve to you.
            </Dialog.Description>
            <div className="mt-2 flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setDismissed(true)}
              >
                Maybe later
              </Button>
              <Button
                size="sm"
                onClick={() => window.location.assign(slackConnectUrl())}
              >
                <IoLogoSlack className="size-4" />
                Connect Slack
              </Button>
            </div>
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
