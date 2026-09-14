import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react"

const BOTTOM_LOCK_THRESHOLD_PX = 24
const REMEMBERED_TRANSCRIPTS = 200

interface RememberedPosition {
  top: number
  /** Pinned to the live tail: restoring means following new content, not an offset. */
  atBottom: boolean
}

/** Where the user last was in each transcript, kept for the life of the page. */
const rememberedPositions = new Map<string, RememberedPosition>()

function remember(key: string, position: RememberedPosition): void {
  rememberedPositions.delete(key)
  rememberedPositions.set(key, position)
  if (rememberedPositions.size > REMEMBERED_TRANSCRIPTS) {
    const oldest = rememberedPositions.keys().next().value
    if (oldest !== undefined) rememberedPositions.delete(oldest)
  }
}

function isNearBottom(el: HTMLElement): boolean {
  return (
    el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_LOCK_THRESHOLD_PX
  )
}

function maxScrollTop(el: HTMLElement): number {
  return Math.max(0, el.scrollHeight - el.clientHeight)
}

interface ScrollState {
  /** Stick to the tail as content grows; cleared when the user scrolls up. */
  followTail: boolean
  /** The offset the user chose, restored when layout shifts under them. */
  manualTop: number
  previousTop: number
  frame: number | null
  /** A remembered offset still waiting for enough content to scroll to. */
  pendingRestoreTop: number | null
}

/**
 * Scroll behaviour for a streaming transcript: follow the tail until the user
 * scrolls up, hold their place while content reflows, and remember where they
 * were in each transcript (by `scrollKey`) across navigation.
 */
export function useTranscriptScroll({
  scrollKey,
  messages,
  isStreaming,
}: {
  scrollKey?: string
  messages: ReadonlyArray<unknown>
  isStreaming: boolean
}) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  const state = useRef<ScrollState>({
    followTail: true,
    manualTop: 0,
    previousTop: 0,
    frame: null,
    pendingRestoreTop: null,
  })
  const [showScrollToBottom, setShowScrollToBottom] = useState(false)

  const settle = useCallback((el: HTMLElement, top: number) => {
    state.current.manualTop = top
    state.current.previousTop = top
    setShowScrollToBottom(!isNearBottom(el))
  }, [])

  const cancelScheduledJump = useCallback(() => {
    if (state.current.frame === null) return
    window.cancelAnimationFrame(state.current.frame)
    state.current.frame = null
  }, [])

  const jumpToBottom = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
    settle(el, el.scrollTop)
  }, [settle])

  const scheduleJumpToBottom = useCallback(() => {
    if (!state.current.followTail) return
    cancelScheduledJump()
    state.current.frame = window.requestAnimationFrame(() => {
      state.current.frame = null
      if (state.current.followTail) jumpToBottom()
    })
  }, [cancelScheduledJump, jumpToBottom])

  const applyPendingRestore = useCallback(
    (el: HTMLElement): boolean => {
      const top = state.current.pendingRestoreTop
      if (top === null) return false
      el.scrollTop = Math.min(top, maxScrollTop(el))
      settle(el, el.scrollTop)
      return true
    },
    [settle]
  )

  const scrollToBottom = useCallback(() => {
    state.current.followTail = true
    state.current.pendingRestoreTop = null
    cancelScheduledJump()
    jumpToBottom()
  }, [cancelScheduledJump, jumpToBottom])

  // Entering a transcript: resume where the user left it, or follow the tail
  // when they were pinned there (or have never seen it).
  useLayoutEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const remembered = scrollKey
      ? rememberedPositions.get(scrollKey)
      : undefined
    if (remembered && !remembered.atBottom) {
      state.current.followTail = false
      state.current.pendingRestoreTop = remembered.top
      cancelScheduledJump()
      applyPendingRestore(el)
      return
    }
    scrollToBottom()
  }, [applyPendingRestore, cancelScheduledJump, scrollKey, scrollToBottom])

  // New or reflowed content: keep following, or hold the user's place.
  useLayoutEffect(() => {
    const el = scrollRef.current
    if (!el) return
    if (applyPendingRestore(el)) {
      // Once the transcript has content the restore is done; later growth
      // must not yank the user back to the remembered offset.
      if (messages.length > 0) state.current.pendingRestoreTop = null
      return
    }
    if (state.current.followTail) {
      scheduleJumpToBottom()
      return
    }
    const target = Math.min(state.current.manualTop, maxScrollTop(el))
    if (Math.abs(el.scrollTop - target) > el.clientHeight * 0.5) {
      el.scrollTop = target
    }
    state.current.previousTop = el.scrollTop
    setShowScrollToBottom(!isNearBottom(el))
  }, [applyPendingRestore, isStreaming, messages, scheduleJumpToBottom])

  // The user's own scrolling decides whether we keep following the tail.
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const handleScroll = () => {
      const top = el.scrollTop
      const nearBottom = isNearBottom(el)
      if (top < state.current.previousTop - 1) {
        state.current.followTail = false
        cancelScheduledJump()
      } else if (nearBottom) {
        state.current.followTail = true
      }
      settle(el, top)
      if (scrollKey) remember(scrollKey, { top, atBottom: nearBottom })
    }
    el.addEventListener("scroll", handleScroll, { passive: true })
    return () => {
      el.removeEventListener("scroll", handleScroll)
      cancelScheduledJump()
    }
  }, [cancelScheduledJump, scrollKey, settle])

  useEffect(() => {
    const scroller = scrollRef.current
    const content = contentRef.current
    if (!scroller || !content || typeof ResizeObserver === "undefined") return
    const observer = new ResizeObserver(() => {
      if (applyPendingRestore(scroller)) return
      if (state.current.followTail) {
        scheduleJumpToBottom()
        return
      }
      const max = maxScrollTop(scroller)
      if (state.current.manualTop > max) {
        scroller.scrollTop = max
        state.current.manualTop = max
        state.current.previousTop = max
      }
      setShowScrollToBottom(!isNearBottom(scroller))
    })
    observer.observe(scroller)
    observer.observe(content)
    return () => observer.disconnect()
  }, [applyPendingRestore, scheduleJumpToBottom])

  return { scrollRef, contentRef, showScrollToBottom, scrollToBottom }
}
