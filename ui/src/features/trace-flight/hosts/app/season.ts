import { api } from "@/lib/api"

/** A season that hasn't answered by now is played unsaved rather than waited on. */
const SEASON_TIMEOUT_MS = 5000

/** The open season; fetched when the logo is clicked twice, so it's ready by the fourth. */
export const seasonQuery = {
  queryKey: ["trace-flight", "season"],
  queryFn: ({ signal }: { signal: AbortSignal }) =>
    api.traceFlightSeason(
      AbortSignal.any([signal, AbortSignal.timeout(SEASON_TIMEOUT_MS)])
    ),
  staleTime: 60_000,
  retry: false,
  // Offline, a paused query would leave the game blank; failing lets it play unsaved.
  networkMode: "always",
} as const
