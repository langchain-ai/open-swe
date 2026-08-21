/**
 * What the reader has picked out of the diff: a highlighted line range destined
 * for the chat, and the single open inline comment composer. Both are driven
 * from the same gutter/highlight interactions, and only one of them — plus any
 * expanded finding — may own a line at a time, so they live together.
 */

import { useCallback, useEffect, useRef, useState } from "react"

import type { SelectedLineRange } from "@pierre/diffs"
import type { ReviewDiffFile } from "@/features/reviews/lib/api"
import type { ReviewChatComposer } from "@/features/reviews/components/ReviewChat"
import { buildSelectionAttachments } from "@/features/reviews/lib/diffSelection"

export interface FileLineRange {
  file: string
  range: SelectedLineRange
}

export interface ReviewSelection {
  userSelection: FileLineRange | null
  commentDraft: FileLineRange | null
  selectLines: (path: string, range: SelectedLineRange | null) => void
  addToChat: (path: string, range: SelectedLineRange) => void
  startComment: (path: string, range: SelectedLineRange) => void
  closeComment: () => void
  clearSelection: () => void
}

export function useReviewSelection({
  filesByPath,
  composer,
  onDiffFocus,
  onAddedToChat,
}: {
  filesByPath: Map<string, ReviewDiffFile>
  composer: ReviewChatComposer | null
  /** A diff interaction took focus; anything else expanded should collapse. */
  onDiffFocus: () => void
  onAddedToChat: () => void
}): ReviewSelection {
  const [userSelection, setUserSelection] = useState<FileLineRange | null>(null)
  const [commentDraft, setCommentDraft] = useState<FileLineRange | null>(null)

  const filesByPathRef = useRef(filesByPath)
  filesByPathRef.current = filesByPath
  const onDiffFocusRef = useRef(onDiffFocus)
  onDiffFocusRef.current = onDiffFocus
  const onAddedToChatRef = useRef(onAddedToChat)
  onAddedToChatRef.current = onAddedToChat

  const selectLines = useCallback(
    (path: string, range: SelectedLineRange | null) => {
      if (range) {
        setUserSelection({ file: path, range })
        onDiffFocusRef.current()
      } else {
        setUserSelection((prev) => (prev?.file === path ? null : prev))
      }
    },
    []
  )

  const clearSelection = useCallback(() => setUserSelection(null), [])

  const addToChat = useCallback(
    (path: string, range: SelectedLineRange) => {
      const file = filesByPathRef.current.get(path)
      if (!file) return
      for (const attachment of buildSelectionAttachments(file, range)) {
        composer?.addAttachment(attachment)
      }
      onAddedToChatRef.current()
      setUserSelection(null)
    },
    [composer]
  )

  // Open the inline comment composer for a line (gutter "+" click). Clearing the
  // chat selection + expanded finding keeps the "+" owned by the composer alone.
  const startComment = useCallback((path: string, range: SelectedLineRange) => {
    setUserSelection(null)
    onDiffFocusRef.current()
    setCommentDraft({ file: path, range })
  }, [])

  const closeComment = useCallback(() => setCommentDraft(null), [])

  // ⌘L / Ctrl+L adds the current line selection to the chat (Cursor-style).
  const userSelectionRef = useRef(userSelection)
  userSelectionRef.current = userSelection
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "l") {
        const sel = userSelectionRef.current
        if (sel) {
          event.preventDefault()
          addToChat(sel.file, sel.range)
        }
      }
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [addToChat])

  // Clicking away from the highlighted rows clears the selection. A pointer-down
  // that begins a fresh selection clears here first, then the new drag repaints.
  // Reads the ref so the listener is registered once (no churn during a drag).
  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      if (!userSelectionRef.current) return
      const target = event.target
      if (target instanceof Element && target.closest("[data-add-to-chat]"))
        return
      setUserSelection(null)
    }
    window.addEventListener("pointerdown", onPointerDown)
    return () => window.removeEventListener("pointerdown", onPointerDown)
  }, [])

  return {
    userSelection,
    commentDraft,
    selectLines,
    addToChat,
    startComment,
    closeComment,
    clearSelection,
  }
}
