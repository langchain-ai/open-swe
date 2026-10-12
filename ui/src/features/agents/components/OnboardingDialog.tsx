import { Button } from "@langchain/macaw-components/Button"
import { Dialog, DialogContent } from "@langchain/macaw-components/Dialog"
import { SlackLogoIcon } from "@phosphor-icons/react/dist/ssr/SlackLogo"

import { connectService } from "@/lib/api"
import { useDismissSlackOnboarding, useProfile } from "@/lib/profile"
import { useSession } from "@/lib/session"

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
      <DialogContent
        title="Connect your Slack account"
        titleIcon={SlackLogoIcon}
        description="Connect Slack so that when you tag Open SWE, it can resolve your GitHub account. We use the email Slack verifies, which also lets Linear mentions resolve to you."
        showClose={false}
        className="w-[min(28rem,calc(100vw-2rem))]"
      >
        <div className="flex justify-end gap-space-2">
          <Button
            color="secondary"
            variant="outlined"
            onClick={() => dismiss.mutate()}
          >
            Don't ask again
          </Button>
          <Button
            leftDecorator={SlackLogoIcon}
            onClick={() =>
              void connectService("slack")?.finally(
                () => void session.refetch()
              )
            }
          >
            Connect Slack
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
