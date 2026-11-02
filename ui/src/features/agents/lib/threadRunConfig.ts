import { create } from "zustand"

type RunConfig = Readonly<Record<string, boolean | string>>

/**
 * Run config keys a surface beside a thread adds to each message sent in it,
 * such as the app the agent builds there: its tools are offered, and what it
 * has open travels with the message, only while it is open to answer them.
 */
export const useThreadRunConfig = create<{
  byThread: Readonly<Record<string, RunConfig>>
  set: (threadId: string, config: RunConfig | null) => void
}>((set) => ({
  byThread: {},
  set: (threadId, config) =>
    set((state) => {
      const { [threadId]: _previous, ...rest } = state.byThread
      return { byThread: config ? { ...rest, [threadId]: config } : rest }
    }),
}))
