/**
 * The game harness: the real TraceFlight view with a debug panel beside it and
 * a test API (`window.traceFlight`) for agents and Playwright. Links with a
 * seed, inputs and a tick reproduce any frame.
 */
import { useEffect, useRef, useState } from "react"
import { Button } from "@langchain/macaw-components/Button"
import { CopyButton } from "@langchain/macaw-components/CopyButton"
import { Input } from "@langchain/macaw-components/Input"
import { Switch } from "@langchain/macaw-components/Switch"

import { replay, type EndReason, type Run } from "../../core/replay"
import {
  BIRD_RADIUS,
  DEFAULT_TUNING,
  Q,
  START_Y,
  type Tuning,
} from "../../core/rules"
import { modeOf } from "../../core/sim"
import { currentLevel, stateHash } from "../../core/state"
import { BIRD_SIZE } from "../../view/drawBird"
import type { Theme } from "../../view/palette"
import { quit, runOf, type Session } from "../../view/session"
import { TraceFlight, type TraceFlightHandle } from "../../view/TraceFlight"
import { ParamSlider, Section } from "./controls"
import { memoryScoreStore } from "./scoreStore"

export interface HarnessState {
  readonly seed: number
  readonly ready: boolean
  readonly ended: EndReason | null
  readonly tick: number
  readonly score: number
  readonly passed: number
  readonly tokens: number
  readonly lives: number
  readonly level: number
  readonly mode: string
  readonly y: number
  readonly hash: number | null
}

interface HarnessApi {
  readonly load: (seed: number) => void
  readonly restart: () => void
  readonly pause: () => void
  readonly resume: () => void
  readonly step: (ticks: number) => void
  readonly flap: () => void
  readonly setBot: (on: boolean) => void
  readonly state: () => HarnessState
  readonly run: () => Run | null
  /** Whether the server's replay of this run reaches the same state (default tuning only). */
  readonly replayMatches: () => boolean | null
}

declare global {
  interface Window {
    /** Dev-only hook for agents and Playwright to drive the game tick by tick. */
    traceFlight?: HarnessApi
  }
}

/** Sample rivals so the score card's board has rows to rank against. */
const RIVALS = [
  { name: "ship-it", score: 118 },
  { name: "rubber-duck", score: 97 },
  { name: "yak-shaver", score: 84 },
  { name: "tab-hoarder", score: 58 },
  { name: "flaky-test", score: 31 },
]

function replayMatches(session: Session, tuning: Tuning): boolean | null {
  const run = runOf(session)
  if (!run || !session.sim || tuning.birdRadius !== DEFAULT_TUNING.birdRadius)
    return null
  const result = replay(run)
  return (
    typeof result !== "string" &&
    stateHash(result.state) === stateHash(session.sim)
  )
}

function stateOf(session: Session): HarnessState {
  const sim = session.sim
  return {
    seed: session.seed,
    ready: sim === null,
    ended: session.ended,
    tick: sim?.tick ?? 0,
    score: sim?.score ?? 0,
    passed: sim?.passed ?? 0,
    tokens: sim?.tokens ?? 0,
    lives: sim?.lives ?? 0,
    level: sim ? currentLevel(sim) : 1,
    mode: sim ? modeOf(sim.effect) : "ready",
    y: sim ? sim.y / Q : START_Y,
    hash: sim ? stateHash(sim) : null,
  }
}

function numberParam(search: URLSearchParams, key: string, fallback: number) {
  const value = Number(search.get(key))
  return search.has(key) && Number.isFinite(value) ? value : fallback
}

/** `?inputs=0,40,35` are flap deltas, as a run submits them. */
function scriptedFlaps(search: URLSearchParams): ReadonlySet<number> {
  const deltas = (search.get("inputs") ?? "")
    .split(",")
    .filter(Boolean)
    .map(Number)
  let at = 0
  return new Set(deltas.filter(Number.isInteger).map((delta) => (at += delta)))
}

