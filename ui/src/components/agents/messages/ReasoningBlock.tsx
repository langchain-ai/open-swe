import {
  Reasoning,
  ReasoningContent,
  ReasoningTrigger,
} from "@/components/ai-elements/reasoning";

/**
 * Renders a model's reasoning ("thinking") tokens via the AI Elements Reasoning
 * component: a shimmering "Thinking…" header while the reasoning streams, which
 * collapses into a "Thought for …" toggle once it ends. Open/close + duration
 * are managed by Reasoning from the `isStreaming` signal.
 */
export function ReasoningBlock({ text, isLive }: { text: string; isLive: boolean }) {
  const trimmed = text.trim();
  if (!trimmed && !isLive) return null;

  return (
    <Reasoning className="mb-0" isStreaming={isLive}>
      <ReasoningTrigger />
      <ReasoningContent>{trimmed}</ReasoningContent>
    </Reasoning>
  );
}
