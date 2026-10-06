import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { ArrowUpRight, X } from "lucide-react"

import { PlanArtifactFrame } from "@/features/agents/components/PlanArtifactFrame"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { dismissPlan, getPlan, type PlanData } from "@/lib/plan"

export function InlinePlanArtifact({ threadId }: { threadId: string }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const queryKey = ["plan", threadId]
  const query = useQuery({
    queryKey,
    queryFn: () => getPlan(threadId),
  })
  const dismiss = useMutation({
    mutationFn: () => dismissPlan(threadId),
    meta: { errorTitle: "Couldn't dismiss the plan" },
    onSuccess: () =>
      queryClient.setQueryData<PlanData>(queryKey, (plan) =>
        plan ? { ...plan, dismissed: true } : plan
      ),
  })
  const html = query.data?.html.trim() ?? ""
  const markdown = query.data?.markdown.trim() ?? ""
  if ((!html && !markdown) || query.data?.dismissed) return null

  return (
    <div className="group relative mt-4">
      <button
        type="button"
        data-testid="inline-plan-artifact"
        aria-label="Open artifact in the conversation"
        onClick={() =>
          void navigate({
            to: "/agents/$threadId/plan",
            params: { threadId },
          })
        }
        className="block h-[250px] w-full overflow-hidden rounded-control border border-line bg-canvas text-left shadow-control transition-[border-color,box-shadow] outline-none hover:border-ink/25 hover:shadow-popup focus-visible:ring-2 focus-visible:ring-primary"
      >
        {html ? (
          <PlanArtifactFrame
            html={html}
            title="Artifact preview"
            className="pointer-events-none h-[250px]"
          />
        ) : (
          <div className="pointer-events-none h-[250px] overflow-hidden p-5">
            <Markdown content={markdown} />
          </div>
        )}
        <span
          data-testid="inline-plan-fade"
          className="pointer-events-none absolute inset-x-0 bottom-0 flex h-24 items-end justify-end bg-linear-to-b from-transparent via-canvas/75 to-canvas p-3"
        >
          <span className="inline-flex items-center gap-1 rounded-badge bg-ink px-2.5 py-1.5 text-label font-medium text-canvas shadow-control">
            Open artifact
            <ArrowUpRight className="size-3.5" />
          </span>
        </span>
      </button>
      <button
        type="button"
        aria-label="Dismiss artifact"
        disabled={dismiss.isPending}
        onClick={() => dismiss.mutate()}
        className="absolute top-2 right-2 z-10 inline-flex size-7 items-center justify-center rounded-full border border-line bg-canvas/90 text-ink-subtle shadow-control backdrop-blur-sm transition-colors hover:text-ink focus-visible:ring-2 focus-visible:ring-primary"
      >
        <X className="size-3.5" />
      </button>
    </div>
  )
}
