import { PageSection } from "@langchain/gtm-platform-design-system/patterns/page-frame"
import { Box } from "@langchain/gtm-platform-design-system/ui/box"

export function HumanInputText({ summary }: { summary: string }) {
  return <p className="text-body whitespace-pre-wrap text-ink">{summary}</p>
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
    <Box className={className}>
      <PageSection title="Human input" contained inset="padded">
        <HumanInputText summary={summary} />
      </PageSection>
    </Box>
  )
}
