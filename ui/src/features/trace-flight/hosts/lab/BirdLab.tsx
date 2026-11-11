import { useEffect, useRef, useState } from "react"
import { Button } from "@langchain/macaw-components/Button"
import { CopyButton } from "@langchain/macaw-components/CopyButton"
import { Switch } from "@langchain/macaw-components/Switch"

import { FLOOR, Q, SCROLL_Q8, START_Y, WORLD_HEIGHT } from "../../core/rules"
import { lerp } from "../../pose/geometry"
import {
  DEFAULT_PARAMS,
  type RigParams,
  type WingbeatParams,
} from "../../pose/params"
import {
  DEFAULT_POSES,
  JOINT_CHANNELS,
  JOINT_NAMES,
  KEY_POSE_NAMES,
  PERCHED,
  type JointChannel,
  type JointPose,
  type KeyPoseName,
} from "../../pose/poses"
import {
  INITIAL_RIG,
  lerpPose,
  poseOf,
  stepRig,
  type BirdPose,
  type RigState,
} from "../../pose/rig"
import type { SpringTuning } from "../../pose/springs"
import { TICK_SECONDS } from "../../pose/springs"
import { DEFAULT_WING, type WingShape } from "../../pose/wings"
import { BIRD_SIZE, drawBird } from "../../view/drawBird"
import { sizeCanvas } from "../../view/canvas"
import { FLAP_KEYS, isControl } from "../../view/input"
import type { Theme } from "../../view/palette"
import { ChoiceRow, ParamSlider, Section, SpringSliders } from "./controls"
import { READY, stepFlight, type LabCommand, type LabFlight } from "./labFlight"
import { SCENARIOS, type Scenario } from "./scenarios"

type View = "fly" | "pose"

interface TrailPoint {
  readonly pose: BirdPose
  readonly y: number
}

interface Sim {
  tick: number
  flight: LabFlight
  rig: RigState
  prev: TrailPoint
  current: TrailPoint
  trail: TrailPoint[]
  queue: LabCommand[]
  scenario: { readonly scenario: Scenario; readonly startTick: number } | null
  accumulator: number
}

interface Controls {
  readonly view: View
  readonly paused: boolean
  readonly speed: number
  readonly onion: boolean
  readonly showRig: boolean
  readonly editPose: KeyPoseName
}

interface Colors {
  readonly bird: string
  readonly background: string
  readonly ground: string
  readonly guide: string
  readonly label: string
}

interface LabApi {
  readonly pause: () => void
  readonly resume: () => void
  readonly step: (ticks: number) => void
  readonly flap: () => void
  readonly runScenario: (id: string) => void
  readonly state: () => { tick: number; flight: LabFlight; pose: BirdPose }
}

declare global {
  interface Window {
    /** Dev-only hook for agents and Playwright to drive the lab tick by tick. */
    traceFlightLab?: LabApi
  }
}

const MAX_CATCH_UP_TICKS = 5
const TRAIL_LENGTH = 32
const TRAIL_EVERY = 4
/** World units the course scrolls per tick, used to spread the onion skin. */
const SCROLL_PER_TICK = SCROLL_Q8 / Q
const BIRD_X_FRACTION = 0.45
/** In the pose editor the bird fills this share of the stage's shorter side. */
const EDITOR_FILL = 0.6

const CHANNEL_SLIDERS: Readonly<
  Record<
    JointChannel,
    {
      readonly label: string
      readonly min: number
      readonly max: number
      readonly step: number
    }
  >
> = {
  head: { label: "Head turn (°, + beak up)", min: -25, max: 25, step: 1 },
  headReach: { label: "Head reach (forward)", min: -10, max: 40, step: 1 },
  tail: { label: "Tail turn (°, + up)", min: -30, max: 30, step: 1 },
  beat: { label: "Wingbeat (°, + up)", min: -90, max: 95, step: 1 },
  spread: { label: "Wing spread (0 folded)", min: 0, max: 1.2, step: 0.05 },
}

