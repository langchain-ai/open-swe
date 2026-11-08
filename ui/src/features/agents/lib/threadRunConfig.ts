import { create } from "zustand"

/** What a surface around a thread adds to each message sent in it. */
export interface ThreadRun {
  /** Run config keys, which the backend reads by name. */
  configurable: Readonly<Record<string, boolean | string>>
  /** Runs just before each message is sent, such as offering an app's tools again. */
  prepare?: () => Promise<void>
}

/**
 * The `ThreadRun` of the surface beside each thread, such as the app the agent
 * builds there: its tools are offered, and what it has open travels with the
 * message, only while it is open to answer them.
 */
export const useThreadRunConfig = create<{
  byThread: Readonly<Record<string, ThreadRun>>
  set: (threadId: string, run: ThreadRun | null) => void
}>((set) => ({
  byThread: {},
  set: (threadId, run) =>
    set((state) => {
      const { [threadId]: _previous, ...rest } = state.byThread
      return { byThread: run ? { ...rest, [threadId]: run } : rest }
    }),
}))
