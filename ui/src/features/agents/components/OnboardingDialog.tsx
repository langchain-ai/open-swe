import { ProviderMark } from "@langchain/gtm-platform-design-system/patterns/provider-mark"
import { Inline } from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@langchain/gtm-platform-design-system/ui/dialog"

import { connectService } from "@/lib/api"
import { useDismissSlackOnboarding, useProfile } from "@/lib/profile"
import { useSession } from "@/lib/session"

/*
 * Not the system's OnboardingDialog: that pattern teaches a surface and treats
 * Skip and finish as the same outcome, while this asks for one connection and
 * keeps "Don't ask again" distinct from "Connect Slack".
 */
/** Prompt unlinked users to connect Slack. */
export function OnboardingDialog() {
  const session = useSession()
  const profile = useProfile()
  const dismiss = useDismissSlackOnboarding()

  const slackEnabled = session.data?.slack_oauth_enabled ?? false
  const slackConnected = !!session.data?.slack_user_id
  const needsSlack =
    slackEnabled &&
    !slackConnected &&
    profile.isSuccess &&
    !profile.data.slack_onboarding_dismissed &&
    !session.isLoading &&
    !session.isError
  return (
    <Dialog
      open={needsSlack}
      onOpenChange={(next) => {
        if (!next) dismiss.mutate()
      }}
    >
      <DialogContent showCloseButton={false} className="sm:max-w-md">
        <DialogHeader>
          <Inline gap="sm" align="center">
            <ProviderMark provider="slack" />
            <DialogTitle className="text-title">
              Connect your Slack account
            </DialogTitle>
          </Inline>
          <DialogDescription>
            Connect Slack so that when you tag Open SWE, it can resolve your
            GitHub account. We use the email Slack verifies, which also lets
            Linear mentions resolve to you.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="ghost" onClick={() => dismiss.mutate()}>
            Don't ask again
          </Button>
          <Button
            onClick={() =>
              void connectService("slack")?.finally(
                () => void session.refetch()
              )
            }
          >
            Connect Slack
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
