import type { PluginHost } from "@openswe/plugin-sdk"
import { usePluginHost } from "@openswe/plugin-sdk/react"

/** What a person did in Design QA, by name only. */
export type UsageAction =
  | "tab_requested"
  | "tab_mine"
  | "tab_pinned"
  | "tab_people"
  | "person_picked"
  | "pull_opened"
  | "pin"
  | "unpin"
  | "capture_requested"
  | "github_opened"
  | "robin_view_opened"
  | "theme_light"
  | "theme_dark"
  | "compare_split"
  | "compare_swipe"
  | "compare_onion"
  | "ask_opened"
  | "sort_changed"
  | "page_size_changed"

/** Counts what people do in Design QA in Robin's product analytics; a web UI without it counts nothing. */
export class Usage {
  static record(host: PluginHost, action: UsageAction): void {
    host.analytics
      .track(action)
      .catch((error: unknown) =>
        console.debug("Design QA usage was not counted", error)
      )
  }
}

/** Counts one action of the person's, through the web UI. */
export function useUsage() {
  const host = usePluginHost()
  return (action: UsageAction) => Usage.record(host, action)
}
