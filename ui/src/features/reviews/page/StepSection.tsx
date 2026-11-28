import { Checkbox } from "@langchain/macaw-components/Checkbox"
import {
  ArrowDownIcon,
  ArrowUpIcon,
  CheckIcon,
} from "@langchain/macaw-components/icons"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Text } from "@langchain/macaw-components/Text"

import { cn } from "@/lib/utils"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import type { StepGroup } from "./diffEntries"
import { useStepGroup } from "./entries"
import { FILE_HEADER_HEIGHT } from "./FileHeader"
import { InlineCode } from "./inlineCode"
import { isRead, useReviewPage } from "./store"
import { plural } from "./text"

const STEP_ITEM_PREFIX = "step:"

/** The id of the item that opens a step: its header and intro, above the step's files. */
export function stepItemId(index: number): string {
  return `${STEP_ITEM_PREFIX}${index}`
}

function useStepRead(group: StepGroup): boolean {
  return useReviewPage((page) =>
    group.entries.every((entry) => isRead(page.viewed, entry))
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
export function MarkStepRead({ group }: { group: StepGroup }) {
  const { index } = group.step
  const read = useStepRead(group)
  const markStepRead = useReviewPage((state) => state.markStepRead)
  return (
    <div className="relative isolate grid min-h-6 shrink-0 items-center">
      <Text
        variant="sm"
        aria-hidden
        className="invisible col-start-1 row-start-1 flex items-center gap-space-2 text-xxs whitespace-nowrap"
      >
        <span className="size-4 shrink-0" />
        Mark step read
      </Text>
      <Checkbox
        checked={read}
        onCheckedChange={() => markStepRead(index)}
        className="z-floating-bar rounded-full after:hidden"
        label={
          <>
            {read ? "Read" : "Mark step read"}
            <span className="sr-only">: step {index}</span>
          </>
        }
        labelClassName="whitespace-nowrap text-xxs text-secondary before:absolute before:inset-0"
        containerClassName="col-start-1 row-start-1 justify-self-end"
        size="sm"
      />
    </div>
  )
}

/** The step's bar: where it sits in the walkthrough, what it covers, and whether it's read. */
export function StepHeader({
  group,
  inToolbar = false,
}: {
  group: StepGroup
  inToolbar?: boolean
}) {
  const { step, entries } = group
  const read = useStepRead(group)
  const entryOrder = useReviewPage((page) => page.entryOrder)
  const jumpTo = useReviewPage((page) => page.jumpTo)
  const previousStep = entryOrder.findLast(
    (entry) => entry.step && entry.step.index < step.index
  )?.step
  const previous =
    previousStep &&
    entryOrder.find((entry) => entry.step?.index === previousStep.index)
  const next = entryOrder.find(
    (entry) => entry.step && entry.step.index > step.index
  )
  const showInlineReadToggle = useReviewPage(
    (page) =>
      page.order !== "guide" ||
      page.walkthroughToolbar?.step.index !== step.index
  )
  const files = new Set(entries.map((entry) => entry.file.path)).size
  const additions = entries.reduce((sum, entry) => sum + entry.additions, 0)
  const deletions = entries.reduce((sum, entry) => sum + entry.deletions, 0)
  const detailsClassName = inToolbar
    ? "@max-[420px]:hidden"
    : "any-pointer-coarse:@max-[420px]:hidden"
  return (
    <div
      data-step-header={inToolbar ? undefined : step.index}
      style={inToolbar ? undefined : { height: FILE_HEADER_HEIGHT }}
      className={cn(
        "flex min-w-0 items-center gap-space-2 font-sans text-xxs",
        inToolbar
          ? "w-full"
          : "@container bg-surface-level-1 px-space-4 pt-space-2",
        !inToolbar && step.index > 1 && "border-t border-default"
      )}
    >
      <StepBadge index={step.index} read={read} />
      <div className="group/step-navigation relative isolate flex shrink-0 items-center gap-space-1">
        <span className="font-medium text-brand-primary">
          Step {step.index} of {step.count}
        </span>
        {(previous || next) && (
          <div
            className={cn(
              "pointer-events-none absolute inset-x-0 top-1/2 z-floating-bar flex h-6 -translate-y-1/2 items-center justify-end bg-surface-level-1 opacity-0 transition-opacity duration-normal motion-reduce:transition-none",
              "group-focus-within/step-navigation:pointer-events-auto group-focus-within/step-navigation:opacity-100 group-hover/step-navigation:pointer-events-auto group-hover/step-navigation:opacity-100",
              "any-pointer-coarse:pointer-events-auto any-pointer-coarse:static any-pointer-coarse:h-8 any-pointer-coarse:translate-none any-pointer-coarse:opacity-100 any-pointer-coarse:[&_button]:size-8"
            )}
          >
            <IconButton
              icon={ArrowUpIcon}
              label="Previous step"
              size="xs"
              variant="plain"
              color="secondary"
              disabled={!previous}
              onClick={() =>
                previous && jumpTo({ kind: "entry", id: previous.id })
              }
            />
            <IconButton
              icon={ArrowDownIcon}
              label="Next step"
              size="xs"
              variant="plain"
              color="secondary"
              disabled={!next}
              onClick={() => next && jumpTo({ kind: "entry", id: next.id })}
            />
          </div>
        )}
      </div>
      {inToolbar && (
        <>
          <span aria-hidden className="text-tertiary">
            ·
          </span>
          <span
            className="min-w-0 truncate font-medium text-primary"
            title={step.title}
          >
            <InlineCode text={step.title} />
          </span>
        </>
      )}
      <span aria-hidden className={cn("text-tertiary", detailsClassName)}>
        ·
      </span>
      <span className={cn("shrink-0 text-secondary", detailsClassName)}>
        {plural(files, "file")}
      </span>
      <span
        className={cn(
          "flex shrink-0 gap-space-2 font-mono tabular-nums",
          detailsClassName
        )}
      >
        {additions > 0 && (
          <span className="text-success-secondary">+{additions}</span>
        )}
        {deletions > 0 && (
          <span className="text-error-secondary">−{deletions}</span>
        )}
      </span>
      {!inToolbar && <span className="flex-1" />}
      {!inToolbar && showInlineReadToggle && <MarkStepRead group={group} />}
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
    <div
      data-reply-part={`walkthrough step ${step.index}: ${step.title}`}
      className="-mb-5 px-space-4 pt-space-1 font-sans"
    >
      <h3
        data-walkthrough-step-title
        className="text-sm leading-6 font-semibold text-primary"
      >
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
