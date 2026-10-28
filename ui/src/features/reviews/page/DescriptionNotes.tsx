import { CaretDownIcon } from "@langchain/macaw-components/icons"
import {
  Popover,
  PopoverAnchor,
  PopoverContent,
} from "@langchain/macaw-components/Popover"
import { PatchDiff } from "@pierre/diffs/react"
import { useQuery } from "@tanstack/react-query"
import { createContext, use, useEffect, useRef, useState } from "react"
import type { ComponentProps, ReactNode } from "react"
import type { ExtraProps, StreamdownProps } from "streamdown"

import type {
  ReviewDiffFile,
  ReviewWalkthroughFile,
  ReviewWalkthroughSentence,
} from "@/lib/api"
import { cn } from "@/lib/utils"
import { useDiffOptions } from "@/features/agents/utils/diffUtils"
import { linesPatch } from "@/features/reviews/lib/walkthroughDiff"
import {
  NOTE_ATTRIBUTE,
  NOTE_END_ATTRIBUTE,
  remarkDescriptionNotes,
} from "./descriptionNotes"
import { reviewQueries, type PullRequestRef } from "./queries"
import { useReviewPage } from "./store"

// Long enough that sweeping across sentences opens none of them.
const OPEN_DELAY_MS = 250
// Long enough to cross the gap between a sentence and its popover.
const CLOSE_DELAY_MS = 150

interface NotesState {
  pr: PullRequestRef
  sentences: Array<ReviewWalkthroughSentence>
  open: number | null
  hover: (note: number | null) => void
  toggle: (note: number) => void
  close: () => void
}

const NotesContext = createContext<NotesState | null>(null)

/** The remark plugins that mark the annotated sentences of a description. */
export function descriptionNotePlugins(
  sentences: Array<ReviewWalkthroughSentence>
): NonNullable<StreamdownProps["remarkPlugins"]> {
  return sentences.length
    ? [[remarkDescriptionNotes, sentences.map((s) => s.sentence)]]
    : []
}

/** The renderers that turn the plugin's marks into hover targets. */
export const NOTE_COMPONENTS = { span: NoteSpan }

/** Lets the description's `span`s show the code each annotated sentence refers to. */
export function DescriptionNotes({
  pr,
  sentences,
  children,
}: {
  pr: PullRequestRef
  sentences: Array<ReviewWalkthroughSentence>
  children: ReactNode
}) {
  const [open, setOpen] = useState<number | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)
  useEffect(() => () => clearTimeout(timer.current), [])
  const state: NotesState = {
    pr,
    sentences,
    open,
    hover: (note) => {
      clearTimeout(timer.current)
      if (note === open) return
      timer.current = setTimeout(
        () => setOpen(note),
        note === null ? CLOSE_DELAY_MS : OPEN_DELAY_MS
      )
    },
    toggle: (note) => {
      clearTimeout(timer.current)
      setOpen((current) => (current === note ? null : note))
    },
    close: () => {
      clearTimeout(timer.current)
      setOpen(null)
    },
  }
  return <NotesContext value={state}>{children}</NotesContext>
}

