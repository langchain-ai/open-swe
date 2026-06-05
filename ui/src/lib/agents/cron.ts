export interface CronPreset {
  id: "hourly" | "daily" | "weekly"
  label: string
  value: string
}

export const CRON_PRESETS: Array<CronPreset> = [
  { id: "hourly", label: "Hourly", value: "0 * * * *" },
  { id: "daily", label: "Daily", value: "0 9 * * *" },
  { id: "weekly", label: "Weekly", value: "0 9 * * 1" },
]

const DAY_NAMES = [
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
]

function pad(n: number): string {
  return n.toString().padStart(2, "0")
}

function formatTime(minute: number, hour: number): string {
  return `${pad(hour)}:${pad(minute)} UTC`
}

/** Best-effort human description of a 5-field cron expression. */
export function describeCron(expr: string): string {
  const parts = expr.trim().split(/\s+/)
  if (parts.length !== 5) return expr
  const [min, hour, dom, mon, dow] = parts
  if (!min || !hour || !dom || !mon || !dow) return expr

  if (min === "0" && hour === "*" && dom === "*" && mon === "*" && dow === "*") {
    return "Every hour"
  }

  const everyNHours = hour.match(/^\*\/(\d+)$/)
  if (everyNHours && min === "0" && dom === "*" && mon === "*" && dow === "*") {
    return `Every ${everyNHours[1]} hours`
  }

  const minNum = Number(min)
  const hourNum = Number(hour)
  const timeKnown =
    Number.isInteger(minNum) &&
    Number.isInteger(hourNum) &&
    minNum >= 0 &&
    hourNum >= 0

  if (timeKnown && dom === "*" && mon === "*") {
    if (dow === "*") return `Daily at ${formatTime(minNum, hourNum)}`
    if (dow === "1-5") return `Weekdays at ${formatTime(minNum, hourNum)}`
    const dowNum = Number(dow)
    if (Number.isInteger(dowNum) && dowNum >= 0 && dowNum <= 7) {
      const name = DAY_NAMES[dowNum % 7]
      return `Weekly on ${name} at ${formatTime(minNum, hourNum)}`
    }
  }

  return expr
}

/** Match a cron expression back to a known preset id, if any. */
export function presetForCron(expr: string): CronPreset["id"] | "custom" {
  const normalized = expr.trim().split(/\s+/).join(" ")
  const match = CRON_PRESETS.find((p) => p.value === normalized)
  return match?.id ?? "custom"
}
