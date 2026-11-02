import { Button } from "@langchain/macaw-components/Button"
import type { FrontendReviewRun, FrontendScenario } from "@openswe/plugin-sdk"
import { usePluginHost } from "@openswe/plugin-sdk/react"
import { useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"

import {
  askDraft,
  chatName,
  clip,
  MAX_IMAGE_BYTES,
  scenarioContext,
  type Attachment,
} from "../lib/ask"
import {
  pullRequestLabel,
  repositoryOf,
  type PullRequestRef,
} from "../lib/pullRequest"
import { errorMessage, isForbidden, keys, readMedia } from "../lib/queries"

/**
 * Opens the pull request's chat in the agent dock with a question about one
 * scenario, its original screenshots attached, for the person to edit and send.
 */
export function AskAboutChange({
  pr,
  run,
  scenario,
  currentHeadSha,
}: {
  pr: PullRequestRef
  run: FrontendReviewRun
  scenario: FrontendScenario
  currentHeadSha: string | null | undefined
}) {
  const host = usePluginHost()
  const queryClient = useQueryClient()
  const [loading, setLoading] = useState(false)
  // A late answer must not open the dock once the person has moved on.
  const mounted = useRef(false)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  const ask = async () => {
    const repository = repositoryOf(pr)
    const media = { repository, pull: pr.number, run: run.id }
    setLoading(true)
    try {
      const sides = [
        ["Base", scenario.before_url],
        ["PR", scenario.after_url],
      ] as const
      const screenshots: Attachment[] = await Promise.all(
        sides.flatMap(([side, name]) =>
          name
            ? [
                queryClient
                  .fetchQuery({
                    queryKey: keys.media(media, name),
                    queryFn: () =>
                      readMedia(host.frontendReviews(repository), media, name),
                    staleTime: Infinity,
                  })
                  .then((blob) => ({
                    side,
                    blob,
                    name: `design-qa-${run.id}-${scenario.id}-${side.toLowerCase()}.png`,
                  })),
              ]
            : []
        )
      )
      if (!mounted.current) return
      const attached = screenshots.filter(
        ({ blob }) => blob.size <= MAX_IMAGE_BYTES
      )
      const tooLarge = screenshots.filter(
        ({ blob }) => blob.size > MAX_IMAGE_BYTES
      )
      await host.agent.use({ name: chatName(pr), repository })
      await host.agent.share(
        scenarioContext(pr, run, scenario, currentHeadSha),
        { label: clip(`${pullRequestLabel(pr)} · ${scenario.title}`, 120) }
      )
      if (!mounted.current) return
      await host.agent.open({
        draft: askDraft({
          pr,
          run,
          scenario,
          currentHeadSha,
          attached,
          tooLarge,
        }),
        images: attached.map(({ name, blob }) => ({ name, blob })),
      })
    } catch (error) {
      if (!mounted.current) return
      if (isForbidden(error))
        void queryClient.invalidateQueries({ queryKey: keys.runs(pr) })
      void host.ui.toast({
        title: "Could not attach Design QA evidence",
        description: errorMessage(error),
        intent: "error",
      })
    }
    if (mounted.current) setLoading(false)
  }

  return (
    <Button
      size="sm"
      color="secondary"
      variant="outlined"
      disabled={loading || (!scenario.before_url && !scenario.after_url)}
      title="Attach the saved screenshots and replay steps to the agent. Review before sending."
      onClick={() => void ask()}
    >
      {loading ? "Attaching evidence…" : "Ask about this change"}
    </Button>
  )
}
