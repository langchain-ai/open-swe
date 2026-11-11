/** Sidebar controls and keyboard helpers shared by the lab pages. */
import { Button } from "@langchain/macaw-components/Button"
import { Slider } from "@langchain/macaw-components/Slider"

import type { SpringTuning } from "../../pose/springs"

export function ParamSlider(props: {
  readonly label: string
  readonly value: number
  readonly min: number
  readonly max: number
  readonly step: number
  readonly onChange: (value: number) => void
}) {
  return (
    <div className="flex flex-col gap-space-1">
      <div className="flex justify-between text-xs text-secondary">
        <span>{props.label}</span>
        <span className="tabular-nums">
          {Number.isInteger(props.step) ? props.value : props.value.toFixed(2)}
        </span>
      </div>
      <Slider
        aria-label={props.label}
        value={props.value}
        min={props.min}
        max={props.max}
        step={props.step}
        onChange={(v) =>
          props.onChange(Array.isArray(v) ? (v[0] ?? props.value) : v)
        }
      />
    </div>
  )
}

export function SpringSliders(props: {
  readonly label: string
  readonly tuning: SpringTuning
  readonly onChange: (tuning: SpringTuning) => void
}) {
  return (
    <>
      <ParamSlider
        label={`${props.label} speed (Hz)`}
        value={props.tuning.frequency}
        min={0.5}
        max={12}
        step={0.1}
        onChange={(frequency) => props.onChange({ ...props.tuning, frequency })}
      />
      <ParamSlider
        label={`${props.label} damping`}
        value={props.tuning.damping}
        min={0.05}
        max={1.5}
        step={0.05}
        onChange={(damping) => props.onChange({ ...props.tuning, damping })}
      />
    </>
  )
}

export function Section(props: {
  readonly title: string
  readonly children: React.ReactNode
}) {
  return (
    <section className="flex flex-col gap-space-3">
      <h2 className="text-sm font-medium text-primary">{props.title}</h2>
      {props.children}
    </section>
  )
}

export function ChoiceRow<T extends string>(props: {
  readonly options: readonly T[]
  readonly value: T
  readonly onChange: (value: T) => void
}) {
  return (
    <div className="flex flex-wrap gap-space-2">
      {props.options.map((option) => (
        <Button
          key={option}
          size="sm"
          variant={option === props.value ? "normal" : "outlined"}
          color={option === props.value ? "primary" : "secondary"}
          onClick={() => props.onChange(option)}
        >
          {option}
        </Button>
      ))}
    </div>
  )
}
