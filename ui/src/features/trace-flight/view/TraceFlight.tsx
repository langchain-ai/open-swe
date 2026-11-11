/**
 * The game. Everything outside comes in as props (where scores go, the theme,
 * reduced motion, how to leave), so the harness and the app host the same
 * component. Drawing stays out of React: the loop steps the session and paints
 * the canvas each frame; React renders only the controls and the score card.
 */
import {
  type KeyboardEvent,
  useEffect,
  useEffectEvent,
  useRef,
  useState,
} from "react"
import { Button } from "@langchain/macaw-components/Button"

import type { EndReason } from "../core/replay"
import { TICK_CAP, type Tuning } from "../core/rules"
import { currentLevel } from "../core/state"
import { DEFAULT_PARAMS } from "../pose/params"
import { lerpPose, type BirdPose } from "../pose/rig"
import { TICK_SECONDS } from "../pose/springs"
import { sizeCanvas } from "./canvas"
import { drawScene, type SceneLaunch } from "./draw/scene"
import { BIRD_SIZE } from "./drawBird"
import { FLAP_KEYS, isControl } from "./input"
import { LEAP_MS, leapAt } from "./leap"
import { readPalette, type Palette, type Theme } from "./palette"
import { ScoreCard } from "./ScoreCard"
import type { BestRun, RunResult, ScoreStore } from "./scoreStore"
import {
  advance,
  newSession,
  PLAYER,
  quit,
  runOf,
  type Session,
} from "./session"
import { traceOf, type Trace } from "./trace"

export interface TraceFlightProps {
  readonly seed: number
  readonly scoreStore: ScoreStore
  readonly theme: Theme
  readonly reducedMotion: boolean
  readonly onExit: () => void
  /**
   * Where the bird lives when you're not playing (the logo), in viewport pixels.
   * Given, the bird leaps out of it to start and flies back before `onExit`.
   */
  readonly home?: () => DOMRectReadOnly | null
  /** The bird has left its home and the game is starting to show; the host hides its logo now. */
  readonly onDepart?: () => void
  /** A run ended: what it scored on your side, for the host's analytics. */
  readonly onRunEnd?: (summary: RunSummary) => void
  /** The score store refused a run; the host decides whether play can go on. */
  readonly onSubmitError?: (error: unknown) => void
  /** Harness controls; the app leaves this out. */
  readonly dev?: TraceFlightDev
}

export interface TraceFlightDev {
  /** Holds the clock so the host can step tick by tick. */
  readonly paused: boolean
  readonly speed: number
  readonly bot: boolean
  readonly tuning: Tuning
  /** Drawn width of the bird, world units. */
  readonly birdSize: number
  readonly boxes: boolean
  readonly hitbox: boolean
  /** Flap ticks from a shared link, and the tick to fast-forward to. */
  readonly scripted: ReadonlySet<number>
  readonly startTick: number
  /** Called with a handle on the live run after every start. */
  readonly onHandle: (handle: TraceFlightHandle) => void
  /** Called once a frame with the live run, for the host's own readouts. */
  readonly onFrame: (session: Session) => void
}

export interface RunSummary {
  readonly score: number
  readonly level: number
  readonly endReason: EndReason
}

export interface TraceFlightHandle {
  readonly session: () => Session
  readonly step: (ticks: number) => void
  readonly flap: () => void
  readonly restart: () => void
}

const MAX_CATCH_UP_TICKS = 5
/** Fly back into the logo, or just fade either way with reduced motion; milliseconds. */
const LAND_MS = 650
const FADE_MS = 250
/** World units the flight home arcs above the straight line. */
const LAND_LIFT = 140

type Phase = "in" | "play" | "out"

function easeInOut(t: number): number {
  return t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2
}

/** Where the flying bird and the fade were on a frame, so leaving mid-leap can start from there. */
interface FlightFrame {
  readonly along: number
  readonly lift: number
  readonly fade: number
  readonly pose: BirdPose | undefined
}

/** The leap's last stretch, where the bird eases into the pose the game starts from. */
const SETTLE_FROM = 0.8

