import { usePageCommandRunner } from "@/features/agents/lib/pageToolBridge"

/** Carries out the commands Robin queues for this tab's page, wherever the person is in the app. */
export function PageCommandRunner() {
  usePageCommandRunner()
  return null
}
