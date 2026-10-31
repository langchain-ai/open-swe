import { IconButton } from "@langchain/macaw-components/IconButton"
import { BookOpenTextIcon } from "@phosphor-icons/react/dist/ssr/BookOpenText"
import { GithubLogoIcon } from "@phosphor-icons/react/dist/ssr/GithubLogo"
import { useNavigate } from "@tanstack/react-router"

import type { ReviewPageRef } from "@/features/agents/lib/types"
import { reviewPageRoute } from "@/features/reviews/lib/reviewEntry"
import { useAltKey } from "@/lib/useAltKey"

/** Opens a pull request on our review page, or on GitHub while Alt (Option) is held. */
export function OpenPullRequestButton({
  pullRequest,
  onNavigate,
}: {
  pullRequest: { url: string; review: ReviewPageRef }
  onNavigate?: () => void
}) {
  const navigate = useNavigate()
  const github = useAltKey()
  return (
    <IconButton
      icon={github ? GithubLogoIcon : BookOpenTextIcon}
      label={github ? "Open PR on GitHub" : "Open PR review"}
      tooltipProps={{
        title: github
          ? "Open PR on GitHub"
          : "Open PR review (hold ⌥ Option / Alt for GitHub)",
      }}
      size="xs"
      color="secondary"
      variant="plain"
      onClick={(event) => {
        event.preventDefault()
        event.stopPropagation()
        if (event.altKey || github) {
          window.open(pullRequest.url, "_blank", "noopener,noreferrer")
          return
        }
        onNavigate?.()
        void navigate(reviewPageRoute(pullRequest.review))
      }}
    />
  )
}
