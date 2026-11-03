import { Button } from "@langchain/macaw-components/Button"
import { CaretDownIcon } from "@langchain/macaw-components/icons"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@langchain/macaw-components/Popover"
import { Text } from "@langchain/macaw-components/Text"
import { FolderIcon } from "@phosphor-icons/react/dist/ssr/Folder"
import { GithubLogoIcon } from "@phosphor-icons/react/dist/ssr/GithubLogo"

import type { SessionUser } from "@/lib/api"
import { LinkGitHubButton } from "@/features/settings/components/ConnectionsSection"

/**
 * Stands in for the composer's repository picker until the signed-in person
 * links GitHub: choosing a repository asks them to link it first.
 */
export function GitHubLinkPrompt({
  user,
  side = "bottom",
}: {
  user: SessionUser
  side?: "top" | "bottom"
}) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          color="secondary"
          variant="plain"
          size="sm"
          type="button"
          aria-label="Select repository"
          className="max-w-[260px] cursor-pointer gap-space-1 text-secondary transition-opacity hover:opacity-80"
        >
          <FolderIcon size={14} weight="regular" className="shrink-0" />
          <span className="flex-1 truncate text-left">Select repository</span>
          <CaretDownIcon
            size={12}
            weight="regular"
            className="shrink-0 opacity-70"
          />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        side={side}
        sideOffset={7}
        className="flex w-72 flex-col items-center gap-space-3 p-space-4 text-center"
      >
        <GithubLogoIcon size={24} weight="regular" aria-hidden />
        <div className="flex flex-col gap-space-1">
          <Text as="h2" variant="sm" weight="medium">
            Link GitHub to work in a repository
          </Text>
          <Text as="p" variant="xs" color="secondary">
            Agents clone repositories, push branches and open pull requests as
            you. Without a repository, they work in a task folder of their own.
          </Text>
        </div>
        <LinkGitHubButton user={user} size="sm" />
      </PopoverContent>
    </Popover>
  )
}
