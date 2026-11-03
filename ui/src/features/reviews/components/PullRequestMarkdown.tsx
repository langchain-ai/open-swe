import { useCallback } from "react"

import { Markdown } from "@/features/agents/components/chat/Markdown"
import type { PullRequestRef } from "@/features/reviews/lib/reviewKeys"
import { reviewImageProxyUrl } from "@/lib/api"

export function PullRequestMarkdown({
  pr: { owner, repo, number },
  content,
}: {
  pr: PullRequestRef
  content: string
}) {
  const proxyImage = useCallback(
    (src: string) => reviewImageProxyUrl(owner, repo, number, src),
    [owner, repo, number]
  )
  return (
    <Markdown content={content} transformImageUrl={proxyImage} enlargeImages />
  )
}
