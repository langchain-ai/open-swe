import { dashboardApiUrl } from "./dashboard-fetch"

export class SlackLinks {
  static open(channel: string, messageTs?: string, threadTs?: string): string {
    const params = new URLSearchParams({ channel })
    if (messageTs) params.set("message_ts", messageTs)
    if (threadTs) params.set("thread_ts", threadTs)
    return dashboardApiUrl(`/slack/open?${params}`)
  }

  static normalize(href: string): string {
    if (!URL.canParse(href)) return href
    const url = new URL(href)
    if (url.username || url.password || url.port) return href
    if (url.protocol === "slack:" && url.hostname === "channel") {
      const channel = url.searchParams.get("id")
      return channel && !url.searchParams.get("team")
        ? SlackLinks.open(channel, url.searchParams.get("message") ?? undefined)
        : href
    }
    if (
      url.protocol !== "https:" ||
      !["slack.com", "app.slack.com"].includes(url.hostname)
    )
      return href
    const message = /^\/archives\/([A-Z0-9]+)\/p(\d+)$/.exec(url.pathname)
    if (message?.[1] && message[2] && message[2].length > 6) {
      const timestamp = message[2]
      return SlackLinks.open(
        message[1],
        `${timestamp.slice(0, -6)}.${timestamp.slice(-6)}`,
        url.searchParams.get("thread_ts") ?? undefined
      )
    }
    const channel = /^\/archives\/([A-Z0-9]+)$/.exec(url.pathname)?.[1]
    if (channel) return SlackLinks.open(channel)
    if (url.pathname === "/app_redirect" && !url.searchParams.get("team")) {
      const target = url.searchParams.get("channel")
      if (target) return SlackLinks.open(target)
    }
    return href
  }
}