const WINGBEAT_SLIDERS: readonly {
  readonly key: keyof WingbeatParams
  readonly label: string
  readonly min: number
  readonly max: number
  readonly step: number
}[] = [
  { key: "downTicks", label: "Stroke (ticks)", min: 2, max: 14, step: 1 },
  { key: "recoverTicks", label: "Recovery (ticks)", min: 2, max: 30, step: 1 },
  { key: "stroke", label: "Stroke depth", min: 0, max: 1.3, step: 0.05 },
  { key: "recover", label: "Recovery reach", min: -1.3, max: 0, step: 0.05 },
  {
    key: "fallLean",
    label: "Reach while falling",
    min: 0,
    max: 1.3,
    step: 0.05,
  },
]

const STILL: Omit<BirdPose, "joints"> = {
  offsetY: 0,
  angle: 0,
  scaleX: 1,
  scaleY: 1,
  alpha: 1,
  eye: 0,
  lids: "upper",
}

function freshSim(params: RigParams): Sim {
  const start = { pose: poseOf(INITIAL_RIG, params), y: START_Y }
  return {
    tick: 0,
    flight: READY,
    rig: INITIAL_RIG,
    prev: start,
    current: start,
    trail: [],
    queue: [],
    scenario: null,
    accumulator: 0,
  }
}

/** Imperative shell: one tick of lab flight + pose. */
function advance(sim: Sim, params: RigParams): void {
  const scripted =
    sim.scenario?.scenario.events
      .filter((e) => e.tick === sim.tick - sim.scenario!.startTick)
      .map((e) => e.command) ?? []
  const { flight, rig } = stepFlight(sim.flight, [...sim.queue, ...scripted])
  sim.queue = []
  sim.flight = flight
  sim.rig = stepRig(sim.rig, rig, params)
  sim.prev = sim.current
  sim.current = { pose: poseOf(sim.rig, params), y: flight.y }
  sim.trail = [sim.current, ...sim.trail].slice(0, TRAIL_LENGTH)
  sim.tick += 1
}

function readColors(theme: Theme): Colors {
  const style = getComputedStyle(document.documentElement)
  const token = (name: string) => style.getPropertyValue(name).trim()
  return {
    // The bird keeps its blue in both themes. Macaw's dark theme swaps the brand
    // icon's fill and background, so the blue is the background token there.
    bird: token(
      theme === "dark" ? "--icon-brand-background" : "--icon-brand-fill"
    ),
    background: token("--bg-surface-level-1"),
    ground: token("--border-muted"),
    guide: token("--border-subtle"),
    label: token("--text-tertiary"),
  }
}

function drawFlight(
  canvas: HTMLCanvasElement,
  sim: Sim,
  params: RigParams,
  controls: Controls,
  colors: Colors
): void {
  const sized = sizeCanvas(canvas)
  if (!sized) return
  const { ctx, width, height } = sized
  const scale = height / WORLD_HEIGHT
  const birdX = width * BIRD_X_FRACTION
  const size = BIRD_SIZE * scale

  ctx.fillStyle = colors.background
  ctx.fillRect(0, 0, width, height)
  ctx.fillStyle = colors.guide
  ctx.fillRect(0, FLOOR * scale, width, height - FLOOR * scale)
  ctx.strokeStyle = colors.ground
  ctx.setLineDash([6, 6])
  ctx.beginPath()
  ctx.moveTo(0, START_Y * scale)
  ctx.lineTo(width, START_Y * scale)
  ctx.stroke()
  ctx.setLineDash([])

  if (controls.onion) {
    sim.trail.forEach((point, i) => {
      if (i === 0 || i % TRAIL_EVERY !== 0) return
      ctx.save()
      ctx.globalAlpha = 0.28 * (1 - i / TRAIL_LENGTH)
      drawBird(ctx, point.pose, {
        x: birdX - i * SCROLL_PER_TICK * scale,
        y: point.y * scale,
        size,
        fill: colors.bird,
        wing: params.wing,
      })
      ctx.restore()
    })
  }

  const t = controls.paused ? 1 : sim.accumulator / TICK_SECONDS
  drawBird(ctx, lerpPose(sim.prev.pose, sim.current.pose, t), {
    x: birdX,
    y: lerp(sim.prev.y, sim.current.y, t) * scale,
    size,
    fill: colors.bird,
    wing: params.wing,
    rigColor: controls.showRig ? colors.label : undefined,
  })

  ctx.fillStyle = colors.label
  ctx.font = "12px ui-monospace, SFMono-Regular, Menlo, monospace"
  ctx.fillText(
    `tick ${sim.tick}  ${sim.flight.mode}  vy ${sim.flight.vy.toFixed(1)}`,
    12,
    20
  )
}