export function TraceFlight(props: TraceFlightProps) {
  const { seed, dev, scoreStore } = props
  const regionRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const sessionRef = useRef<Session>(newSession(seed, dev?.scripted))
  const propsRef = useRef(props)
  const traceRef = useRef<Trace | null>(null)
  const bestRef = useRef<number | null>(null)
  const [userPaused, setUserPaused] = useState(false)
  const pausedRef = useRef(false)
  const [over, setOver] = useState(false)
  /** The run's own score; the server's replay always agrees, so the card needn't wait for it. */
  const [score, setScore] = useState(0)
  const [result, setResult] = useState<RunResult | null>(null)
  const [saveFailed, setSaveFailed] = useState(false)
  const [announcement, setAnnouncement] = useState("")
  const [phase, setPhase] = useState<Phase>(props.home ? "in" : "play")
  /** When the current leap or fade began; the loop reads it, React only shows or hides controls. */
  const flight = useRef<{
    phase: Exclude<Phase, "play">
    start: number
    /** A flight home that turned back mid-leap starts from this frame. */
    from?: FlightFrame
  } | null>(props.home ? { phase: "in", start: -1 } : null)
  const lastFlight = useRef<FlightFrame | null>(null)
  function clearCard() {
    setOver(false)
    setResult(null)
    setSaveFailed(false)
  }
  // A new course clears the last run's card while rendering, not in an effect.
  const [course, setCourse] = useState(seed)
  if (course !== seed) {
    setCourse(seed)
    clearCard()
  }

  useEffect(() => {
    propsRef.current = props
    pausedRef.current = userPaused || (props.dev?.paused ?? false)
  })

  /** Sessions whose run has gone to the score store; each goes once. */
  const submitted = useRef(new WeakSet<Session>())
  /** Only the latest request for your best run may set the trace. */
  const bestRequest = useRef(0)

  // Callbacks the loop holds read props through this ref, never a stale render's.
  function loadBest() {
    const p = propsRef.current
    const request = ++bestRequest.current
    const show = (best: BestRun | null) => {
      if (request !== bestRequest.current) return
      traceRef.current = best ? traceOf(best.run) : null
      bestRef.current = best?.score ?? null
    }
    p.scoreStore.best(p.seed).then(show, () => show(null))
  }

  /**
   * Sends a started run to the score store, once, and tells the host how it
   * ended and whether the store refused it; a run that never started sends nothing.
   */
  function submit(session: Session): Promise<RunResult> | null {
    if (submitted.current.has(session)) return null
    quit(session)
    const run = runOf(session)
    if (!run || !session.sim || !session.ended) return null
    submitted.current.add(session)
    const p = propsRef.current
    p.onRunEnd?.({
      score: session.sim.score,
      level: currentLevel(session.sim),
      endReason: session.ended,
    })
    const sent = p.scoreStore.submit(run)
    sent.catch(refused)
    return sent
  }

  /** A refusal that lands while the bird flies home waits for it, so the host can't cancel the landing. */
  const refusedDuringLanding = useRef<{ error: unknown } | null>(null)
  function refused(error: unknown) {
    if (flight.current?.phase === "out")
      refusedDuringLanding.current = { error }
    else propsRef.current.onSubmitError?.(error)
  }

  function controls() {
    const d = propsRef.current.dev
    return d ? { bot: d.bot, tuning: d.tuning } : PLAYER
  }

  function step(ticks: number) {
    const count = Math.min(Math.max(0, Math.floor(ticks)), TICK_CAP)
    for (let i = 0; i < count && !sessionRef.current.ended; i++)
      advance(sessionRef.current, controls())
  }

  function freshSession() {
    const p = propsRef.current
    submit(sessionRef.current)?.then(loadBest, () => undefined)
    sessionRef.current = newSession(p.seed, p.dev?.scripted)
    regionRef.current?.focus()
    p.dev?.onHandle(handle)
  }

  function restart() {
    freshSession()
    clearCard()
  }

  const handle: TraceFlightHandle = {
    session: () => sessionRef.current,
    step,
    flap: () => {
      sessionRef.current.queued = true
    },
    restart,
  }

  function finish(session: Session) {
    setOver(true)
    const final = session.sim?.score ?? 0
    setScore(final)
    setAnnouncement(`Run over. Score ${final}.`)
    // A late answer for a run you've already left only refreshes your best.
    submit(session)?.then(
      (scored) => {
        loadBest()
        if (sessionRef.current === session) setResult(scored)
      },
      () => {
        if (sessionRef.current === session) setSaveFailed(true)
      }
    )
  }

  function flightMs(leg: Exclude<Phase, "play">): number {
    if (propsRef.current.reducedMotion) return FADE_MS
    return leg === "in" ? LEAP_MS : LAND_MS
  }

  function exit() {
    const f = flight.current
    if (f?.phase === "out") return
    submit(sessionRef.current)?.catch(() => undefined)
    // Leaving before the leap's first frame has nothing to fly home from.
    if (!propsRef.current.home || (f?.phase === "in" && f.start < 0)) {
      flight.current = null
      return propsRef.current.onExit()
    }
    // Leaving mid-leap turns the bird around where it is rather than snapping it to the course.
    const from =
      f?.phase === "in" && f.start >= 0
        ? (lastFlight.current ?? undefined)
        : undefined
    flight.current = { phase: "out", start: -1, from }
    setPhase("out")
    // The controls hide during the flight; focus stays in the game rather than dropping to the page.
    regionRef.current?.focus()
  }

  /** Where the leap or fade is this frame; ends the flight when it lands. */
  function flightAt(now: number): SceneLaunch | undefined {
    const f = flight.current
    if (!f) return undefined
    const p = propsRef.current
    if (f.start < 0) {
      f.start = now
      if (f.phase === "in") p.onDepart?.()
    }
    const reduced = p.reducedMotion
    const done = Math.min(1, (now - f.start) / flightMs(f.phase))
    const resting = sessionRef.current.pose
    let frame: FlightFrame
    if (f.phase === "in" && !reduced) {
      const leap = leapAt(done)
      const settle = Math.max(0, (done - SETTLE_FROM) / (1 - SETTLE_FROM))
      frame = {
        along: leap.along,
        lift: leap.lift,
        // The game is in view by the top of the leap, before the bird settles.
        fade: Math.min(1, done / 0.6),
        pose: lerpPose(leap.pose, resting, easeInOut(settle)),
      }
    } else if (f.phase === "in") {
      const shown = easeInOut(done)
      frame = { along: shown, lift: 0, fade: shown, pose: undefined }
    } else if (f.from) {
      const left = 1 - easeInOut(done)
      frame = {
        along: f.from.along * left,
        lift: f.from.lift * left,
        fade: f.from.fade * left,
        pose: f.from.pose && lerpPose(f.from.pose, resting, 1 - left),
      }
    } else {
      const along = easeInOut(1 - done)
      frame = {
        along,
        lift: LAND_LIFT * Math.sin(Math.PI * along),
        fade: along,
        pose: undefined,
      }
    }
    lastFlight.current = frame
    if (done >= 1) {
      flight.current = null
      if (f.phase === "out") {
        const late = refusedDuringLanding.current
        refusedDuringLanding.current = null
        if (late) p.onSubmitError?.(late.error)
        p.onExit()
      } else setPhase("play")
    }
    const home = reduced ? null : p.home?.()
    const canvas = canvasRef.current?.getBoundingClientRect()
    return {
      fade: frame.fade,
      leap:
        home && canvas
          ? {
              x: home.x + home.width / 2 - canvas.left,
              y: home.y + home.height / 2 - canvas.top,
              size: home.width,
              along: frame.along,
              lift: frame.lift,
              pose: frame.pose,
            }
          : undefined,
    }
  }

  /** The run this seed's start effect made, so a replay of the effect can keep it. */
  const started = useRef<{ seed: number; session: Session } | null>(null)

  // A new seed is a new course; fast-forward for a shared link. Strict Mode
  // replays this effect: the replay keeps the run the first pass started, rather
  // than settling it as a run the player left and stepping a second one.
  const startRun = useEffectEvent(() => {
    const before = started.current
    if (before?.seed === seed && before.session === sessionRef.current) return
    freshSession()
    step(props.dev?.startTick ?? 0)
    started.current = { seed, session: sessionRef.current }
  })
  useEffect(() => startRun(), [seed])

  // A new course or store shows no trace until its own best arrives.
  const refreshBest = useEffectEvent(() => {
    traceRef.current = null
    bestRef.current = null
    loadBest()
  })
  useEffect(() => refreshBest(), [seed, scoreStore])

  /** What one frame remembers for the next, besides the session itself. */
  const frameMemory = useRef({
    theme: null as Theme | null,
    palette: null as Palette | null,
    lives: null as number | null,
  })

  const onFrame = useEffectEvent(
    (now: number, elapsed: number, away: boolean) => {
      const memory = frameMemory.current
      const session = sessionRef.current
      const launch = flightAt(now)
      // In the app the game holds still while something else, such as the command
      // palette, has the keyboard; the harness keeps running while you use its panel.
      const elsewhere =
        !props.dev && !regionRef.current?.contains(document.activeElement)
      if (elsewhere) session.accumulator = 0
      const paused =
        pausedRef.current || away || elsewhere || launch !== undefined
      if (!paused) {
        session.accumulator += elapsed * (props.dev?.speed ?? 1)
        let steps = 0
        while (
          session.accumulator >= TICK_SECONDS &&
          steps < MAX_CATCH_UP_TICKS
        ) {
          advance(session, controls())
          session.accumulator -= TICK_SECONDS
          steps += 1
        }
        if (steps === MAX_CATCH_UP_TICKS) session.accumulator = 0
      }
      props.dev?.onFrame(session)
      if (session.ended && !submitted.current.has(session)) finish(session)
      const lives = session.sim?.lives ?? null
      if (memory.lives !== null && lives !== null && lives !== memory.lives)
        if (!session.ended)
          setAnnouncement(`${lives} ${lives === 1 ? "life" : "lives"} left.`)
      memory.lives = lives

      if (memory.theme !== props.theme) {
        memory.palette = readPalette(props.theme)
        memory.theme = props.theme
      }
      const canvas = canvasRef.current
      const sized = canvas && sizeCanvas(canvas)
      if (!sized || !memory.palette) return
      const t = paused || session.ended ? 1 : session.accumulator / TICK_SECONDS
      drawScene(
        sized.ctx,
        sized.width,
        sized.height,
        {
          sim: session.sim,
          prev: session.prev,
          t,
          pose: lerpPose(session.prevPose, session.pose, t),
          ended: session.ended !== null,
          collected: session.collected,
          trace: traceRef.current,
          best: bestRef.current,
          launch,
        },
        {
          palette: memory.palette,
          reducedMotion: props.reducedMotion,
          seed: session.seed,
          tuning: controls().tuning,
          birdSize: props.dev?.birdSize ?? BIRD_SIZE,
          wing: DEFAULT_PARAMS.wing,
          debug: props.dev && {
            boxes: props.dev.boxes,
            hitbox: props.dev.hitbox,
          },
        }
      )
    }
  )

  useEffect(() => {
    let frame = 0
    let last = performance.now()
    // A blurred or hidden page pauses, and comes back without catch-up ticks.
    let away = document.hidden || !document.hasFocus()
    const onAway = () => {
      away = document.hidden || !document.hasFocus()
      sessionRef.current.accumulator = 0
      last = performance.now()
    }
    window.addEventListener("blur", onAway)
    window.addEventListener("focus", onAway)
    document.addEventListener("visibilitychange", onAway)
    const render = (now: number) => {
      onFrame(now, Math.min(0.1, (now - last) / 1000), away)
      last = now
      frame = requestAnimationFrame(render)
    }
    frame = requestAnimationFrame(render)
    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener("blur", onAway)
      window.removeEventListener("focus", onAway)
      document.removeEventListener("visibilitychange", onAway)
    }
  }, [])

  function onKeyDown(event: KeyboardEvent) {
    // Esc leaves from anywhere in the game, the score card's buttons and the leap in
    // included; the game owns every Esc, held ones too, so the host never cuts a flight short.
    if (event.code === "Escape") {
      event.preventDefault()
      if (!event.repeat) exit()
      return
    }
    if (event.repeat || phase !== "play") return
    if (isControl(event.target)) return
    if (FLAP_KEYS.has(event.code)) {
      event.preventDefault()
      if (over) restart()
      else sessionRef.current.queued = true
    } else if (event.code === "KeyP" && !over) setUserPaused((p) => !p)
  }

  return (
    <div
      ref={regionRef}
      role="application"
      aria-label="Trace Flight"
      tabIndex={0}
      onKeyDown={onKeyDown}
      className="relative size-full touch-none overflow-hidden outline-none select-none"
    >
      <canvas
        ref={canvasRef}
        className="absolute inset-0 block size-full"
        onPointerDown={(event) => {
          if (!event.isPrimary || over || phase !== "play") return
          regionRef.current?.focus()
          sessionRef.current.queued = true
        }}
      />
      <div
        hidden={phase !== "play"}
        className="absolute top-3 left-1/2 flex -translate-x-1/2 gap-space-1"
      >
        <Button
          size="sm"
          variant="plain"
          color="secondary"
          disabled={over}
          onClick={() => setUserPaused((p) => !p)}
        >
          {userPaused ? "Resume" : "Pause"}
        </Button>
        <Button size="sm" variant="plain" color="secondary" onClick={restart}>
          Restart
        </Button>
        <Button size="sm" variant="plain" color="secondary" onClick={exit}>
          Exit
        </Button>
      </div>
      {userPaused && !over && (
        <div className="pointer-events-none absolute inset-0 grid place-items-center">
          <span className="rounded-lg bg-elevated px-space-3 py-space-2 text-sm text-secondary">
            Paused. Press P to resume.
          </span>
        </div>
      )}
      {over && phase === "play" && (
        <div className="absolute inset-0 grid place-items-center">
          <div className="flex flex-col items-center gap-space-2">
            <ScoreCard
              score={score}
              result={result}
              onPlayAgain={restart}
              onExit={exit}
            />
            {saveFailed && (
              <span className="text-sm text-secondary">
                Couldn't save this run.
              </span>
            )}
          </div>
        </div>
      )}
      <div aria-live="polite" className="sr-only">
        {announcement}
      </div>
    </div>
  )
}
