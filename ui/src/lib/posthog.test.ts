/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest"
import type { CaptureResult, PostHog, PostHogConfig } from "posthog-js"

import type { SessionUser } from "./api"
import { productAnalytics } from "./posthog"

function postHogClient() {
  const client = {
    init: vi.fn<(key: string, config: Partial<PostHogConfig>) => void>(),
    identify: vi.fn(),
    reset: vi.fn(),
  }
  return { client, load: async () => client as unknown as PostHog }
}

describe("productAnalytics", () => {
  afterEach(() => {
    productAnalytics.reset()
    vi.unstubAllGlobals()
  })

  it("stays off when the deployment has no PostHog project", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({ environment: "staging", posthog: null })
      )
    )
    const { client, load } = postHogClient()

    await productAnalytics.initialize(load)

    expect(client.init).not.toHaveBeenCalled()
  })

  it("identifies by user id and strips ids from event URLs", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({
          environment: "staging",
          posthog: { key: "phc_project", host: "https://us.i.posthog.com" },
        })
      )
    )
    window.history.pushState({}, "", "/agents/thread-id?token=secret")
    const { client, load } = postHogClient()
    productAnalytics.identify({
      login: "alice",
      user_id: "user-uuid",
      email: "alice@example.com",
      github_login: "alice",
      avatar_url: null,
      is_admin: false,
    } satisfies SessionUser)

    await productAnalytics.initialize(load)

    expect(client.identify).toHaveBeenCalledWith("user-uuid", {
      email: "alice@example.com",
      github_login: "alice",
    })
    const [key, config] = client.init.mock.calls[0] ?? []
    expect(key).toBe("phc_project")
    const beforeSend = config?.before_send
    if (typeof beforeSend !== "function") throw new Error("before_send unset")
    const origin = window.location.origin
    const event = beforeSend({
      uuid: "event-id",
      event: "$pageview",
      properties: {
        $current_url: `${origin}/agents/thread-id?token=secret`,
        $pathname: "/agents/thread-id",
        $referrer: "https://github.com/acme/widgets/pull/7?tab=files",
        title: "Fix the private billing bug",
      },
      $set_once: { $initial_current_url: `${origin}/agents/thread-id#m` },
    } satisfies CaptureResult)

    expect(event?.properties).toEqual({
      $current_url: `${origin}/agents/:threadId`,
      $pathname: "/agents/:threadId",
      $referrer: "https://github.com/acme/widgets/pull/7",
      title: "Fix the private billing bug",
      product: "open-swe",
      environment: "staging",
      surface: "dashboard",
      platform: "web",
      page_name: "agents",
      $ip: "0.0.0.0",
      $geoip_disable: true,
    })
    expect(event?.$set_once).toEqual({
      $initial_current_url: `${origin}/agents/:threadId`,
    })
  })
})
