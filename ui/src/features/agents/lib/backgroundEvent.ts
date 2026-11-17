import type { Message } from "./types"
import { formatElapsed } from "@/lib/utils"

export interface BackgroundEventView {
  heading: string
  detail?: string
  failed: boolean
}

export class BackgroundEvent {
  static fromMessage(message: Message): BackgroundEventView | null {
    if (message.structuredSenderKind !== "system" || message.taskEvent)
      return null
    const text = message.chunks
      .filter((chunk) => chunk.kind === "text")
      .map((chunk) => chunk.text)
      .join("")
    const sender = message.structuredSenderId
    if (
      sender === "system:background-task" &&
      text.startsWith("A sandbox background command finished.")
    ) {
      const status =
        /^Status: (completed|failed|timed_out|stopped|lost)$/m.exec(text)?.[1]
      const exitCode = /^Exit code: (-?\d+)$/m.exec(text)?.[1]
      const duration = /^Duration: (\d+(?:\.\d+)?)s$/m.exec(text)?.[1]
      const task = /^Task: (cmd-[\da-f-]+)$/im.exec(text)?.[1]
      const failed =
        status === "failed" ||
        status === "timed_out" ||
        status === "lost" ||
        (status === "completed" && exitCode !== undefined && exitCode !== "0")
      const label =
        status === "timed_out"
          ? "timed out"
          : status === "stopped"
            ? "stopped"
            : failed
              ? "failed"
              : status === "completed"
                ? "completed"
                : "finished"
      const elapsed = duration === undefined ? undefined : Number(duration)
      const detail = [
        task && `Task: ${task}`,
        exitCode !== undefined && `Exit code: ${exitCode}`,
      ]
        .filter(Boolean)
        .join("\n")
      return {
        heading: `Background task ${label}${elapsed !== undefined && Number.isFinite(elapsed) ? ` · ${formatElapsed(Math.round(elapsed) * 1000)}` : ""}`,
        ...(detail ? { detail } : {}),
        failed,
      }
    }
    if (
      sender === "system:monitor" &&
      /^Monitor `[^`]+` \([^\n]*\) (?:emitted|ended:)/.test(text)
    ) {
      const ended = /^Monitor `[^`]+` \([^\n]*\) ended: ([^\n]*)/.exec(
        text
      )?.[1]
      const timedOut = ended?.startsWith("it reached its ") ?? false
      const failed =
        timedOut ||
        Boolean(
          ended &&
          /^(the command failed|its filter exited|its runner disappeared|it was too noisy)/.test(
            ended
          )
        )
      return {
        heading: timedOut
          ? "Monitor timed out"
          : failed
            ? "Monitor ended · Failed"
            : ended
              ? "Monitor ended"
              : "Monitor update",
        failed,
      }
    }
    if (sender === "system:event-subscription") {
      if (
        /^The bounded wait for subscription `[^`]+` on [^\n]+ has reached its deadline\./.test(
          text
        )
      )
        return { heading: "Event subscription timed out", failed: false }
      if (/^A [\w-]+ `[^`\n]+` event arrived\./.test(text))
        return { heading: "Subscribed event received", failed: false }
    }
    if (
      sender === "system:thread-wakeup" &&
      text.startsWith("This is an automated re-trigger of this thread.")
    )
      return { heading: "Scheduled check", failed: false }
    if (
      sender === "system:background-event" &&
      text.startsWith(
        "This run was started by a Robin background event, not by a message."
      )
    )
      return { heading: "Background event received", failed: false }
    return null
  }
}
