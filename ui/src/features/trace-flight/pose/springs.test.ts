import { describe, expect, it } from "vitest"

import { AT_REST, kick, stepSpring } from "./springs"

describe("stepSpring", () => {
  it("overshoots its target when underdamped, then settles on it", () => {
    let s = AT_REST
    let peak = 0
    for (let i = 0; i < 300; i++) {
      s = stepSpring(s, 10, { frequency: 4, damping: 0.4 })
      peak = Math.max(peak, s.value)
    }
    expect(peak).toBeGreaterThan(10)
    expect(s.value).toBeCloseTo(10, 3)
    expect(s.velocity).toBeCloseTo(0, 3)
  })

  // The lab's sliders span 0.5–12 Hz and damping 0.05–1.5; every pair must stay
  // finite and never grow past the kick's own swing.
  it("stays stable across every lab setting", () => {
    for (const frequency of [0.5, 4, 8, 10, 12])
      for (const damping of [0.05, 0.5, 1, 1.5]) {
        let s = kick(AT_REST, 1000)
        for (let i = 0; i < 600; i++)
          s = stepSpring(s, 0, { frequency, damping })
        expect(Number.isFinite(s.value)).toBe(true)
        expect(Math.abs(s.value)).toBeLessThan(1000)
      }
  })
})
