import { describe, expect, it } from "vitest"

import { ScheduleSpec, describeCron, sameTimeZone, timeZoneLabel } from "./cron"

describe("ScheduleSpec", () => {
  it.each([
    ["15 * * * *", "hourly"],
    ["0 9 * * *", "daily"],
    ["0 9 * * 1-5", "weekdays"],
    ["0 9 * * 1", "weekly"],
    ["30 2 31 * *", "monthly"],
  ])("rebuilds %s exactly as a %s schedule", (cron, frequency) => {
    const spec = ScheduleSpec.parse(cron)
    expect(spec?.frequency).toBe(frequency)
    expect(spec?.cron).toBe(cron)
  })

  it("reads equivalent spellings without losing their meaning", () => {
    expect(ScheduleSpec.parse("00 09 * * *")?.cron).toBe("0 9 * * *")
    // Day 7 is Sunday, as 0 is.
    expect(ScheduleSpec.parse("0 9 * * 7")?.weekday).toBe(0)
  })

  it.each([
    "*/5 * * * *",
    "0 9 * * 1,3",
    "0 9 1 * 1",
    "0 9 * 6 *",
    "0 */2 * * *",
    "0 24 * * *",
    "not a cron",
  ])("leaves %s to the custom cron field", (cron) => {
    expect(ScheduleSpec.parse(cron)).toBeNull()
  })
})

describe("time zones", () => {
  it("treats aliases of one zone as the same zone", () => {
    expect(sameTimeZone("Asia/Calcutta", "Asia/Kolkata")).toBe(true)
    expect(sameTimeZone("Etc/UTC", "UTC")).toBe(true)
    expect(sameTimeZone("UTC", "America/New_York")).toBe(false)
  })

  it("names a zone only when it isn't the viewer's", () => {
    const local = Intl.DateTimeFormat().resolvedOptions().timeZone
    const other = sameTimeZone(local, "Asia/Kolkata")
      ? "America/New_York"
      : "Asia/Kolkata"
    expect(describeCron("0 9 * * *", local)).toBe(describeCron("0 9 * * *"))
    // An hourly schedule's minute shifts in a half-hour zone, so it is named too.
    expect(describeCron("0 * * * *", other)).toBe(
      `Every hour ${timeZoneLabel(other)}`
    )
  })
})
