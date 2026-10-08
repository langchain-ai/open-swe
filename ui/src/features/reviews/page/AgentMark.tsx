import { cn } from "@/lib/utils"

/** Open SWE's mark, used wherever the agent (not a person) is speaking. */
export function AgentMark({ className }: { className?: string }) {
  return (
    <img
      src={`${import.meta.env.BASE_URL}logo-mark.png`}
      alt=""
      aria-hidden
      draggable={false}
      className={cn("size-3.5 shrink-0 rounded-[3px]", className)}
    />
  )
}
