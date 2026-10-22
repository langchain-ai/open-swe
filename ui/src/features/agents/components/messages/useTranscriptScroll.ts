import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react"

const BOTTOM_LOCK_THRESHOLD_PX = 24
/** A sent message that hasn't rendered by now never will; stop waiting for it. */
const PENDING_PIN_TIMEOUT_MS = 5000
const REMEMBERED_TRANSCRIPTS = 200
/** How long a sent message takes to glide up to the top. */
const PIN_GLIDE_MS = 450
/**
 * The most one frame may advance a glide. A slow frame (rendering right after
 * a send often is one) then slows the glide for a beat instead of jumping it.
 */
const PIN_GLIDE_MAX_FRAME_MS = 17
/** How long a wheel, touch, or key keeps scroll events attributed to the user (momentum included). */
const USER_SCROLL_WINDOW_MS = 1000
const SCROLL_KEYS = new Set([
  "ArrowUp",
  "ArrowDown",
  "PageUp",
  "PageDown",
  "Home",
  "End",
  " ",
])

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

function isEditable(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLElement &&
    (target.isContentEditable ||
      target instanceof HTMLInputElement ||
      target instanceof HTMLTextAreaElement ||
      target instanceof HTMLSelectElement)
  )
}

function messageElement(
  scroller: HTMLElement,
  messageId: string
): HTMLElement | null {
  return scroller.querySelector<HTMLElement>(
    `[data-message-id="${CSS.escape(messageId)}"]`
  )
}

/** The offset that puts a message at the top, as far down as the first one sits. */
function pinTarget(scroller: HTMLElement, message: HTMLElement): number {
  const gap =
    parseFloat(getComputedStyle(scroller).paddingTop) +
    parseFloat(getComputedStyle(message).marginTop)
  return (
    message.getBoundingClientRect().top -
    scroller.getBoundingClientRect().top +
    scroller.scrollTop -
    gap
  )
}

/** Leaves at once, then settles gently: the glide reads as a response, not a wait. */
function easeOutQuint(progress: number): number {
  return 1 - (1 - progress) ** 5
}

function prefersReducedMotion(): boolean {
  return (
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false
  )
}

interface Glide {
  frame: number
}

interface Pin {
  messageId: string
  /** Keep the message at the top; dropped once the user scrolls. */
  hold: boolean
  /** It has been brought to the top once; later holds keep it there in place. */
  placed: boolean
  /** The animation bringing it there, while it runs. */
  glide: Glide | null
}

interface ScrollState {
  /** Stick to the tail as content grows; cleared when the user scrolls up. */
  followTail: boolean
  /** The offset the user chose, restored when layout shifts under them. */
  manualTop: number
  previousTop: number
  /** A remembered offset still waiting for enough content to scroll to. */
  pendingRestoreTop: number | null
  /**
   * Geometry captured when an older page was asked for, so the content the
   * user is reading can be put back under their eyes once it prepends.
   */
  prependAnchor: { scrollHeight: number; scrollTop: number } | null
  /**
   * The message the user last sent that has rendered. Space is reserved below
   * it so it can sit at the top of the viewport while the reply fills in.
   */
  pin: Pin | null
  /**
   * A message the user sent that has not rendered yet. It takes over the pin
   * when it does; until then the transcript holds still, so whatever renders
   * ahead of it (the run's spinner) can't scroll it first.
   */
  pendingPin: { messageId: string; since: number } | null
}