export function GameHarness() {
  const search = new URLSearchParams(window.location.search)
  const [seed, setSeed] = useState(numberParam(search, "seed", 1) >>> 0)
  const [paused, setPaused] = useState(search.has("manual"))
  const [speed, setSpeed] = useState(1)
  const [bot, setBot] = useState(search.has("bot"))
  const [hitbox, setHitbox] = useState(search.has("hitboxes"))
  const [boxes, setBoxes] = useState(search.has("boxes"))
  const [reducedMotion, setReducedMotion] = useState(search.has("reduced"))
  const [radius, setRadius] = useState(
    numberParam(search, "radius", BIRD_RADIUS)
  )
  const [birdSize, setBirdSize] = useState(
    numberParam(search, "size", BIRD_SIZE)
  )
  const [theme, setTheme] = useState<Theme>(
    search.get("theme") === "dark" ? "dark" : "light"
  )
  const [panel, setPanel] = useState<{
    readonly info: HarnessState
    readonly run: Run | null
  } | null>(null)
  const shown = useRef("")
  const [replayCheck, setReplayCheck] = useState<boolean | null>(null)
  const [scoreStore] = useState(() => memoryScoreStore(RIVALS))
  const [scripted] = useState(() => scriptedFlaps(search))
  const [startTick] = useState(() => numberParam(search, "tick", 0))
  const handle = useRef<TraceFlightHandle | null>(null)
  const tuning: Tuning = { birdRadius: radius }
  const live = useRef({ seed, tuning })

  useEffect(() => {
    live.current = { seed, tuning }
  })

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark")
  }, [theme])

  function load(next: number) {
    setReplayCheck(null)
    if (next >>> 0 === live.current.seed) handle.current?.restart()
    else setSeed(next >>> 0)
  }

  /** The panel's numbers refresh a few times a second, not every frame. */
  function onFrame(session: Session) {
    const info = stateOf(session)
    const key = `${info.tick >> 4}|${info.ended}|${info.ready}`
    if (key === shown.current) return
    shown.current = key
    setPanel({ info, run: info.ready ? null : runOf(session) })
  }

  // The test API reads through the handle, which follows every restart.
  useEffect(() => {
    const session = () => handle.current?.session() ?? null
    window.traceFlight = {
      load,
      restart: () => handle.current?.restart(),
      pause: () => setPaused(true),
      resume: () => setPaused(false),
      step: (ticks) => handle.current?.step(ticks),
      flap: () => handle.current?.flap(),
      setBot,
      state: () => {
        const s = session()
        if (!s) throw new Error("The game hasn't started yet")
        return stateOf(s)
      },
      run: () => {
        const s = session()
        return s ? runOf(s) : null
      },
      replayMatches: () => {
        const s = session()
        return s ? replayMatches(s, live.current.tuning) : null
      },
    }
    return () => {
      delete window.traceFlight
    }
    // Set once; the API reads live values through refs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const info = panel?.info ?? null
  const run = panel?.run ?? null

  return (
    <div className="grid h-svh grid-cols-[minmax(0,1fr)_22rem] bg-surface-level-1 text-primary">
      <TraceFlight
        seed={seed}
        scoreStore={scoreStore}
        theme={theme}
        reducedMotion={reducedMotion}
        onExit={() => handle.current?.restart()}
        dev={{
          paused,
          speed,
          bot,
          tuning,
          birdSize,
          boxes,
          hitbox,
          scripted,
          startTick,
          onHandle: (next) => {
            handle.current = next
          },
          onFrame,
        }}
      />
      <aside className="flex min-h-0 flex-col gap-space-5 overflow-y-auto border-l border-muted bg-surface-level-2 p-space-4">
        <Section title="Page">
          <Button
            size="sm"
            variant="outlined"
            color="secondary"
            onClick={() => {
              window.location.search = ""
            }}
          >
            Open the bird lab
          </Button>
          <Switch
            label="Dark theme"
            checked={theme === "dark"}
            onChange={(dark) => setTheme(dark ? "dark" : "light")}
          />
          <Switch
            label="Reduced motion"
            checked={reducedMotion}
            onChange={setReducedMotion}
          />
        </Section>

        <Section title="Run">
          <Input
            label="Seed"
            type="number"
            value={String(seed)}
            onChange={(value) => {
              const next = Number(value)
              if (Number.isInteger(next) && next >= 0) load(next)
            }}
          />
          <div className="flex flex-wrap gap-space-2">
            <Button size="sm" onClick={() => setPaused((p) => !p)}>
              {paused ? "Resume clock" : "Hold clock"}
            </Button>
            <Button
              size="sm"
              variant="outlined"
              color="secondary"
              onClick={() => handle.current?.step(1)}
            >
              Step 1 tick
            </Button>
            <Button
              size="sm"
              variant="outlined"
              color="secondary"
              onClick={() => {
                const s = handle.current?.session()
                if (s) quit(s)
              }}
            >
              End run
            </Button>
          </div>
          <ParamSlider
            label="Speed"
            value={speed}
            min={0.1}
            max={1}
            step={0.05}
            onChange={setSpeed}
          />
          <Switch label="Let the bot play" checked={bot} onChange={setBot} />
          <Switch label="Show hitbox" checked={hitbox} onChange={setHitbox} />
          <Switch
            label="Show collision boxes"
            checked={boxes}
            onChange={setBoxes}
          />
        </Section>

        <Section title="Tuning">
          <ParamSlider
            label={`Hitbox radius (rule; default ${BIRD_RADIUS})`}
            value={radius}
            min={10}
            max={60}
            step={1}
            onChange={(value) => {
              setRadius(value)
              setReplayCheck(null)
            }}
          />
          <ParamSlider
            label="Bird drawing width (view only)"
            value={birdSize}
            min={60}
            max={220}
            step={2}
            onChange={setBirdSize}
          />
        </Section>

        <Section title="This run">
          <p className="text-xs text-secondary tabular-nums">
            {info
              ? `tick ${info.tick} · score ${info.score} (${info.passed} passed + ${info.tokens} tokens) · level ${info.level} · lives ${info.lives} · ${info.mode}${info.ended ? ` · ended: ${info.ended}` : ""}`
              : ""}
          </p>
          <div className="flex flex-wrap gap-space-2">
            <Button
              size="sm"
              variant="outlined"
              color="secondary"
              disabled={!run}
              onClick={() => {
                const s = handle.current?.session()
                setReplayCheck(s ? replayMatches(s, tuning) : null)
              }}
            >
              Check replay
            </Button>
            <CopyButton
              copy={run ? JSON.stringify(run) : ""}
              copyText="Copy run"
            />
          </div>
          {replayCheck !== null && (
            <p className="text-xs text-secondary">
              {replayCheck
                ? "Replay matches: the server would score this run the same."
                : "Replay differs from this run."}
            </p>
          )}
        </Section>
      </aside>
    </div>
  )
}
