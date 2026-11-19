import type { DesignSystemReview, RiskAssessment } from "./capture"

/** How a result reads, in Macaw's status colors. */
export type Tone = "success" | "warning" | "error"

export interface Verdict {
  tone: Tone
  label: string
}

export type SummaryBlock =
  | { kind: "points"; items: string[] }
  | { kind: "text"; text: string }

/** How a capture's AI assessment reads: its risk, its design system check and its recap. */
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

  /** What the design system check read, or that the repository had nothing to read. */
  static sources(review: DesignSystemReview): string {
    return review.sources.length
      ? `Checked against ${review.sources.join(", ")}.`
      : "The repository has no design system documents, so only consistency with the surrounding UI was checked."
  }

  /** A recap's lines, with each run of `- `, `* ` or `• ` lines as one list. */
  static summary(summary: string): SummaryBlock[] {
    const blocks: SummaryBlock[] = []
    for (const raw of summary.split("\n")) {
      const line = raw.trim()
      if (!line) continue
      const point = /^[-*•]\s+(.*)$/.exec(line)?.[1]
      const last = blocks.at(-1)
      if (point === undefined) blocks.push({ kind: "text", text: line })
      else if (last?.kind === "points") last.items.push(point)
      else blocks.push({ kind: "points", items: [point] })
    }
    return blocks
  }
}
