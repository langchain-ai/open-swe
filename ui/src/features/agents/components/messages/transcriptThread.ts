import { createContext, useContext } from "react"

/** The thread whose transcript is being rendered, for rows that fetch on demand. */
export const TranscriptThreadContext = createContext<string | undefined>(
  undefined
)

export function useTranscriptThreadId(): string | undefined {
  return useContext(TranscriptThreadContext)
}
