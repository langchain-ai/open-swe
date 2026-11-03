import { Checkbox } from "@langchain/macaw-components/Checkbox"
import { Button } from "@langchain/macaw-components/Button"
import { CheckIcon, XIcon } from "@langchain/macaw-components/icons"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { useState } from "react"

import { cn } from "@/lib/utils"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import type { StepGroup } from "./diffEntries"
import { useStepGroup } from "./entries"
import { FILE_HEADER_HEIGHT } from "./FileHeader"
import { InlineCode } from "./inlineCode"
import { activeStep, isPassed, isRead, useReviewPage } from "./store"
import { plural } from "./text"

const STEP_ITEM_PREFIX = "step:"

/** The id of the item that opens a step: its header and intro, above the step's files. */
export function stepItemId(index: number): string {
  return `${STEP_ITEM_PREFIX}${index}`
}

export function isStepItem(id: string): boolean {
  return id.startsWith(STEP_ITEM_PREFIX)
}

function useStepRead(group: StepGroup | undefined): boolean {
  return useReviewPage(
    (state) =>
      !!group && group.entries.every((entry) => isRead(state.viewed, entry))
  )
}

/** A step's number, filled once every part of it is read. */
export function StepBadge({
  index,
  read,
  active = false,
  className,
}: {
  index: number
  read: boolean
  active?: boolean
  className?: string
}) {
  return (
    <span
      className={cn(
        "flex size-5 shrink-0 items-center justify-center rounded-full text-xxs font-semibold tabular-nums",
        read
          ? "bg-brand text-brand-on-fill"
          : active
            ? "ring-brand bg-brand-subtle text-brand-primary ring-1"
            : "bg-surface-level-3 text-secondary",
        className
      )}
    >
      {read ? <CheckIcon weight="bold" className="size-3" /> : index}
    </span>
  )
}

/** The step's toggle: one click marks every part of it read, or unread again. */
export function MarkStepRead({
  index,
  read,
  className,
}: {
  index: number
  read: boolean
  className?: string
}) {
  const markStepRead = useReviewPage((state) => state.markStepRead)
  return (
    <Checkbox
      checked={read}
      onCheckedChange={() => markStepRead(index)}
      label={
        <>
          {read ? "Read" : "Mark step read"}
          <span className="sr-only">: step {index}</span>
        </>
      }
      labelClassName="text-xxs text-secondary"
      containerClassName={cn("shrink-0", className)}
      size="sm"
    />
  )
}

/** The step's bar: where it sits in the walkthrough, what it covers, and whether it's read. */
export function StepHeader({ id }: { id: string }) {
  const group = useStepGroup(id)
  const read = useStepRead(group)
  if (!group) return null
  const { step, entries } = group
  const files = new Set(entries.map((entry) => entry.file.path)).size
  const additions = entries.reduce((sum, entry) => sum + entry.additions, 0)
  const deletions = entries.reduce((sum, entry) => sum + entry.deletions, 0)
  return (
    <div
      data-step-header={step.index}
      style={{ height: FILE_HEADER_HEIGHT }}
      // The first step sits right under the Changes rule; later ones start with their own.
      className={cn(
        "flex items-center gap-space-2 bg-surface-level-1 pt-space-1 font-sans text-xxs max-sm:px-space-4",
        step.index > 1 && "border-t border-default"
      )}
    >
      <StepBadge index={step.index} read={read} />
      <span className="font-medium text-brand-primary">
        Step {step.index} of {step.count}
      </span>
      <span className="text-tertiary">·</span>
      <span className="text-secondary">{plural(files, "file")}</span>
      <span className="flex gap-space-2 font-mono tabular-nums">
        {additions > 0 && (
          <span className="text-success-secondary">+{additions}</span>
        )}
        {deletions > 0 && (
          <span className="text-error-secondary">−{deletions}</span>
        )}
      </span>
      <span className="flex-1" />
      <MarkStepRead index={step.index} read={read} />
    </div>
  )
}

/** What the step is about, above its files and as wide as them. */
export function StepIntro({ id }: { id: string }) {
  const group = useStepGroup(id)
  if (!group) return null
  const { step } = group
  return (
    // Pulled down over the step item's hidden placeholder line, so the files follow closely.
    <div className="-mb-5 pt-space-1 font-sans max-sm:px-space-4">
      <h3 className="text-sm leading-6 font-semibold text-primary">
        <InlineCode text={step.title} />
      </h3>
      {step.summary && (
        <div className="mt-space-1 max-w-[80ch] text-secondary [&_li]:text-xs [&_li]:leading-5 [&_p]:text-xs [&_p]:leading-5">
          <Markdown content={step.summary} />
        </div>
      )}
    </div>
  )
}

/** Below the last step: how much of the walkthrough is read. */
export function WalkthroughEnd({
  groups,
}: {
  groups: ReadonlyArray<StepGroup>
}) {
  const read = useReviewPage(
    (state) =>
      groups.filter((group) =>
        group.entries.every((entry) => isRead(state.viewed, entry))
      ).length
  )
  if (groups.length === 0) return null
  const total = groups[0]!.step.count
  return (
    <p className="mx-space-4 mt-space-4 flex items-center gap-space-2 border-t border-default pt-space-3 font-sans text-xs text-secondary">
      <StepBadge index={total} read={read === total} className="size-4" />
      End of the walkthrough · {read} of {plural(total, "step")} read
    </p>
  )
}

/**
 * Once the reader scrolls past a step without marking it, a quiet offer to mark it read.
 * Scrolling past is never taken as reading; only the click marks it.
 */
export function ReadNudge({ groups }: { groups: ReadonlyArray<StepGroup> }) {
  const [dismissed, setDismissed] = useState<ReadonlySet<number>>(new Set())
  const candidate = useReviewPage((state) => {
    const current = activeStep(state)
    const last = groups.at(-1)
    const index =
      state.atEnd && last
        ? last.step.index
        : current === null
          ? null
          : current - 1
    const group = groups.find((item) => item.step.index === index)
    if (!group || !isPassed(state, group.step)) return null
    return group.entries.every((entry) => isRead(state.viewed, entry))
      ? null
      : group.step.index
  })
  const markStepRead = useReviewPage((state) => state.markStepRead)
  if (candidate === null || dismissed.has(candidate)) return null
  return (
    <div className="absolute right-6 bottom-6 z-floating-bar flex items-center gap-space-1 rounded-full border border-subtle bg-surface-level-1/80 py-0.5 pr-0.5 pl-space-3 font-sans text-xxs text-tertiary opacity-80 backdrop-blur-sm transition-opacity focus-within:opacity-100 hover:opacity-100">
      Done with step {candidate}?
      <Button
        color="secondary"
        variant="plain"
        size="sm"
        aria-label="Mark read"
        type="button"
        onClick={() => markStepRead(candidate)}
        className="gap-space-1 rounded-full px-space-2 py-0.5 font-medium text-brand-primary hover:bg-brand-subtle"
      >
        <CheckIcon weight="bold" className="size-3" />
        Mark read
      </Button>
      <IconButton
        icon={XIcon}
        label={`Don't ask about step ${candidate}`}
        size="xxs"
        color="secondary"
        variant="plain"
        onClick={() => setDismissed(new Set(dismissed).add(candidate))}
      />
    </div>
  )
}
