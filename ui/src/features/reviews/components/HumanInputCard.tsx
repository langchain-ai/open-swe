/** The review scout's summary of what people asked Open SWE for. */
export function HumanInputText({ summary }: { summary: string }) {
  return <p className="text-sm whitespace-pre-wrap text-primary">{summary}</p>
}
