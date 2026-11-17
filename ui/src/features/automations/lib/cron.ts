export interface CronPreset {
  id: "hourly" | "daily" | "weekly"
  label: string
  value: string
}

/** What a new schedule runs on until its creator picks otherwise: Mondays at 09:00. */
export const DEFAULT_CRON = "0 9 * * 1"

export const CRON_PRESETS: Array<CronPreset> = [
  { id: "weekly", label: "Weekly", value: DEFAULT_CRON },
  { id: "daily", label: "Daily", value: "0 9 * * *" },
  { id: "hourly", label: "Hourly", value: "0 * * * *" },
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

function formatTime(minute: number, hour: number, timezone = "UTC"): string {
  return `${pad(hour)}:${pad(minute)} ${timezone}`
}

/** Best-effort human description of a 5-field cron expression. */
export function describeCron(expr: string, timezone?: string): string {
  const raw = timezone ? `${expr} (${timezone})` : expr
  const zone = timezone ? ` (${timezone})` : ""
  const parts = expr.trim().split(/\s+/)
  if (parts.length !== 5) return raw
  const [min, hour, dom, mon, dow] = parts
  if (!min || !hour || !dom || !mon || !dow) return raw

  if (
    min === "0" &&
    hour === "*" &&
    dom === "*" &&
    mon === "*" &&
    dow === "*"
  ) {
    return `Every hour${zone}`
  }

  const everyNHours = hour.match(/^\*\/(\d+)$/)
  if (everyNHours && min === "0" && dom === "*" && mon === "*" && dow === "*") {
    return `Every ${everyNHours[1]} hours${zone}`
  }

  const minNum = Number(min)
  const hourNum = Number(hour)
  const timeKnown =
    Number.isInteger(minNum) &&
    Number.isInteger(hourNum) &&
    minNum >= 0 &&
    hourNum >= 0

  if (timeKnown && dom === "*" && mon === "*") {
    if (dow === "*") return `Daily at ${formatTime(minNum, hourNum, timezone)}`
    if (dow === "1-5")
      return `Weekdays at ${formatTime(minNum, hourNum, timezone)}`
    const dowNum = Number(dow)
    if (Number.isInteger(dowNum) && dowNum >= 0 && dowNum <= 7) {
      const name = DAY_NAMES[dowNum % 7]
      return `Weekly on ${name} at ${formatTime(minNum, hourNum, timezone)}`
    }
  }

  return raw
}

/** True when describeCron renders a human-readable label (not the raw expression). */
export function isDescribableCron(expr: string): boolean {
  return describeCron(expr) !== expr
}
