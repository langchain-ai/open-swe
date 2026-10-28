import { Button } from "@langchain/macaw-components/Button"
import { useEffect, useRef, useState } from "react"
import { toast } from "sonner"

import { materializeImages } from "@/features/agents/lib/queuedMessages"
import { useReviewPage } from "@/features/reviews/page/store"
import {
  evidenceMediaUrl,
  type FrontendReviewRun,
  type FrontendScenario,
} from "./api"

interface EvidenceSelection {
  run: FrontendReviewRun
  scenario: FrontendScenario
  currentHeadSha?: string | null
}

async function prepareDraft(
  { run, scenario, currentHeadSha }: EvidenceSelection,
  signal: AbortSignal
) {
  const screenshots = [
    { side: "Base", url: scenario.before_url },
    { side: "PR", url: scenario.after_url },
  ].flatMap(({ side, url }) =>
    url
      ? [
          {
            side,
            url: evidenceMediaUrl(url),
            fileName: `design-qa-${run.id}-${scenario.id}-${side.toLowerCase()}.png`,
          },
        ]
      : []
  )
  const { images, failed } = await materializeImages(
    screenshots.map(({ url, fileName }) => ({
      kind: "image",
      url,
      fileName,
      credentials: "session",
    })),
    signal
  )
  if (failed || images.length === 0)
    throw new Error("Could not load all selected screenshots. Try again.")

  const reportUrl = new URL(window.location.pathname, window.location.origin)
  reportUrl.searchParams.set("view", "frontend")
  reportUrl.searchParams.set("run", run.id)
  const steps = scenario.actions ?? []
  const details = [
    `Design QA: ${scenario.title}`,
    `Route: ${scenario.path} · ${scenario.width ?? "?"} × ${scenario.height ?? "?"}`,
    `Checkpoint: ${scenario.id} · ${scenario.interaction ? "Interaction state" : "Page capture"} · ${scenario.status}`,
    `Report: ${reportUrl.href}`,
    `Base commit: ${run.base_sha}`,
    `PR commit: ${run.head_sha}`,
    currentHeadSha && currentHeadSha !== run.head_sha
      ? `Stale capture: the current PR commit is ${currentHeadSha}.`
      : null,
    `Build verification: ${run.report?.build_verification ?? "unverified"}`,
    `Attached screenshots: ${screenshots.map(({ side, fileName }) => `${side} (${fileName})`).join("; ")}`,
    !scenario.before_url ? "Base screenshot: not captured." : null,
    !scenario.after_url ? "PR screenshot: not captured." : null,
    steps.length
      ? `Planned replay steps:\n${steps.map((step, index) => `${index + 1}. ${step}`).join("\n")}`
      : "Interaction replay: not captured.",
    scenario.before_error
      ? `Base capture blocker: ${scenario.before_error}`
      : null,
    scenario.after_error ? `PR capture blocker: ${scenario.after_error}` : null,
    scenario.error ? `Capture blocker: ${scenario.error}` : null,
    ...(scenario.layout_warnings ?? []).map(
      (warning) => `Potential layout issue: ${warning}`
    ),
    scenario.metrics
      ? `Recorded pixel change: ${(scenario.metrics.changed_ratio * 100).toFixed(2)}% (${scenario.metrics.changed_pixels} pixels; threshold ${scenario.metrics.threshold}).`
      : null,
    ...scenario.annotations.map(
      (annotation) => `Review note: ${annotation.title}: ${annotation.body}`
    ),
    "These are the original saved screenshots; viewer overlays, local masks, and crops are not included. Planned steps alone do not establish successful coverage.",
  ].filter(Boolean)
  return {
    text: `Can you review this Design QA change and its interaction state?\n\n${details.join("\n\n")}`,
    images,
  }
}

export function AskAboutChange(selection: EvidenceSelection) {
  const { scenario } = selection
  const [loading, setLoading] = useState(false)
  const pending = useRef<AbortController | null>(null)
  useEffect(() => () => pending.current?.abort(), [])

  const ask = async () => {
    if (pending.current) return
    const controller = new AbortController()
    pending.current = controller
    setLoading(true)
    try {
      const draft = await prepareDraft(selection, controller.signal)
      if (!controller.signal.aborted)
        useReviewPage.getState().askInChat(draft.text, draft.images)
    } catch (error) {
      if (!controller.signal.aborted)
        toast.error("Could not attach Design QA evidence", {
          description: error instanceof Error ? error.message : "Try again.",
        })
    }
    if (!controller.signal.aborted) {
      pending.current = null
      setLoading(false)
    }
  }

  return (
    <Button
      size="sm"
      color="secondary"
      variant="outlined"
      disabled={loading || (!scenario.before_url && !scenario.after_url)}
      title="Attach the saved screenshots and replay steps to Chat. Review before sending."
      onClick={() => void ask()}
    >
      {loading ? "Attaching evidence…" : "Ask about this change"}
    </Button>
  )
}
