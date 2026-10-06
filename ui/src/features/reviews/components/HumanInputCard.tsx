import { cn } from "@/lib/utils"

export function HumanInputText({ summary }: { summary: string }) {
  return (
    <p className="text-body whitespace-pre-wrap text-ink">{summary}</p>
  )
}

/**
 * The review scout's summary of what people asked Open SWE for, from the
 * opening request through every follow-up. Renders nothing until one exists.
 */
export function HumanInputCard({
  summary,
  className,
}: {
  summary: string
  className?: string
}) {
  if (!summary) return null
  return (
    <section
      aria-label="Human input"
      className={cn("rounded-compact border border-line bg-panel p-4", className)}
    >
      <h3 className="mb-2.5 text-label font-medium text-ink">
        Human input
      </h3>
      <HumanInputText summary={summary} />
    </section>
  )
}
