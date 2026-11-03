import { Card } from "@langchain/macaw-components/Card"
import { Button } from "@langchain/macaw-components/Button"
import { useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { productAnalytics } from "@/lib/posthog"
import { clearCachedRepos } from "@/lib/repoCache"
import { api } from "@/lib/api"
import { Text } from "@langchain/macaw-components/Text"
import { GithubLogoIcon } from "@phosphor-icons/react/dist/ssr/GithubLogo"

import type { SessionUser } from "@/lib/api"
import { LinkGitHubButton } from "@/features/settings/components/ConnectionsSection"

/** Blocks access until the signed-in person links GitHub. */
export function GitHubLinkPrompt({ user }: { user: SessionUser }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  return (
    <div className="flex flex-1 items-center justify-center p-space-5">
      <Card
        intent="plain"
        className="flex w-full max-w-md flex-col items-center gap-space-4 p-space-6 text-center"
      >
        <GithubLogoIcon size={32} weight="regular" aria-hidden />
        <div className="flex flex-col gap-space-2">
          <Text as="h1" variant="h3">
            Link GitHub to use Open SWE
          </Text>
          <Text as="p" variant="sm" color="secondary">
            GitHub is required to use Open SWE. If you've used Open SWE before,
            link the GitHub account you used then to return to your existing
            account, threads, and settings.
          </Text>
        </div>
        <LinkGitHubButton user={user} size="md" />
        <Button
          color="secondary"
          variant="plain"
          size="sm"
          onClick={async () => {
            await api.logout()
            productAnalytics.reset()
            clearCachedRepos()
            qc.setQueryData(["session"], null)
            navigate({ to: "/login" })
          }}
        >
          Sign out
        </Button>
      </Card>
    </div>
  )
}
