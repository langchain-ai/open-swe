import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"

import { PlanArtifactFrame } from "@/features/agents/components/PlanArtifactFrame"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { Box } from "@langchain/gtm-platform-design-system/ui/box"
import {
  Button,
  buttonVariants,
} from "@langchain/gtm-platform-design-system/ui/button"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { ArrowUpRight, X } from "@/components/glyphs"
import { dismissPlan, getPlan, type PlanData } from "@/lib/plan"
import { cn } from "@/lib/utils"

/** The preview window; the artifact itself opens on its own route. */
const PREVIEW_HEIGHT_CLASS = "h-64"

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
    <Box className="group relative">
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
        className={cn(
          PREVIEW_HEIGHT_CLASS,
          "block w-full cursor-pointer overflow-hidden rounded-panel border border-line bg-canvas text-left shadow-control transition-[border-color,box-shadow] duration-fast ease-out-quint outline-none hover:border-line-strong hover:shadow-raised-hover focus-visible:ring-2 focus-visible:ring-primary motion-reduce:transition-none"
        )}
      >
        {html ? (
          <PlanArtifactFrame
            html={html}
            title="Artifact preview"
            className={cn(PREVIEW_HEIGHT_CLASS, "pointer-events-none")}
          />
        ) : (
          <Box
            padding="lg"
            className={cn(
              PREVIEW_HEIGHT_CLASS,
              "pointer-events-none overflow-hidden"
            )}
          >
            <Markdown content={markdown} />
          </Box>
        )}
        <span
          data-testid="inline-plan-fade"
          className="pointer-events-none absolute inset-x-0 bottom-0 flex h-24 items-end justify-end rounded-b-panel bg-linear-to-b from-transparent via-canvas/75 to-canvas p-3"
        >
          <span
            className={buttonVariants({
              variant: "secondary",
              size: "compact",
            })}
          >
            Open artifact
            <Icon icon={ArrowUpRight} size="sm" />
          </span>
        </span>
      </button>
      <Button
        type="button"
        variant="outline"
        size="icon-sm"
        aria-label="Dismiss artifact"
        disabled={dismiss.isPending}
        onClick={() => dismiss.mutate()}
        className="absolute top-2 right-2 z-10 text-ink-subtle shadow-control hover:text-ink"
      >
        <Icon icon={X} size="sm" />
      </Button>
    </Box>
  )
}
