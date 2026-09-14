import { useQuery } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { ArrowUpRight } from "lucide-react"

import { PlanArtifactFrame } from "@/features/agents/components/PlanArtifactFrame"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { getPlan } from "@/lib/plan"

export function InlinePlanArtifact({
  threadId,
  html,
  markdown,
}: {
  threadId: string
  html?: string
  markdown?: string
}) {
  const navigate = useNavigate()
  const shouldFetch = html === undefined && markdown === undefined
  const query = useQuery({
    queryKey: ["plan", threadId],
    queryFn: () => getPlan(threadId),
    enabled: shouldFetch,
  })
  const resolvedHtml = html?.trim() ?? query.data?.html.trim() ?? ""
  const resolvedMarkdown = markdown?.trim() ?? query.data?.markdown.trim() ?? ""
  if (!resolvedHtml && !resolvedMarkdown) return null

  return (
    <button
      type="button"
      data-testid="inline-plan-artifact"
      aria-label="Open plan in the conversation"
      onClick={() =>
        void navigate({
          to: "/agents/$threadId/plan",
          params: { threadId },
        })
      }
      className="group relative my-4 block h-[250px] w-full overflow-hidden rounded-xl border border-border bg-background text-left shadow-sm transition-[border-color,box-shadow] outline-none hover:border-foreground/25 hover:shadow-md focus-visible:ring-2 focus-visible:ring-ring"
    >
      {resolvedHtml ? (
        <PlanArtifactFrame
          html={resolvedHtml}
          title="Plan preview"
          className="pointer-events-none h-[250px]"
        />
      ) : (
        <div className="pointer-events-none h-[250px] overflow-hidden p-5">
          <Markdown content={resolvedMarkdown} />
        </div>
      )}
      <span
        data-testid="inline-plan-fade"
        className="pointer-events-none absolute inset-x-0 bottom-0 flex h-24 items-end justify-end bg-linear-to-b from-transparent via-background/75 to-background p-3"
      >
        <span className="inline-flex items-center gap-1 rounded-md bg-foreground px-2.5 py-1.5 text-xs font-medium text-background shadow-sm">
          Open plan
          <ArrowUpRight className="size-3.5" />
        </span>
      </span>
    </button>
  )
}