/**
 * Scroll behaviour for a streaming transcript: follow the tail until the user
 * scrolls up, hold their place while content reflows, and remember where they
 * were in each transcript (by `scrollKey`) across navigation. A message the
 * user sends glides up to the top and the reply fills in below it.
 *
 * Every adjustment runs before paint (layout effects and ResizeObserver
 * callbacks), so no frame ever shows the transcript out of place. Any scroll
 * the hook didn't make counts as the user moving, except the browser clamping
 * to a shrinking scroll height, which must not stop the tail being followed.
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
  const reserveRef = useRef<HTMLDivElement>(null)
  const state = useRef<ScrollState>({
    followTail: true,
    manualTop: 0,
    previousTop: 0,
    pendingRestoreTop: null,
    prependAnchor: null,
    pin: null,
    pendingPin: null,
  })
  const [showScrollToBottom, setShowScrollToBottom] = useState(false)

  const settle = useCallback((el: HTMLElement, top: number) => {
    state.current.manualTop = top
    state.current.previousTop = top
    setShowScrollToBottom(!isNearBottom(el) && !state.current.pin?.glide)
  }, [])

  const jumpToBottom = useCallback(
    (el: HTMLElement) => {
      el.scrollTop = el.scrollHeight
      settle(el, el.scrollTop)
    },
    [settle]
  )

  const pinnedElement = useCallback((el: HTMLElement): HTMLElement | null => {
    const pin = state.current.pin
    if (!pin) return null
    return messageElement(el, pin.messageId)
  }, [])

  /** Whether a sent message is still on its way; adopts it once it renders. */
  const awaitPendingPin = useCallback((el: HTMLElement): boolean => {
    const pending = state.current.pendingPin
    if (!pending) return false
    if (messageElement(el, pending.messageId)) {
      state.current.pin = {
        messageId: pending.messageId,
        hold: true,
        placed: false,
        glide: null,
      }
      state.current.pendingPin = null
      return false
    }
    if (performance.now() - pending.since < PENDING_PIN_TIMEOUT_MS) return true
    state.current.pendingPin = null
    return false
  }, [])

  /**
   * Size the space below the transcript so the pinned message can reach the
   * top: it shrinks as the reply grows, so the scroll height holds still until
   * the reply outgrows the viewport.
   */
  const fitReserve = useCallback(
    (el: HTMLElement) => {
      const reserve = reserveRef.current
      const message = pinnedElement(el)
      if (!reserve || !message) return
      // Where the content really ends: scrollHeight can't say while the
      // transcript is shorter than the viewport, since it never reports less.
      const contentEnd =
        reserve.getBoundingClientRect().top -
        el.getBoundingClientRect().top +
        el.scrollTop +
        parseFloat(getComputedStyle(el).paddingBottom)
      const needed = Math.max(
        0,
        Math.ceil(pinTarget(el, message) + el.clientHeight - contentEnd)
      )
      if (needed !== reserve.offsetHeight) {
        reserve.style.height = `${needed}px`
      }
    },
    [pinnedElement]
  )

  const cancelGlide = useCallback(() => {
    const pin = state.current.pin
    if (!pin?.glide) return
    window.cancelAnimationFrame(pin.glide.frame)
    pin.glide = null
  }, [])

  /**
   * Ease the pinned message up to the top. It starts at most a screen away, so
   * a long way still reads as a short glide, and each frame aims at where the
   * message belongs now, so content moving mid-glide can't throw it off. The
   * clock starts a frame late and advances at most a frame at a time: the
   * frame after a send's render is often long, and the curve is steepest at
   * the start, so a long frame there would jump the message instead of
   * gliding it.
   */
  const glideToPin = useCallback(
    (el: HTMLElement, pin: Pin, target: number) => {
      const reach = el.clientHeight
      el.scrollTop = Math.min(
        Math.max(el.scrollTop, target - reach),
        target + reach
      )
      if (prefersReducedMotion() || Math.abs(target - el.scrollTop) < 1) {
        el.scrollTop = target
        settle(el, el.scrollTop)
        return
      }
      const from = el.scrollTop
      let elapsed = 0
      let last: number | null = null
      const step = (now: number) => {
        if (!pin.glide || state.current.pin !== pin) return
        const message = pinnedElement(el)
        if (!message) {
          // Gone mid-glide (a failed send, a re-keyed bubble): end the glide,
          // so the scroll button and scroll handling stop treating it as
          // moving, and glide again from wherever it is if it comes back.
          pin.glide = null
          pin.placed = false
          settle(el, el.scrollTop)
          return
        }
        elapsed += Math.min(now - (last ?? now), PIN_GLIDE_MAX_FRAME_MS)
        last = now
        const progress = Math.min(1, elapsed / PIN_GLIDE_MS)
        const aim = Math.min(pinTarget(el, message), maxScrollTop(el))
        el.scrollTop = from + (aim - from) * easeOutQuint(progress)
        if (progress < 1) pin.glide.frame = window.requestAnimationFrame(step)
        else pin.glide = null
        settle(el, el.scrollTop)
      }
      const glide: Glide = {
        frame: window.requestAnimationFrame(() => {
          glide.frame = window.requestAnimationFrame(step)
        }),
      }
      pin.glide = glide
      settle(el, el.scrollTop)
    },
    [pinnedElement, settle]
  )

  const holdPin = useCallback(
    (el: HTMLElement): boolean => {
      const pin = state.current.pin
      if (!pin?.hold) return false
      const message = pinnedElement(el)
      if (!message) return false
      state.current.followTail = false
      const target = Math.min(pinTarget(el, message), maxScrollTop(el))
      if (!pin.placed) {
        pin.placed = true
        glideToPin(el, pin, target)
      } else if (!pin.glide) {
        el.scrollTop = target
        settle(el, el.scrollTop)
      }
      return true
    },
    [glideToPin, pinnedElement, settle]
  )

  /**
   * Put the reader back where they were after an older page prepended: the
   * content above them grew by exactly the difference in scroll height, so
   * adding it to their offset leaves the same pixels on screen.
   */
  const restoreAfterPrepend = useCallback(
    (el: HTMLElement): boolean => {
      const anchor = state.current.prependAnchor
      if (!anchor) return false
      const grown = el.scrollHeight - anchor.scrollHeight
      if (grown <= 0) return false
      state.current.prependAnchor = null
      el.scrollTop = anchor.scrollTop + grown
      settle(el, el.scrollTop)
      return true
    },
    [settle]
  )

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

  /** Content or viewport changed: put the transcript where it belongs. */
  const reconcile = useCallback(
    (el: HTMLElement) => {
      const awaitingPin = awaitPendingPin(el)
      fitReserve(el)
      if (restoreAfterPrepend(el)) return
      if (holdPin(el)) return
      if (applyPendingRestore(el)) return
      if (state.current.followTail && !awaitingPin) {
        jumpToBottom(el)
        return
      }
      const target = Math.min(state.current.manualTop, maxScrollTop(el))
      if (Math.abs(el.scrollTop - target) > el.clientHeight * 0.5) {
        el.scrollTop = target
      }
      state.current.previousTop = el.scrollTop
      setShowScrollToBottom(!isNearBottom(el))
    },
    [
      applyPendingRestore,
      awaitPendingPin,
      fitReserve,
      holdPin,
      jumpToBottom,
      restoreAfterPrepend,
    ]
  )

  const clearPin = useCallback(() => {
    cancelGlide()
    state.current.pin = null
    state.current.pendingPin = null
    reserveRef.current?.style.removeProperty("height")
  }, [cancelGlide])

  /** The user scrolled: stop holding any sent message in place. */
  const releasePin = useCallback(() => {
    cancelGlide()
    if (state.current.pin) state.current.pin.hold = false
    state.current.pendingPin = null
  }, [cancelGlide])

  /** Pin a message the user is sending to the top once it renders. */
  const pinMessage = useCallback((messageId: string) => {
    state.current.pendingPin = { messageId, since: performance.now() }
    state.current.pendingRestoreTop = null
  }, [])

  /** Called when an older page is asked for, so the prepend can be undone. */
  const capturePrependAnchor = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    state.current.prependAnchor = {
      scrollHeight: el.scrollHeight,
      scrollTop: el.scrollTop,
    }
  }, [])

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current
    releasePin()
    state.current.followTail = true
    state.current.pendingRestoreTop = null
    if (el) jumpToBottom(el)
  }, [jumpToBottom, releasePin])

  // Entering a transcript: resume where the user left it, or follow the tail
  // when they were pinned there (or have never seen it).
  useLayoutEffect(() => {
    const el = scrollRef.current
    if (!el) return
    clearPin()
    const remembered = scrollKey
      ? rememberedPositions.get(scrollKey)
      : undefined
    if (remembered && !remembered.atBottom) {
      state.current.followTail = false
      state.current.pendingRestoreTop = remembered.top
      applyPendingRestore(el)
      return
    }
    scrollToBottom()
  }, [applyPendingRestore, clearPin, scrollKey, scrollToBottom])

  useLayoutEffect(() => {
    const el = scrollRef.current
    if (!el) return
    reconcile(el)
    // Once the transcript has content a remembered offset is restored; later
    // growth must not yank the user back to it.
    if (messages.length > 0) state.current.pendingRestoreTop = null
  }, [isStreaming, messages, reconcile])

  // Only the user's own scrolling decides whether we keep following the tail.
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    let userScrollUntil = 0
    let draggingScrollbar = false
    const userIsScrolling = () =>
      draggingScrollbar || performance.now() < userScrollUntil
    const markUserScroll = () => {
      userScrollUntil = performance.now() + USER_SCROLL_WINDOW_MS
    }
    const handleKey = (event: KeyboardEvent) => {
      if (SCROLL_KEYS.has(event.key) && !isEditable(event.target)) {
        markUserScroll()
      }
    }
    // A press on the scroller itself, not its content, is on its scrollbar.
    const handlePointerDown = (event: PointerEvent) => {
      if (event.target === el) draggingScrollbar = true
    }
    const handlePointerUp = () => {
      if (!draggingScrollbar) return
      draggingScrollbar = false
      markUserScroll()
    }
    const handleScroll = () => {
      const top = el.scrollTop
      const nearBottom = isNearBottom(el)
      // Our own writes settle `previousTop` to where they land, and the browser
      // clamping to a shorter transcript lands exactly on the new bottom. Any
      // other move is the user's even without input we can see: find in page,
      // drag-select autoscroll, focus moving to an off-screen control.
      // A glide's frames are ours too, until it lands.
      const ours =
        Math.abs(top - state.current.previousTop) <= 1 ||
        !!state.current.pin?.glide
      const clamped =
        top < state.current.previousTop && top >= maxScrollTop(el) - 1
      if (userIsScrolling() || (!ours && !clamped)) {
        releasePin()
        if (top < state.current.previousTop - 1) {
          state.current.followTail = false
        }
      }
      if (nearBottom) state.current.followTail = true
      settle(el, top)
      if (scrollKey) remember(scrollKey, { top, atBottom: nearBottom })
    }
    const doc = el.ownerDocument
    el.addEventListener("scroll", handleScroll, { passive: true })
    el.addEventListener("wheel", markUserScroll, { passive: true })
    el.addEventListener("touchmove", markUserScroll, { passive: true })
    el.addEventListener("touchend", markUserScroll, { passive: true })
    el.addEventListener("pointerdown", handlePointerDown)
    doc.addEventListener("pointerup", handlePointerUp)
    doc.addEventListener("keydown", handleKey)
    return () => {
      el.removeEventListener("scroll", handleScroll)
      el.removeEventListener("wheel", markUserScroll)
      el.removeEventListener("touchmove", markUserScroll)
      el.removeEventListener("touchend", markUserScroll)
      el.removeEventListener("pointerdown", handlePointerDown)
      doc.removeEventListener("pointerup", handlePointerUp)
      doc.removeEventListener("keydown", handleKey)
    }
  }, [releasePin, scrollKey, settle])

  useEffect(() => {
    const scroller = scrollRef.current
    const content = contentRef.current
    if (!scroller || !content || typeof ResizeObserver === "undefined") return
    const observer = new ResizeObserver(() => reconcile(scroller))
    observer.observe(scroller)
    observer.observe(content)
    return () => observer.disconnect()
  }, [reconcile])

  useEffect(() => cancelGlide, [cancelGlide])

  return {
    scrollRef,
    contentRef,
    reserveRef,
    showScrollToBottom,
    scrollToBottom,
    pinMessage,
    capturePrependAnchor,
  }
}
