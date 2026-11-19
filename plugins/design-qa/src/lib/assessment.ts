import type { DesignSystemReview, RiskAssessment } from "./capture"

/** How a result reads, in Macaw's status colors. */
export type Tone = "success" | "warning" | "error"

export interface Verdict {
  tone: Tone
  label: string
}

/** How a capture's AI assessment reads: its risk and its design system check. */
export class Assessment {
  /** A risk score as it reads: 1–2 low, 3 medium, 4–5 high. */
  static risk(risk: RiskAssessment): Verdict {
    const tone =
      risk.score <= 2 ? "success" : risk.score === 3 ? "warning" : "error"
    const level = { success: "Low", warning: "Medium", error: "High" }[tone]
    return { tone, label: `Risk ${risk.score}/5 · ${level}` }
  }

  /** The design system check as it reads: any error fails it, any warning flags it. */
  static designSystem(
    review: DesignSystemReview
  ): Verdict & { errors: number; warnings: number } {
    const errors = review.findings.filter(
      ({ severity }) => severity === "error"
    ).length
    const warnings = review.findings.length - errors
    const plural = (count: number, word: string) =>
      `${count} design system ${word}${count === 1 ? "" : "s"}`
    if (errors)
      return {
        tone: "error",
        label: plural(errors, "violation"),
        errors,
        warnings,
      }
    if (warnings)
      return {
        tone: "warning",
        label: plural(warnings, "warning"),
        errors,
        warnings,
      }
    return {
      tone: "success",
      label: "Follows the design system",
      errors,
      warnings,
    }
  }
}
