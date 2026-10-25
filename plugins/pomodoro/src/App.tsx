import { Button } from "@langchain/macaw-components/Button"
import { Card } from "@langchain/macaw-components/Card"
import { Text } from "@langchain/macaw-components/Text"
import { useDashboardTheme } from "@openswe/plugin-sdk/react"
import { useEffect, useState } from "react"

const presets = [
  { label: "25 min", minutes: 25 },
  { label: "50 min", minutes: 50 },
  { label: "5 min", minutes: 5 },
] as const

const ringCircumference = 2 * Math.PI * 46

export function App() {
  useDashboardTheme()
  const [minutes, setMinutes] = useState(25)
  const [secondsLeft, setSecondsLeft] = useState(25 * 60)
  const [isRunning, setIsRunning] = useState(false)

  useEffect(() => {
    if (!isRunning) return
    const interval = window.setInterval(() => {
      setSecondsLeft((remaining) => {
        if (remaining <= 1) {
          window.clearInterval(interval)
          setIsRunning(false)
          return 0
        }
        return remaining - 1
      })
    }, 1000)
    return () => window.clearInterval(interval)
  }, [isRunning])

  const choosePreset = (duration: number) => {
    setIsRunning(false)
    setMinutes(duration)
    setSecondsLeft(duration * 60)
  }
  const displayTime = `${String(Math.floor(secondsLeft / 60)).padStart(2, "0")}:${String(secondsLeft % 60).padStart(2, "0")}`
  const progress = 1 - secondsLeft / (minutes * 60)

  return (
    <main className="flex min-h-full items-center justify-center bg-surface-level-1 p-space-6">
      <Card className="w-full max-w-xl p-space-8">
        <div className="flex flex-col items-center gap-space-6 text-center">
          <div className="flex flex-col gap-space-2">
            <Text as="h1" variant="h4" weight="semibold">
              Pomodoro
            </Text>
            <Text color="secondary">A little focus goes a long way.</Text>
          </div>

          <div className="flex items-center justify-center gap-space-2 rounded-full bg-surface-level-2 p-space-1">
            {presets.map((preset) => (
              <Button
                key={preset.label}
                variant={minutes === preset.minutes ? "normal" : "plain"}
                color={minutes === preset.minutes ? "primary" : "secondary"}
                onClick={() => choosePreset(preset.minutes)}
                aria-pressed={minutes === preset.minutes}
              >
                {preset.label}
              </Button>
            ))}
          </div>

          <div className="relative flex aspect-square w-full max-w-[min(72vw,22rem)] items-center justify-center">
            <svg
              className="absolute inset-0 h-full w-full -rotate-90"
              viewBox="0 0 100 100"
              aria-hidden="true"
            >
              <circle
                cx="50"
                cy="50"
                r="46"
                fill="none"
                stroke="var(--border-default)"
                strokeWidth="1.5"
              />
              <circle
                cx="50"
                cy="50"
                r="46"
                fill="none"
                stroke="var(--border-brand)"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeDasharray={ringCircumference}
                strokeDashoffset={ringCircumference * (1 - progress)}
                className="transition-[stroke-dashoffset] duration-500"
              />
            </svg>
            <Text
              as="div"
              variant="h1"
              weight="semibold"
              aria-live="off"
              aria-label={`${Math.floor(secondsLeft / 60)} minutes ${secondsLeft % 60} seconds remaining`}
              className="relative font-mono text-[clamp(5rem,18vw,9rem)] leading-none tracking-tight tabular-nums"
            >
              {displayTime}
            </Text>
          </div>

          <div className="flex items-center gap-space-3">
            <Button
              size="md"
              color="primary"
              onClick={() => setIsRunning((running) => !running)}
              disabled={secondsLeft === 0}
            >
              {isRunning ? "Pause" : "Start"}
            </Button>
            <Button
              size="md"
              variant="outlined"
              color="secondary"
              onClick={() => {
                setIsRunning(false)
                setSecondsLeft(minutes * 60)
              }}
            >
              Reset
            </Button>
          </div>
          <Text color="tertiary" variant="sm">
            {isRunning
              ? "Stay with the task in front of you."
              : secondsLeft === 0
                ? "Session complete. Well done."
                : "Choose a session and begin when you're ready."}
          </Text>
        </div>
      </Card>
    </main>
  )
}
