import { Markdown } from "@/features/agents/components/chat/Markdown"

/** The review scout's summary of what people asked Open SWE for. */
export function HumanInputText({ summary }: { summary: string }) {
  return (
    <div className="text-sm text-primary">
      <Markdown content={summary} />
    </div>
  )
}
