/**
 * The hidden way in: four quick clicks on the Robin logo. The game's code and
 * the season start loading on the second click, so the fourth opens at once.
 */
import {
  lazy,
  Suspense,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react"
import { useQueryClient } from "@tanstack/react-query"

import { LogoMark } from "@/components/Branding"
import { productAnalytics } from "@/lib/analytics"
import { cn } from "@/lib/utils"

import { GameBoundary } from "./GameBoundary"
import { seasonQuery } from "./season"

const CLICKS = 4
/** All four clicks fit in about two-thirds of a second. */
const WITHIN_MS = 670

const loadGame = () => import("./TraceFlightGame")
/** Layers that open over the game, such as the command palette, and own their focus while open. */
const OTHER_LAYERS =
  '[role="dialog"],[role="alertdialog"],[role="menu"],[role="listbox"]'
const TABBABLE =
  'button:not([disabled]),[href],input:not([disabled]),select,textarea,[tabindex]:not([tabindex="-1"])'

/** Keeps Tab inside the game, the way a modal dialog does. */
function containTab(event: React.KeyboardEvent<HTMLElement>) {
  const inside = Array.from(
    event.currentTarget.querySelectorAll<HTMLElement>(TABBABLE)
  ).filter((element) => !element.closest("[hidden]"))
  const first = inside.at(0)
  const last = inside.at(-1)
  const at = document.activeElement
  if (!first || !last) return event.preventDefault()
  if (event.shiftKey && (at === first || at === event.currentTarget)) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && at === last) {
    event.preventDefault()
    first.focus()
  }
}
const TraceFlightGame = lazy(() =>
  loadGame().then((module) => ({ default: module.TraceFlightGame }))
)

export interface TraceFlightLauncher {
  /** The Robin logo, to click four times. */
  readonly logo: ReactNode
  readonly playing: boolean
  /** The game, to lay over the page it launched from; null when closed. */
  readonly game: ReactNode
}

export function useTraceFlight(): TraceFlightLauncher {
  const ref = useRef<HTMLButtonElement>(null)
  const clicks = useRef<number[]>([])
  const queryClient = useQueryClient()
  const [playing, setPlaying] = useState(false)
  // The logo stays until the bird leaps out of it: the first open may still be loading the game.
  const [departed, setDeparted] = useState(false)
  const close = () => {
    setPlaying(false)
    setDeparted(false)
  }
  const played = useRef(false)
  const dialog = useRef<HTMLDivElement>(null)

  // While you play, focus that lands outside the game (a hidden control dropping it
  // to the page, a click on the header) comes back, and a key pressed out there
  // reaches neither the page's handlers nor its shortcuts. Another layer, such as
  // the command palette, keeps its focus: two traps would pull it back and forth.
  useEffect(() => {
    if (!playing) return
    const stray = (target: EventTarget | null) =>
      !(target instanceof Element && target.closest(OTHER_LAYERS)) &&
      !(target instanceof Node && dialog.current?.contains(target))
    // The game takes its keys on its own region; the wrapper only holds loading and error screens.
    const recover = () =>
      (
        dialog.current?.querySelector<HTMLElement>('[role="application"]') ??
        dialog.current
      )?.focus()
    // A layer that closes without a trigger to return focus to (the command
    // palette) drops it on the page, which fires no focus event; its removal does show.
    const layerClosed = new MutationObserver(() => {
      if (document.activeElement !== document.body) return
      layerClosed.disconnect()
      recover()
    })
    const reclaim = (event: FocusEvent) => {
      if (stray(event.target)) recover()
      else if (!dialog.current?.contains(event.target as Node))
        layerClosed.observe(document.body, { childList: true, subtree: true })
    }
    const strayKey = (event: KeyboardEvent) => {
      if (!stray(document.activeElement)) return
      // Browser shortcuts (reload, print) keep their defaults; only Tab is the game's.
      if (event.key === "Tab") event.preventDefault()
      event.stopPropagation()
      recover()
    }
    document.addEventListener("focusin", reclaim)
    document.addEventListener("keydown", strayKey, true)
    return () => {
      layerClosed.disconnect()
      document.removeEventListener("focusin", reclaim)
      document.removeEventListener("keydown", strayKey, true)
    }
  }, [playing])

  // Focus can only return once the page is no longer inert, so after the render that closes the game.
  useEffect(() => {
    // A game that mounted in this same commit has already focused itself; keep that.
    if (playing && !dialog.current?.contains(document.activeElement))
      dialog.current?.focus()
    else if (played.current) ref.current?.focus()
    played.current = playing
  }, [playing])

  function onClick() {
    const now = performance.now()
    const recent = [...clicks.current.filter((at) => now - at < WITHIN_MS), now]
    clicks.current = recent
    if (recent.length === 2) {
      loadGame().catch(() => undefined)
      void queryClient.prefetchQuery(seasonQuery)
    }
    if (recent.length < CLICKS || playing) return
    clicks.current = []
    productAnalytics.track("trace_flight_opened", {})
    setPlaying(true)
  }

  const game = playing ? (
    // A modal dialog that ignores app hotkeys, so the page's shortcuts stand down while you play.
    <div
      ref={dialog}
      role="dialog"
      aria-modal="true"
      aria-label="Trace Flight"
      tabIndex={-1}
      data-hotkeys="ignore"
      className="absolute inset-0 z-floating-bar outline-none"
      onKeyDown={(event) => {
        if (event.key === "Tab") containTab(event)
        // The game handles its own Esc; this one leaves while it loads, or if it failed to.
        else if (event.key === "Escape" && !event.defaultPrevented) {
          event.preventDefault()
          close()
        }
      }}
    >
      <GameBoundary onExit={close}>
        <Suspense>
          <TraceFlightGame
            home={() => ref.current?.getBoundingClientRect() ?? null}
            onDepart={() => setDeparted(true)}
            onExit={close}
          />
        </Suspense>
      </GameBoundary>
    </div>
  ) : null

  const logo = (
    <button
      ref={ref}
      type="button"
      aria-label="Robin"
      onClick={onClick}
      className="shrink-0 cursor-default rounded-sm focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none"
    >
      {/* While you play, the bird is out of the logo. */}
      <LogoMark
        alt=""
        className={cn("block size-8", playing && departed && "invisible")}
      />
    </button>
  )

  return { logo, playing, game }
}
