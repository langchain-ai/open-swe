import { ArrowLeftIcon } from "@phosphor-icons/react"
import { Link, useRouterState } from "@tanstack/react-router"

import { sectionOf, useHrefLinkOptions } from "@/lib/appLocation"

export function PullRequestBackLink() {
  const href = useRouterState({
    select: (state) => state.location.state.pullRequestBackLink,
  })
  const linkOptions = useHrefLinkOptions()
  if (!href || sectionOf(href.split(/[?#]/, 1)[0] ?? "") !== "/agents/reviews")
    return null

  return (
    <Link
      {...linkOptions(href)}
      data-no-drag=""
      className="flex h-7 shrink-0 items-center gap-1 rounded-md px-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
    >
      <ArrowLeftIcon className="size-3.5" aria-hidden />
      Pull Requests
    </Link>
  )
}
