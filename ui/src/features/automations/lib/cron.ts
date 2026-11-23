export type ScheduleFrequency =
  | "hourly"
  | "daily"
  | "weekdays"
  | "weekly"
  | "monthly"

export const FREQUENCY_LABELS: Record<ScheduleFrequency, string> = {
  hourly: "Hourly",
  daily: "Daily",
  weekdays: "Weekdays",
  weekly: "Weekly",
  monthly: "Monthly",
}

export const DAY_NAMES = [
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
]

const ORDINAL_RULES = new Intl.PluralRules("en-US", { type: "ordinal" })
const ORDINAL_SUFFIXES: Record<string, string> = {
  one: "st",
  two: "nd",
  few: "rd",
  other: "th",
}

/** A schedule the editor can show as plain choices, or `parse` returns null for raw cron. */
export class ScheduleSpec {
  constructor(
    readonly frequency: ScheduleFrequency,
    readonly minute: number,
    readonly hour: number,
    /** 0 (Sunday) to 6, for weekly schedules. */
    readonly weekday: number,
    /** 1 to 31, for monthly schedules. */
    readonly monthday: number
  ) {}

  static parse(expr: string): ScheduleSpec | null {
    const parts = expr.trim().split(/\s+/)
    if (parts.length !== 5) return null
    const [min, hour, dom, mon, dow] = parts as [
      string,
      string,
      string,
      string,
      string,
    ]
    const minute = ScheduleSpec.number(min, 0, 59)
    if (minute === null || mon !== "*") return null
    if (hour === "*" && dom === "*" && dow === "*") {
      return new ScheduleSpec("hourly", minute, 9, 1, 1)
    }
    const hourNum = ScheduleSpec.number(hour, 0, 23)
    if (hourNum === null) return null
    if (dom === "*") {
      if (dow === "*") return new ScheduleSpec("daily", minute, hourNum, 1, 1)
      if (dow === "1-5") {
        return new ScheduleSpec("weekdays", minute, hourNum, 1, 1)
      }
      const day = ScheduleSpec.number(dow, 0, 7)
      return day === null
        ? null
        : new ScheduleSpec("weekly", minute, hourNum, day % 7, 1)
    }
    const monthday = ScheduleSpec.number(dom, 1, 31)
    return monthday !== null && dow === "*"
      ? new ScheduleSpec("monthly", minute, hourNum, 1, monthday)
      : null
  }

  private static number(field: string, min: number, max: number) {
    if (!/^\d+$/.test(field)) return null
    const value = Number(field)
    return value >= min && value <= max ? value : null
  }

  with(change: Partial<ScheduleSpecFields>): ScheduleSpec {
    return new ScheduleSpec(
      change.frequency ?? this.frequency,
      change.minute ?? this.minute,
      change.hour ?? this.hour,
      change.weekday ?? this.weekday,
      change.monthday ?? this.monthday
    )
  }

  get cron(): string {
    const { minute: m, hour: h } = this
    switch (this.frequency) {
      case "hourly":
        return `${m} * * * *`
      case "daily":
        return `${m} ${h} * * *`
      case "weekdays":
        return `${m} ${h} * * 1-5`
      case "weekly":
        return `${m} ${h} * * ${this.weekday}`
      case "monthly":
        return `${m} ${h} ${this.monthday} * *`
    }
  }

  /** Plain words, such as "Weekdays at 9:00 AM", naming the zone only when it isn't the viewer's. */
  describe(timezone?: string): string {
    const zone = zoneSuffix(timezone)
    const at = `at ${formatClock(this.hour, this.minute)}${zone}`
    switch (this.frequency) {
      case "hourly":
        // A half-hour zone shifts the minute, so a foreign zone is named here too.
        return this.minute === 0
          ? `Every hour${zone}`
          : `Every hour at :${pad(this.minute)}${zone}`
      case "daily":
        return `Daily ${at}`
      case "weekdays":
        return `Weekdays ${at}`
      case "weekly":
        return `Every ${DAY_NAMES[this.weekday]} ${at}`
      case "monthly":
        return `Monthly on the ${ordinal(this.monthday)} ${at}`
    }
  }
}

type ScheduleSpecFields = Pick<
  ScheduleSpec,
  "frequency" | "minute" | "hour" | "weekday" | "monthday"
>

/** What a new schedule runs on until its creator picks otherwise: weekdays at 9:00. */
export const DEFAULT_SCHEDULE = new ScheduleSpec("weekdays", 0, 9, 1, 1)

function pad(n: number): string {
  return n.toString().padStart(2, "0")
}

/** "1st", "22nd", "31st". */
export function ordinal(n: number): string {
  return `${n}${ORDINAL_SUFFIXES[ORDINAL_RULES.select(n)] ?? "th"}`
}

/** A wall-clock time in the viewer's locale, such as "9:00 AM". */
export function formatClock(hour: number, minute: number): string {
  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
    timeZone: "UTC",
  }).format(Date.UTC(2000, 0, 1, hour, minute))
}

/** The viewer's own IANA time zone; UTC where the browser can't say. */
export function localTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
}

/** Whether two IANA names are one zone, so aliases such as Asia/Calcutta and Asia/Kolkata match. */
export function sameTimeZone(a: string, b: string): boolean {
  return canonicalTimeZone(a) === canonicalTimeZone(b)
}

function canonicalTimeZone(timezone: string): string {
  try {
    return new Intl.DateTimeFormat("en-US", { timeZone: timezone })
      .resolvedOptions()
      .timeZone.replace(/^Etc\/(UTC|GMT)$/, "UTC")
  } catch {
    return timezone
  }
}

/** " PST" for a zone other than the viewer's, else nothing: the viewer's own goes without saying. */
function zoneSuffix(timezone?: string): string {
  return timezone && !sameTimeZone(timezone, localTimeZone())
    ? ` ${timeZoneLabel(timezone)}`
    : ""
}

/** A zone's short name, such as "PST", or the IANA name when there is none. */
export function timeZoneLabel(timezone: string): string {
  if (timezone === "UTC") return "UTC"
  try {
    return (
      new Intl.DateTimeFormat("en-US", {
        timeZone: timezone,
        timeZoneName: "short",
      })
        .formatToParts(new Date())
        .find((part) => part.type === "timeZoneName")?.value ?? timezone
    )
  } catch {
    return timezone
  }
}

/** Best-effort human description of a 5-field cron expression. */
export function describeCron(expr: string, timezone?: string): string {
  const spec = ScheduleSpec.parse(expr)
  if (spec) return spec.describe(timezone)
  const everyNHours = expr.trim().match(/^0 \*\/(\d+) \* \* \*$/)
  if (everyNHours) return `Every ${everyNHours[1]} hours${zoneSuffix(timezone)}`
  return `${expr}${zoneSuffix(timezone)}`
}
