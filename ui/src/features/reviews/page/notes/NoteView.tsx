import { useQuery } from "@tanstack/react-query"
import type { ReactNode } from "react"

import { usePendingReview } from "@/features/reviews/lib/usePendingReview"
import { AgentNoteCard } from "@/features/reviews/components/AgentNoteCard"
import { ProposedCommentCard } from "@/features/reviews/components/ProposedCommentCard"
import { PendingReviewCommentCard } from "@/features/reviews/components/PendingReviewCommentCard"
import { noteKey, type Note } from "@/features/reviews/page/diffEntries"
import {
  reviewQueries,
  type PullRequestRef,
} from "@/features/reviews/page/queries"
import { useReviewPage } from "@/features/reviews/page/store"
import { Composer } from "./Composer"
import { FindingNote } from "./FindingNote"
import { NoteFrame } from "./NoteFrame"
import { ThreadNote } from "./ThreadNote"

/**
 * A note for one of CodeView's annotation slots. CodeView keys the slots by
 * position, so the key keeps a note's component with the note when another
 * is added above it, and its state never lands under a different note.
 */
export function renderNote(
  pr: PullRequestRef,
  note: Exclude<Note, { kind: "step" }>
): ReactNode {
  return <NoteView key={noteKey(note)} pr={pr} note={note} />
}

/** One note on a line; each kind reads only the data it shows. */
function NoteView({
  pr,
  note,
}: {
  pr: PullRequestRef
  note: Exclude<Note, { kind: "step" }>
}): ReactNode {
  switch (note.kind) {
    case "finding":
      return <FindingNoteView pr={pr} id={note.id} />
    case "thread":
      return <ThreadNoteView pr={pr} id={note.id} />
    case "pending":
      return <PendingNote pr={pr} id={note.id} />
    case "draft":
      return (
        <NoteFrame>
          <ProposedCommentCard
            owner={pr.owner}
            repo={pr.repo}
            number={pr.number}
            id={note.id}
          />
        </NoteFrame>
      )
    case "agent-note":
      return (
        <NoteFrame>
          <AgentNoteCard id={note.id} />
        </NoteFrame>
      )
    case "composer":
      return <ComposerNote pr={pr} />
  }
}

function FindingNoteView({ pr, id }: { pr: PullRequestRef; id: string }) {
  const finding = useQuery(reviewQueries.detail(pr)).data?.findings.find(
    (candidate) => candidate.id === id
  )
  const thread = useQuery(reviewQueries.conversation(pr)).data?.threads.find(
    (candidate) => candidate.id === finding?.github_review_comment_id
  )
  return finding ? (
    <FindingNote pr={pr} finding={finding} thread={thread ?? null} />
  ) : null
}

function ThreadNoteView({ pr, id }: { pr: PullRequestRef; id: number }) {
  const thread = useQuery(reviewQueries.conversation(pr)).data?.threads.find(
    (candidate) => candidate.id === id
  )
  return thread ? <ThreadNote pr={pr} thread={thread} /> : null
}

function PendingNote({ pr, id }: { pr: PullRequestRef; id: number }) {
  const comment = usePendingReview(pr.owner, pr.repo, pr.number).comments.find(
    (candidate) => candidate.id === id
  )
  const edit = useReviewPage((state) => state.pendingEdits.get(id) ?? null)
  const setPendingEdit = useReviewPage((state) => state.setPendingEdit)
  return comment ? (
    <NoteFrame className="px-space-1 py-0">
      <PendingReviewCommentCard
        owner={pr.owner}
        repo={pr.repo}
        number={pr.number}
        comment={comment}
        edit={edit}
        onEdit={(text) => setPendingEdit(id, text)}
      />
    </NoteFrame>
  ) : null
}

function ComposerNote({ pr }: { pr: PullRequestRef }) {
  const composer = useReviewPage((state) => state.composer)
  return composer ? <Composer pr={pr} target={composer} /> : null
}