/** The selected key pose, large, over a faint copy of the logo for reference. */
function drawEditor(
  canvas: HTMLCanvasElement,
  params: RigParams,
  controls: Controls,
  colors: Colors
): void {
  const sized = sizeCanvas(canvas)
  if (!sized) return
  const { ctx, width, height } = sized
  const at = {
    x: width / 2,
    y: height / 2,
    size: Math.min(width, height) * EDITOR_FILL,
    fill: colors.bird,
    wing: params.wing,
  }
  ctx.fillStyle = colors.background
  ctx.fillRect(0, 0, width, height)

  ctx.save()
  ctx.globalAlpha = 0.18
  drawBird(ctx, { ...STILL, joints: PERCHED }, at)
  ctx.restore()
  drawBird(
    ctx,
    { ...STILL, joints: params.poses[controls.editPose] },
    { ...at, rigColor: controls.showRig ? colors.label : undefined }
  )

  ctx.fillStyle = colors.label
  ctx.font = "12px ui-monospace, SFMono-Regular, Menlo, monospace"
  ctx.fillText(`${controls.editPose} · the faint bird is the logo`, 12, 20)
}

function WingSliders(props: {
  readonly wing: WingShape
  readonly onChange: (wing: WingShape) => void
}) {
  const { wing, onChange } = props
  return (
    <>
      <ParamSlider
        label="Span"
        value={wing.span}
        min={0.5}
        max={1.6}
        step={0.05}
        onChange={(span) => onChange({ ...wing, span })}
      />
      <ParamSlider
        label="Width (chord)"
        value={wing.chord}
        min={0.5}
        max={1.6}
        step={0.05}
        onChange={(chord) => onChange({ ...wing, chord })}
      />
      <ParamSlider
        label="Rake back (°)"
        value={wing.sweep}
        min={-30}
        max={40}
        step={1}
        onChange={(sweep) => onChange({ ...wing, sweep })}
      />
      <ParamSlider
        label="Feathers"
        value={wing.feathers}
        min={0}
        max={2.5}
        step={0.05}
        onChange={(feathers) => onChange({ ...wing, feathers })}
      />
      <ParamSlider
        label="Shoulder x"
        value={wing.shoulder.x}
        min={480}
        max={680}
        step={1}
        onChange={(x) =>
          onChange({ ...wing, shoulder: { ...wing.shoulder, x } })
        }
      />
      <ParamSlider
        label="Shoulder y"
        value={wing.shoulder.y}
        min={300}
        max={460}
        step={1}
        onChange={(y) =>
          onChange({ ...wing, shoulder: { ...wing.shoulder, y } })
        }
      />
      <ParamSlider
        label="Cut-out line width"
        value={wing.gap}
        min={0}
        max={30}
        step={1}
        onChange={(gap) => onChange({ ...wing, gap })}
      />
      <ParamSlider
        label="Far wing offset x"
        value={wing.far.dx}
        min={-40}
        max={80}
        step={1}
        onChange={(dx) => onChange({ ...wing, far: { ...wing.far, dx } })}
      />
      <ParamSlider
        label="Far wing offset y"
        value={wing.far.dy}
        min={-80}
        max={40}
        step={1}
        onChange={(dy) => onChange({ ...wing, far: { ...wing.far, dy } })}
      />
      <ParamSlider
        label="Far wing size"
        value={wing.far.scale}
        min={0.6}
        max={1.1}
        step={0.01}
        onChange={(scale) => onChange({ ...wing, far: { ...wing.far, scale } })}
      />
    </>
  )
}

