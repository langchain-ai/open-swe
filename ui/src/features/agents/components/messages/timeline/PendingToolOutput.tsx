import { useEffect, useState } from "react"

import { useTranscriptThreadId } from "../transcriptThread"
import { useMessageContentStore } from "@/features/agents/lib/messageContentStore"

/**
 * Shown inside an expanded row whose output the trimmed state view blanked.
 * Mounting it fetches the message; once cached, the transcript rebuilds with
 * the real output and this body is no longer rendered.
 */
export function PendingToolOutput({ messageId }: { messageId: string }) {
  const threadId = useTranscriptThreadId()
  const ensure = useMessageContentStore((state) => state.ensure)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (!threadId) return
    let active = true
    void ensure(threadId, messageId).then((loaded) => {
      if (active && !loaded) setFailed(true)
    })
    return () => {
      active = false
    }
  }, [ensure, messageId, threadId])

  return (
    <p className="font-mono text-[12px] text-muted-foreground">
      {failed || !threadId ? "Output unavailable" : "Loading output…"}
    </p>
  )
}
