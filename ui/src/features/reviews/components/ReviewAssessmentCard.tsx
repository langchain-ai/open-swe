import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { StatReadout } from "@langchain/gtm-platform-design-system/patterns/stat-readout"
import { StateNotice } from "@langchain/gtm-platform-design-system/patterns/state-notice"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import {
  ToggleGroup,
  ToggleGroupItem,
} from "@langchain/gtm-platform-design-system/ui/toggle-group"

import {
  AlertTriangle,
  Check,
  ChevronRight,
  ThumbsDown,
  ThumbsUp,
} from "@/components/glyphs"
import type {
  PublishedReviewAssessment,
  ReviewAssessmentFeedbackInput,
} from "@/lib/api"
import { api } from "@/lib/api"
import { useSession } from "@/lib/session"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Textarea } from "@langchain/gtm-platform-design-system/ui/textarea"

type Rating = ReviewAssessmentFeedbackInput["rating"]

function isRating(value: unknown): value is Rating {
  return value === "helpful" || value === "unhelpful"
}

function riskTone(score: number) {
  if (score >= 4) return "risk" as const
  if (score === 3) return "attention" as const
  return "positive" as const
}

interface Props {
  assessment: PublishedReviewAssessment
  owner: string
  repo: string
  number: number
  headSha: string
}

export function ReviewAssessmentCard(props: Props) {
  const { data: session } = useSession()
  return (
    <AssessmentCard
      key={`${props.assessment.review_id}:${session?.login}`}
      {...props}
      login={session?.login}
    />
  )
}