export function BirdLab() {
  const search = new URLSearchParams(window.location.search)
  const [params, setParams] = useState<RigParams>(DEFAULT_PARAMS)
  const [view, setView] = useState<View>(
    search.get("view") === "pose" ? "pose" : "fly"
  )
  const [paused, setPaused] = useState(search.has("manual"))
  const [speed, setSpeed] = useState(1)
  const [onion, setOnion] = useState(false)
  const [showRig, setShowRig] = useState(search.has("rig"))
  const [theme, setTheme] = useState<Theme>(
    search.get("theme") === "dark" ? "dark" : "light"
  )
  const [editPose, setEditPose] = useState<KeyPoseName>(
    KEY_POSE_NAMES.find((name) => name === search.get("pose")) ?? "reach"
  )
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const sim = useRef<Sim>(freshSim(DEFAULT_PARAMS))
  const paramsRef = useRef(params)
  const controlsRef = useRef<Controls>({
    view,
    paused,
    speed,
    onion,
    showRig,
    editPose,
  })
  const colorsRef = useRef<Colors | null>(null)

  useEffect(() => {
    paramsRef.current = params
    controlsRef.current = { view, paused, speed, onion, showRig, editPose }
  })

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark")
    colorsRef.current = readColors(theme)
  }, [theme])

  useEffect(() => {
    let frame = 0
    let last = performance.now()
    const render = (now: number) => {
      const elapsed = Math.min(0.1, (now - last) / 1000)
      last = now
      const s = sim.current
      const controls = controlsRef.current
      if (controls.view === "fly" && !controls.paused) {
        s.accumulator += elapsed * controls.speed
        let steps = 0
        while (s.accumulator >= TICK_SECONDS && steps < MAX_CATCH_UP_TICKS) {
          advance(s, paramsRef.current)
          s.accumulator -= TICK_SECONDS
          steps += 1
        }
        if (steps === MAX_CATCH_UP_TICKS) s.accumulator = 0
      }
      const canvas = canvasRef.current
      const colors = colorsRef.current
      if (canvas && colors) {
        if (controls.view === "pose")
          drawEditor(canvas, paramsRef.current, controls, colors)
        else drawFlight(canvas, s, paramsRef.current, controls, colors)
      }
      frame = requestAnimationFrame(render)
    }
    frame = requestAnimationFrame(render)
    return () => cancelAnimationFrame(frame)
  }, [])

  function runScenario(id: string) {
    const scenario = SCENARIOS.find((s) => s.id === id)
    if (!scenario) return
    const fresh = freshSim(paramsRef.current)
    sim.current = { ...fresh, scenario: { scenario, startTick: fresh.tick } }
  }

  function step(ticks: number) {
    for (let i = 0; i < ticks; i++) advance(sim.current, paramsRef.current)
  }

  function flap() {
    sim.current.queue.push("flap")
  }

  useEffect(() => {
    // Start fresh so Strict Mode's second run of this effect doesn't step twice.
    sim.current = freshSim(paramsRef.current)
    const scenario = search.get("scenario")
    if (scenario) runScenario(scenario)
    const ticks = Number(search.get("tick") ?? 0)
    if (ticks > 0) step(ticks)
    window.traceFlightLab = {
      pause: () => setPaused(true),
      resume: () => setPaused(false),
      step,
      flap,
      runScenario,
      state: () => ({
        tick: sim.current.tick,
        flight: sim.current.flight,
        pose: sim.current.current.pose,
      }),
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.repeat || !FLAP_KEYS.has(event.code) || isControl(event.target))
        return
      event.preventDefault()
      flap()
    }
    window.addEventListener("keydown", onKey)
    return () => {
      window.removeEventListener("keydown", onKey)
      delete window.traceFlightLab
    }
    // Runs once: the URL is read on load, and the handlers read live refs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function updatePose(name: KeyPoseName, patch: Partial<JointPose>) {
    setParams((p) => ({
      ...p,
      poses: { ...p.poses, [name]: { ...p.poses[name], ...patch } },
    }))
  }

  const pose = params.poses[editPose]
  const tuneSpring = (key: "sweep" | "dart") => (tuning: SpringTuning) =>
    setParams((p) => ({ ...p, [key]: tuning }))
  const setWing = (wing: WingShape) => setParams((p) => ({ ...p, wing }))

  return (
    <div className="grid h-svh grid-cols-[minmax(0,1fr)_22rem] bg-surface-level-1 text-primary">
      <canvas
        ref={canvasRef}
        role="application"
        aria-label={
          view === "fly"
            ? "Bird lab stage. Press Space, Up or W, or tap, to flap."
            : "Pose editor. Use the sliders to shape the selected key pose."
        }
        tabIndex={0}
        className="block size-full touch-none outline-none"
        onPointerDown={(event) => {
          if (view === "fly" && event.isPrimary) flap()
        }}
      />
      <aside className="flex min-h-0 flex-col gap-space-5 overflow-y-auto border-l border-muted bg-surface-level-2 p-space-4">
        <Section title="Mode">
          <ChoiceRow<View>
            options={["fly", "pose"]}
            value={view}
            onChange={setView}
          />
          <Button
            size="sm"
            variant="outlined"
            color="secondary"
            onClick={() => {
              window.location.search = "?page=game"
            }}
          >
            Open the game harness
          </Button>
          <Switch
            label="Dark theme"
            checked={theme === "dark"}
            onChange={(dark) => setTheme(dark ? "dark" : "light")}
          />
          <Switch label="Show joints" checked={showRig} onChange={setShowRig} />
        </Section>

        {view === "fly" ? (
          <>
            <Section title="Playback">
              <div className="flex flex-wrap gap-space-2">
                <Button size="sm" onClick={() => setPaused((p) => !p)}>
                  {paused ? "Resume" : "Pause"}
                </Button>
                <Button
                  size="sm"
                  variant="outlined"
                  color="secondary"
                  onClick={() => step(1)}
                >
                  Step 1 tick
                </Button>
                <Button
                  size="sm"
                  variant="outlined"
                  color="secondary"
                  onClick={() => {
                    sim.current.queue.push("land")
                  }}
                >
                  Land in logo
                </Button>
                <Button
                  size="sm"
                  variant="outlined"
                  color="secondary"
                  onClick={() => {
                    sim.current = freshSim(params)
                  }}
                >
                  Reset bird
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
              <Switch label="Onion skin" checked={onion} onChange={setOnion} />
            </Section>

            <Section title="Scenarios">
              <div className="flex flex-wrap gap-space-2">
                {SCENARIOS.map((s) => (
                  <Button
                    key={s.id}
                    size="sm"
                    variant="outlined"
                    color="secondary"
                    onClick={() => runScenario(s.id)}
                  >
                    {s.label}
                  </Button>
                ))}
              </div>
            </Section>

            <Section title="Wingbeat">
              {WINGBEAT_SLIDERS.map(({ key, ...slider }) => (
                <ParamSlider
                  key={key}
                  {...slider}
                  value={params.wingbeat[key]}
                  onChange={(value) =>
                    setParams((p) => ({
                      ...p,
                      wingbeat: { ...p.wingbeat, [key]: value },
                    }))
                  }
                />
              ))}
              <SpringSliders
                label="Wing sweep"
                tuning={params.sweep}
                onChange={tuneSpring("sweep")}
              />
            </Section>

            <Section title="Leaving the logo">
              {JOINT_NAMES.map((name) => (
                <ParamSlider
                  key={name}
                  label={`Unfold speed: ${name} (Hz)`}
                  value={params.morph[name].frequency}
                  min={0.5}
                  max={8}
                  step={0.1}
                  onChange={(frequency) =>
                    setParams((p) => ({
                      ...p,
                      morph: {
                        ...p.morph,
                        [name]: { ...p.morph[name], frequency },
                      },
                    }))
                  }
                />
              ))}
              <SpringSliders
                label="Dart"
                tuning={params.dart}
                onChange={tuneSpring("dart")}
              />
            </Section>

            <Section title="Joint springs">
              {JOINT_NAMES.map((name) => (
                <div key={name} className="flex flex-col gap-space-3">
                  <SpringSliders
                    label={name}
                    tuning={params.joints[name].spring}
                    onChange={(spring) =>
                      setParams((p) => ({
                        ...p,
                        joints: {
                          ...p.joints,
                          [name]: { ...p.joints[name], spring },
                        },
                      }))
                    }
                  />
                  {name !== "wings" && (
                    <ParamSlider
                      label={`${name} follow-through`}
                      value={params.joints[name].lag}
                      min={0}
                      max={0.3}
                      step={0.01}
                      onChange={(lag) =>
                        setParams((p) => ({
                          ...p,
                          joints: {
                            ...p.joints,
                            [name]: { ...p.joints[name], lag },
                          },
                        }))
                      }
                    />
                  )}
                </div>
              ))}
              <ParamSlider
                label="Keep head level"
                value={params.headLevel}
                min={0}
                max={1}
                step={0.05}
                onChange={(headLevel) =>
                  setParams((p) => ({ ...p, headLevel }))
                }
              />
            </Section>

            <Section title="Body">
              <SpringSliders
                label="Pitch"
                tuning={params.pitch}
                onChange={(t) =>
                  setParams((p) => ({ ...p, pitch: { ...p.pitch, ...t } }))
                }
              />
              <ParamSlider
                label="Pitch per fall speed (°)"
                value={params.pitch.degreesPerSpeed}
                min={0}
                max={5}
                step={0.1}
                onChange={(degreesPerSpeed) =>
                  setParams((p) => ({
                    ...p,
                    pitch: { ...p.pitch, degreesPerSpeed },
                  }))
                }
              />
              <ParamSlider
                label="Force push lean (°)"
                value={params.pitch.forcePushLean}
                min={0}
                max={45}
                step={1}
                onChange={(forcePushLean) =>
                  setParams((p) => ({
                    ...p,
                    pitch: { ...p.pitch, forcePushLean },
                  }))
                }
              />
              <ParamSlider
                label="Squash on flap"
                value={params.squash.flapKick}
                min={0}
                max={8}
                step={0.1}
                onChange={(flapKick) =>
                  setParams((p) => ({
                    ...p,
                    squash: { ...p.squash, flapKick },
                  }))
                }
              />
              <ParamSlider
                label="Hit ruffle (°/s)"
                value={params.hit.ruffleKick}
                min={0}
                max={1200}
                step={10}
                onChange={(ruffleKick) =>
                  setParams((p) => ({ ...p, hit: { ...p.hit, ruffleKick } }))
                }
              />
            </Section>
          </>
        ) : (
          <>
            <Section title="Key pose">
              <ChoiceRow<KeyPoseName>
                options={KEY_POSE_NAMES}
                value={editPose}
                onChange={setEditPose}
              />
              {JOINT_CHANNELS.map((channel) => (
                <ParamSlider
                  key={channel}
                  {...CHANNEL_SLIDERS[channel]}
                  value={pose[channel]}
                  onChange={(value) =>
                    updatePose(editPose, { [channel]: value })
                  }
                />
              ))}
              <Button
                size="sm"
                variant="outlined"
                color="secondary"
                onClick={() =>
                  setParams((p) => ({
                    ...p,
                    poses: { ...p.poses, [editPose]: DEFAULT_POSES[editPose] },
                  }))
                }
              >
                Reset this pose
              </Button>
            </Section>
            <Section title="Wing shape">
              <WingSliders wing={params.wing} onChange={setWing} />
              <Button
                size="sm"
                variant="outlined"
                color="secondary"
                onClick={() => setWing(DEFAULT_WING)}
              >
                Reset wing
              </Button>
            </Section>
          </>
        )}

        <Section title="Save">
          <div className="flex flex-wrap gap-space-2">
            <CopyButton
              copy={JSON.stringify(params, null, 2)}
              copyText="Copy params"
            />
            <Button
              size="sm"
              variant="outlined"
              color="secondary"
              onClick={() => setParams(DEFAULT_PARAMS)}
            >
              Reset params
            </Button>
          </div>
        </Section>
      </aside>
    </div>
  )
}
