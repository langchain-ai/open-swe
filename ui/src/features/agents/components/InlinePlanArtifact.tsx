import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { ArrowUpRightIcon } from "@phosphor-icons/react/dist/ssr/ArrowUpRight"
import { XIcon } from "@phosphor-icons/react/dist/ssr/X"

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
        className="block h-[250px] w-full overflow-hidden rounded-xl border border-default bg-surface-level-1 text-left shadow-sm transition-shadow outline-none hover:shadow-md focus-visible:ring-2 focus-visible:ring-focus"
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
          className="pointer-events-none absolute inset-x-0 bottom-0 flex h-24 items-end justify-end bg-linear-to-b from-transparent via-surface-level-1/75 to-surface-level-1 p-3"
        >
          <span className="inline-flex items-center gap-space-1 rounded-md bg-brand px-2.5 py-1.5 text-xs font-medium text-brand-on-fill shadow-sm">
            Open artifact
            <ArrowUpRightIcon size={14} weight="regular" />
          </span>
        </span>
      </button>
      <IconButton
        icon={XIcon}
        label="Dismiss artifact"
        round
        color="secondary"
        variant="outlined"
        disabled={dismiss.isPending}
        onClick={() => dismiss.mutate()}
        className="absolute top-2 right-2 z-10"
      />
    </div>
  )
}