function AssessmentCard({
  assessment,
  owner,
  repo,
  number,
  headSha,
  login,
}: Props & { login?: string }) {
  const queryClient = useQueryClient()
  const queryKey = [
    "assessment-feedback",
    owner,
    repo,
    number,
    assessment.review_id,
    login,
  ]
  const feedback = useQuery({
    queryKey,
    queryFn: () =>
      api.getAssessmentFeedback(owner, repo, number, assessment.review_id),
    enabled: Boolean(login),
  })
  const [editing, setEditing] = useState(
    () =>
      typeof window !== "undefined" &&
      window.location.hash === "#assessment-feedback"
  )
  const [draft, setDraft] = useState<{
    rating?: ReviewAssessmentFeedbackInput["rating"]
    comment: string
  } | null>(null)
  const value = draft ?? feedback.data
  const save = useMutation({
    mutationFn: (input: ReviewAssessmentFeedbackInput) =>
      api.saveAssessmentFeedback(
        owner,
        repo,
        number,
        assessment.review_id,
        input
      ),
    meta: { errorTitle: "Couldn't save feedback" },
    onSuccess: async (saved) => {
      await queryClient.cancelQueries({ queryKey })
      queryClient.setQueryData(queryKey, saved)
      setDraft(null)
      setEditing(false)
    },
  })

  const verdict = assessment.approved
    ? { label: "Approved", tone: "positive" as const }
    : assessment.decision === "would_approve"
      ? { label: "Would approve", tone: "info" as const }
      : { label: "Needs human review", tone: "attention" as const }

  return (
    <Stack
      render={
        <section id="assessment-feedback" aria-label="Review assessment" />
      }
      gap="md"
      bg="panel"
      border="line"
      radius="panel"
      padding="lg"
      className="mt-4 text-body"
    >
      <Inline gap="md" justify="between" wrap>
        <Inline gap="lg" wrap>
          <Box className="w-24">
            <StatReadout
              label="Risk"
              value={`${assessment.risk_score}/5`}
              tone={riskTone(assessment.risk_score)}
            />
          </Box>
          <Badge tier="quiet" tone={verdict.tone}>
            {verdict.label}
          </Badge>
          <span className="text-meta text-ink-subtle">
            {assessment.approved
              ? "Automatic approval"
              : assessment.dry_run
                ? "Dry run"
                : "Advisory"}
          </span>
        </Inline>
        {!editing && login && feedback.isSuccess && (
          <Inline gap="sm">
            {feedback.data && (
              <Inline
                role="status"
                gap="xs"
                className="text-meta text-ink-subtle"
              >
                <Icon icon={Check} size="sm" className="text-positive" />
                <span>Feedback saved</span>
              </Inline>
            )}
            <Button
              size="compact"
              variant="ghost"
              onClick={() => {
                save.reset()
                setEditing(true)
              }}
            >
              {feedback.data ? "Edit feedback" : "Rate assessment"}
            </Button>
          </Inline>
        )}
      </Inline>
      <details className="group/why text-meta text-ink-subtle">
        <summary className="flex w-fit cursor-pointer list-none items-center gap-1 hover:text-ink [&::-webkit-details-marker]:hidden">
          <Icon
            icon={ChevronRight}
            size="sm"
            className="transition-transform duration-fast ease-out-quint group-open/why:rotate-90 motion-reduce:transition-none"
          />
          Why this assessment?
        </summary>
        <Stack gap="sm" className="mt-2 pl-5">
          <p className="text-body whitespace-pre-wrap text-ink">
            {assessment.explanation}
          </p>
          <p>
            Reviewed commit{" "}
            <span className="font-mono">{assessment.head_sha.slice(0, 7)}</span>
            . Risk ranges from 1 (low) to 5 (high).
          </p>
        </Stack>
      </details>
      {headSha !== assessment.head_sha && (
        <Inline gap="xs" className="text-label text-attention">
          <Icon icon={AlertTriangle} size="sm" />
          <span>This assessment is for an earlier commit.</span>
        </Inline>
      )}
      {feedback.isError && (
        <StateNotice
          tone="ATTENTION"
          icon={AlertTriangle}
          title="Could not load your feedback"
          description="Your saved rating is unchanged. Try loading it again."
          action={
            <Button
              size="compact"
              variant="outline"
              onClick={() => void feedback.refetch()}
            >
              Retry
            </Button>
          }
        />
      )}
      {editing && login && feedback.isSuccess && (
        <form
          className="border-t border-line pt-3"
          onSubmit={(event) => {
            event.preventDefault()
            if (value?.rating && !save.isPending)
              save.mutate({ rating: value.rating, comment: value.comment })
          }}
        >
          <Stack
            render={<fieldset disabled={save.isPending} />}
            gap="md"
            className="min-w-0"
          >
            <legend className="mb-3 text-label font-medium text-ink">
              Was this assessment helpful?
            </legend>
            <ToggleGroup
              aria-label="Was this assessment helpful?"
              value={value?.rating ? [value.rating] : []}
              onValueChange={(next: unknown[]) => {
                const rating = next[0]
                if (isRating(rating))
                  setDraft({ rating, comment: value?.comment ?? "" })
              }}
            >
              <ToggleGroupItem value="helpful">
                <Icon icon={ThumbsUp} size="sm" />
                Helpful
              </ToggleGroupItem>
              <ToggleGroupItem value="unhelpful">
                <Icon icon={ThumbsDown} size="sm" />
                Not helpful
              </ToggleGroupItem>
            </ToggleGroup>
            <Stack
              render={<label />}
              gap="xs"
              className="text-meta text-ink-subtle"
            >
              <span>Comment (optional)</span>
              <Textarea
                maxLength={3000}
                value={value?.comment ?? ""}
                onChange={(event) =>
                  setDraft({
                    rating: value?.rating,
                    comment: event.target.value,
                  })
                }
                placeholder="What was right, or what did we miss?"
                className="min-h-20"
              />
            </Stack>
            <p className="text-meta text-ink-subtle">
              Saved in Open SWE. Your comment is not posted to GitHub.
            </p>
            <Inline gap="sm">
              <Button
                type="submit"
                size="compact"
                disabled={!value?.rating}
                loading={save.isPending}
              >
                Save feedback
              </Button>
              <Button
                type="button"
                size="compact"
                variant="ghost"
                onClick={() => {
                  setEditing(false)
                  setDraft(null)
                  save.reset()
                }}
              >
                Cancel
              </Button>
            </Inline>
          </Stack>
        </form>
      )}
    </Stack>
  )
}
