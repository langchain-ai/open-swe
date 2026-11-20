import { Button } from "@langchain/macaw-components/Button"
import { usePluginHost } from "@openswe/plugin-sdk/react"
import { useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"

import {
  askDraft,
  chatName,
  MAX_IMAGE_BYTES,
  type Attachment,
} from "../lib/ask"
import type {
  FrontendReviewRun,
  FrontendScenario,
  ScenarioShots,
} from "../lib/capture"
import { PixelChange } from "../lib/pixelChange"
import { repositoryOf, type PullRequestRef } from "../lib/pullRequest"
import { errorMessage, isForbidden, keys, useDesignQa } from "../lib/queries"
import { useUsage } from "../lib/usage"

/**
 * Opens the pull request's chat in the agent dock with a question about one
 * scenario, its original screenshots in the theme shown attached, for the
 * person to edit and send.
 * `onAsk` lets the page narrow what it shares with the agent to the scenario.
 */
export function AskAboutChange({
  pr,
  run,
  scenario,
  shots,
  currentHeadSha,
  onAsk,
}: {
  pr: PullRequestRef
  run: FrontendReviewRun
  scenario: FrontendScenario
  shots: ScenarioShots
  currentHeadSha: string | null | undefined
  onAsk: () => void
}) {
  const host = usePluginHost()
  const record = useUsage()
  const qa = useDesignQa()
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
        ["Base", shots.before],
        ["PR", shots.after],
      ] as const
      const screenshots: Attachment[] = await Promise.all(
        sides.flatMap(([side, name]) =>
          name
            ? [
                queryClient
                  .fetchQuery({
                    queryKey: keys.media(media, name),
                    queryFn: () => qa.media(media, name),
                    staleTime: Infinity,
                  })
                  .then((blob) => ({
                    side,
                    blob,
                    // Robin may keep a PNG as a WebP, so the name follows the file's type.
                    name: `design-qa-${run.id}-${scenario.id}-${side.toLowerCase()}${shots.theme === "dark" ? "-dark" : ""}.${blob.type.split("/")[1] || "png"}`,
                  })),
              ]
            : []
        )
      )
      const [base, head] = [
        screenshots.find(({ side }) => side === "Base")?.blob,
        screenshots.find(({ side }) => side === "PR")?.blob,
      ]
      const change =
        base && head && shots.before && shots.after
          ? await queryClient
              .fetchQuery({
                queryKey: keys.change(media, shots.before, shots.after),
                queryFn: () => PixelChange.between(base, head),
                staleTime: Infinity,
              })
              // A pair the page can't decode still goes to the agent.
              .catch(() => null)
          : null
      if (!mounted.current) return
      const attached = screenshots.filter(
        ({ blob }) => blob.size <= MAX_IMAGE_BYTES
      )
      const tooLarge = screenshots.filter(
        ({ blob }) => blob.size > MAX_IMAGE_BYTES
      )
      await host.agent.use({ name: chatName(pr), repository })
      if (!mounted.current) return
      onAsk()
      await host.agent.open({
        draft: askDraft({
          pr,
          run,
          scenario,
          shots,
          currentHeadSha,
          attached,
          tooLarge,
          change,
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
      disabled={loading || (!shots.before && !shots.after)}
      title="Attach the saved screenshots and replay steps to the agent. Review before sending."
      onClick={() => {
        record("ask_opened")
        void ask()
      }}
    >
      {loading ? "Attaching evidence…" : "Ask about this change"}
    </Button>
  )
}
