import type { LabCommand } from "./labFlight"

export interface ScenarioEvent {
  readonly tick: number
  readonly command: LabCommand
}

export interface Scenario {
  readonly id: string
  readonly label: string
  readonly events: readonly ScenarioEvent[]
}

const flapsAt = (...ticks: number[]): ScenarioEvent[] =>
  ticks.map((tick) => ({ tick, command: "flap" }))

/** Repeatable moments to review the bird against before the game is built around it. */
export const SCENARIOS: readonly Scenario[] = [
  {
    id: "launch",
    label: "Leap out of the logo",
    events: flapsAt(40, 76, 112, 148),
  },
  { id: "single", label: "Single flap", events: flapsAt(0, 60) },
  { id: "rapid", label: "Rapid flaps", events: flapsAt(0, 8, 16, 24, 32) },
  {
    id: "steady",
    label: "Steady flight",
    events: flapsAt(0, 36, 72, 108, 144, 180, 216),
  },
  {
    id: "hit",
    label: "Hit + recovery",
    events: [
      ...flapsAt(0, 36),
      { tick: 50, command: "hit" },
      ...flapsAt(80, 116, 152),
    ],
  },
  {
    id: "forcePush",
    label: "Force push",
    events: [...flapsAt(0, 36), { tick: 50, command: "forcePush" }],
  },
  {
    id: "autopilot",
    label: "Autopilot",
    events: [...flapsAt(0), { tick: 30, command: "autopilot" }],
  },
  {
    id: "land",
    label: "Land into the logo",
    events: [...flapsAt(0, 36, 72), { tick: 96, command: "land" }],
  },
]
