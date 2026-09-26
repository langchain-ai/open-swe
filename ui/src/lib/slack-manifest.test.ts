import { describe, expect, it } from "vitest"

import {
  slackAppManifest,
  slackManifestPlaceholdersRemain,
} from "./slack-manifest"

const CODE_CHANNEL_SCOPES = [
  "code_channels:manage",
  "files:read",
  // conversations.invite needs these, or a named invitee silently stays out.
  "channels:manage",
  "groups:write",
]
const CODE_CHANNEL_EVENTS = [
  "agent_session_stopped",
  "code_channel_action",
  "message.channels",
  "message.groups",
]

describe("slackAppManifest", () => {
  it.each([false, true])(
    "includes Incidents channel onboarding and history events with Code Channels=%s",
    (codeChannelsEnabled) => {
      const manifest = slackAppManifest(codeChannelsEnabled)
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
        ])
      )
      expect(new Set(events).size).toBe(events.length)
    }
  )

  it.each([false, true])(
    "registers the ask slash command with Code Channels=%s",
    (codeChannelsEnabled) => {
      const manifest = slackAppManifest(codeChannelsEnabled, {
        backendUrl: "https://openswe.example.com",
      })

      expect(manifest.features.slash_commands).toEqual([
        expect.objectContaining({
          command: "/oswe",
          url: "https://openswe.example.com/webhooks/slack/commands",
        }),
      ])
      expect(manifest.oauth_config.scopes.bot).toEqual(
        expect.arrayContaining(["commands"])
      )
    }
  )

  it("defaults to the legacy Slack integration", () => {
    const manifest = slackAppManifest()

    expect(manifest.features).not.toHaveProperty("code_channels")
    expect(manifest.oauth_config.scopes.bot).not.toEqual(
      expect.arrayContaining(CODE_CHANNEL_SCOPES)
    )
    expect(manifest.settings.event_subscriptions.bot_events).not.toContain(
      "code_channel_action"
    )
  })

  it("adds the complete Code Channels manifest surface when enabled", () => {
    const manifest = slackAppManifest(true)

    expect(manifest.features.code_channels).toEqual({
      enabled: true,
      slash_command_url:
        "https://<your-backend-url>/webhooks/slack/code-channel-commands",
    })
    expect(manifest.oauth_config.redirect_urls).toEqual([
      "https://<your-backend-url>/dashboard/api/slack/callback",
    ])
    expect(manifest.oauth_config.scopes.bot).toEqual(
      expect.arrayContaining(CODE_CHANNEL_SCOPES)
    )
    expect(manifest.settings.event_subscriptions.bot_events).toEqual(
      expect.arrayContaining(CODE_CHANNEL_EVENTS)
    )
  })

  it("fills in the running deployment's backend URL", () => {
    const manifest = slackAppManifest(true, {
      backendUrl: "https://openswe.example.com/",
    })

    expect(manifest.settings.event_subscriptions.request_url).toBe(
      "https://openswe.example.com/webhooks/slack"
    )
    expect(manifest.settings.interactivity.request_url).toBe(
      "https://openswe.example.com/webhooks/slack/interactivity"
    )
    expect(manifest.features.code_channels).toEqual({
      enabled: true,
      slash_command_url:
        "https://openswe.example.com/webhooks/slack/code-channel-commands",
    })
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
