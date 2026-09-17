/**
 * Thread hydration switches, fixed in code so a dev build behaves the same on
 * every load. Flip the constants and let HMR reload; nothing is read from the
 * URL or `localStorage`.
 */

export type StateView = "full" | "transcript"

export interface HydrationFlags {
  /** Which `GET …/state` projection seeds the transcript. */
  view: StateView
  /**
   * Whether the SDK fetches its discovery `getHistory` page after hydrate.
   * Each checkpoint in that page is a full copy of the thread state.
   */
  discoverHistory: boolean
}

export const HYDRATION_FLAGS: HydrationFlags = {
  view: "transcript",
  discoverHistory: false,
}

export function readHydrationFlags(): HydrationFlags {
  return HYDRATION_FLAGS
}

export function stateQuery(flags: HydrationFlags): string {
  return `?${new URLSearchParams({ view: flags.view }).toString()}`
}
