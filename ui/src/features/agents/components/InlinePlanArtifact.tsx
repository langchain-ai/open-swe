import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { ArrowUpRight, X } from "lucide-react"

import { PlanArtifactFrame } from "@/features/agents/components/PlanArtifactFrame"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { dismissPlan, getPlan, type PlanData } from "@/lib/plan"

export function InlinePlanArtifact({ threadId }: { threadId: string }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const queryKey = ["plan", threadId] as const
  const query = useQuery({
    queryKey,
    queryFn: () => getPlan(threadId),
  })
  const dismiss = useMutation({
    mutationFn: () => dismissPlan(threadId),
    onSuccess: () =>
      queryClient.setQueryData<PlanData>(queryKey, (plan) =>
        plan ? { ...plan, dismissed: true } : plan
      ),
  })
  const html = query.data?.html.trim() ?? ""
  const markdown = query.data?.markdown.trim() ?? ""
  const planVersion = html || markdown

  if (!planVersion || query.data?.dismissed) return null

  return (
    <div className="group relative mt-4">
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
        className="block h-[250px] w-full overflow-hidden rounded-xl border border-border bg-background text-left shadow-sm transition-[border-color,box-shadow] outline-none hover:border-foreground/25 hover:shadow-md focus-visible:ring-2 focus-visible:ring-ring"
      >
        {html ? (
          <PlanArtifactFrame
            html={html}
            title="Plan preview"
            className="pointer-events-none h-[250px]"
          />
        ) : (
          <div className="pointer-events-none h-[250px] overflow-hidden p-5">
            <Markdown content={markdown} />
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
      <button
        type="button"
        aria-label="Dismiss plan"
        disabled={dismiss.isPending}
        onClick={() => dismiss.mutate()}
        className="absolute top-2 right-2 z-10 inline-flex size-7 items-center justify-center rounded-full border border-border bg-background/90 text-muted-foreground shadow-sm backdrop-blur-sm transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
      >
        <X className="size-3.5" />
      </button>
    </div>
  )
}
