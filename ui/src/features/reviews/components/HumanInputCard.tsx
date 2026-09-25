import { cn } from "@/lib/utils"

export function HumanInputText({ summary }: { summary: string }) {
  return (
    <p className="text-sm whitespace-pre-wrap text-foreground">{summary}</p>
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
      className={cn("rounded-lg border border-border bg-card p-4", className)}
    >
      <h3 className="mb-2.5 text-xs font-medium text-foreground">
        Human input
      </h3>
      <HumanInputText summary={summary} />
    </section>
  )
}
