import { Card } from "@langchain/macaw-components/Card"
import { Text } from "@langchain/macaw-components/Text"
import { GithubLogoIcon } from "@phosphor-icons/react/dist/ssr/GithubLogo"

import type { SessionUser } from "@/lib/api"
import { LinkGitHubButton } from "@/features/settings/components/ConnectionsSection"

/** Stands in for the coding features until the signed-in person links GitHub. */
export function GitHubLinkPrompt({ user }: { user: SessionUser }) {
  return (
    <div className="flex flex-1 items-center justify-center p-space-5">
      <Card
        intent="plain"
        className="flex w-full max-w-md flex-col items-center gap-space-4 p-space-6 text-center"
      >
        <GithubLogoIcon size={32} weight="regular" aria-hidden />
        <div className="flex flex-col gap-space-2">
          <Text as="h1" variant="h3">
            Link GitHub to start coding
          </Text>
          <Text as="p" variant="sm" color="secondary">
            Coding agents clone repositories, push branches and open pull
            requests as you. Link your GitHub account to use them and return to
            your existing coding threads.
          </Text>
        </div>
        <LinkGitHubButton user={user} size="md" />
      </Card>
    </div>
  )
}