/** Markdown `span` renderer: an annotated sentence's pieces become hover targets. */
export function NoteSpan({
  node: _node,
  children,
  ...props
}: ExtraProps & ComponentProps<"span">) {
  const attributes = props as Record<string, unknown>
  const notes = use(NotesContext)
  const note = Number(attributes[NOTE_ATTRIBUTE])
  const sentence = notes?.sentences[note]
  if (!notes || !sentence) return <span {...props}>{children}</span>
  const open = notes.open === note
  const end = attributes[NOTE_END_ATTRIBUTE] === "true"
  const text = (
    <span
      data-description-note={note}
      className={cn(
        "rounded-xs underline decoration-text-tertiary decoration-dotted underline-offset-2 transition-colors",
        open && "decoration-brand bg-brand-subtle"
      )}
      onMouseEnter={() => notes.hover(note)}
      onMouseLeave={() => notes.hover(null)}
    >
      {children}
    </span>
  )
  if (!end) return text
  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        if (!next) notes.close()
      }}
    >
      <PopoverAnchor asChild>{text}</PopoverAnchor>
      <button
        type="button"
        aria-label={open ? "Hide the code" : "Show the code"}
        aria-expanded={open}
        onClick={() => notes.toggle(note)}
        className="ml-0.5 inline-flex size-4 translate-y-[2px] items-center justify-center rounded-full text-secondary hover:text-primary [@media(hover:hover)]:hidden"
      >
        <CaretDownIcon
          aria-hidden
          className={cn("size-3 transition-transform", open && "rotate-180")}
        />
      </button>
      <PopoverContent
        side="bottom"
        align="start"
        collisionPadding={12}
        className="flex max-h-[min(60vh,520px,var(--radix-popover-content-available-height))] w-[min(640px,calc(100vw-24px))] flex-col gap-space-2 overflow-y-auto p-space-2"
        onOpenAutoFocus={(event) => event.preventDefault()}
        onMouseEnter={() => notes.hover(note)}
        onMouseLeave={() => notes.hover(null)}
      >
        <SentenceCode pr={notes.pr} sentence={sentence} onJump={notes.close} />
      </PopoverContent>
    </Popover>
  )
}

function SentenceCode({
  pr,
  sentence,
  onJump,
}: {
  pr: PullRequestRef
  sentence: ReviewWalkthroughSentence
  onJump: () => void
}) {
  const diff = useQuery(reviewQueries.diff(pr)).data
  const byPath = new Map(diff?.files.map((file) => [file.path, file]))
  return (
    <>
      {sentence.steps.length > 0 && (
        <p className="px-space-1 text-xxs text-secondary">
          {sentence.steps.length === 1 ? "Step " : "Steps "}
          {sentence.steps.join(", ")} of the walkthrough
        </p>
      )}
      {sentence.files.map((lines) => (
        <FileCode
          key={lines.path}
          loading={!diff}
          file={byPath.get(lines.path)}
          lines={lines}
          onJump={onJump}
        />
      ))}
    </>
  )
}

function FileCode({
  loading,
  file,
  lines,
  onJump,
}: {
  loading: boolean
  file: ReviewDiffFile | undefined
  lines: ReviewWalkthroughFile
  onJump: () => void
}) {
  const diffOptions = useDiffOptions("unified")
  // Gaps between the referenced hunks cannot expand here, so mark them plainly.
  const options = { ...diffOptions, hunkSeparators: "simple" as const }
  const jumpTo = useReviewPage((state) => state.jumpTo)
  const patch = file ? linesPatch(file, lines) : null
  const added = lines.added[0]?.[0]
  const deleted = lines.deleted[0]?.[0]
  const jump = () => {
    onJump()
    jumpTo(
      added !== undefined
        ? { kind: "line", path: lines.path, line: added, side: "RIGHT" }
        : deleted !== undefined
          ? { kind: "line", path: lines.path, line: deleted, side: "LEFT" }
          : { kind: "file", path: lines.path }
    )
  }
  return (
    <section className="overflow-hidden rounded-md border border-default">
      <button
        type="button"
        onClick={jump}
        title="Go to this code in the diff"
        className="block w-full truncate border-b border-default bg-surface-level-1 px-space-2 py-space-1 text-left font-mono text-xxs text-secondary hover:text-primary"
      >
        {lines.path}
      </button>
      {patch ? (
        <div className="overflow-x-auto font-mono text-xxs leading-5">
          <PatchDiff patch={patch} disableWorkerPool options={options} />
        </div>
      ) : (
        <p className="p-space-2 text-xxs text-secondary">
          {loading
            ? "Loading…"
            : file
              ? "Binary or large file — diff not shown."
              : "This file is not in the loaded diff."}
        </p>
      )}
    </section>
  )
}
