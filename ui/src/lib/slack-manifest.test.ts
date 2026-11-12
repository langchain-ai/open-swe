import { describe, expect, it } from "vitest"

import {
  slackAppManifest,
  slackManifestPlaceholdersRemain,
} from "./slack-manifest"

describe("slackAppManifest", () => {
  it("includes Incidents channel onboarding and history events", () => {
    const manifest = slackAppManifest()
    expect(manifest.oauth_config.scopes.bot).toEqual(
      expect.arrayContaining([
        "channels:join",
        "channels:read",
        "channels:history",
      ])
    )
    const events = manifest.settings.event_subscriptions.bot_events
    expect(events).toEqual(
      expect.arrayContaining([
        "channel_created",
        "channel_rename",
        "channel_archive",
        "message.channels",
        "message.groups",
        "app_mention",
        "message.im",
        "message.mpim",
        "agent_session_stopped",
      ])
    )
    expect(new Set(events).size).toBe(events.length)
  })

  it("registers the ask slash command", () => {
    const manifest = slackAppManifest({
      backendUrl: "https://openswe.example.com",
    })

    expect(manifest.features.slash_commands).toEqual([
      expect.objectContaining({
        command: "/robin",
        url: "https://openswe.example.com/webhooks/slack/commands",
      }),
      expect.objectContaining({
        command: "/oswe",
        url: "https://openswe.example.com/webhooks/slack/commands",
      }),
    ])
    expect(manifest.oauth_config.scopes.bot).toEqual(
      expect.arrayContaining(["commands"])
    )
  })

  it("does not request retired code-channel capabilities", () => {
    const manifest = slackAppManifest()

    expect(manifest.oauth_config.scopes.bot).toContain("files:read")
    expect(manifest.features).not.toHaveProperty("code_channels")
    expect(manifest.oauth_config.scopes.bot).not.toContain(
      "code_channels:manage"
    )
    expect(manifest.settings.event_subscriptions.bot_events).not.toContain(
      "code_channel_action"
    )
  })

  it("fills in the running deployment's backend URL", () => {
    const manifest = slackAppManifest({
      backendUrl: "https://openswe.example.com/",
    })

    expect(manifest.settings.event_subscriptions.request_url).toBe(
      "https://openswe.example.com/webhooks/slack"
    )
    expect(manifest.settings.interactivity.request_url).toBe(
      "https://openswe.example.com/webhooks/slack/interactivity"
    )
    expect(manifest.oauth_config.redirect_urls).toEqual([
      "https://openswe.example.com/dashboard/api/slack/callback",
    ])
  })

  it("reports whether any placeholder survives the given config", () => {
    expect(slackManifestPlaceholdersRemain()).toBe(true)
    expect(slackManifestPlaceholdersRemain({ backendUrl: "  " })).toBe(true)
    expect(
      slackManifestPlaceholdersRemain({ backendUrl: "https://a.example.com" })
    ).toBe(false)
  })
})
