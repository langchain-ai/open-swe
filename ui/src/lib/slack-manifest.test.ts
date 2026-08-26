import { describe, expect, it } from "vitest"

import { slackAppManifest } from "./slack-manifest"

const CODE_CHANNEL_SCOPES = ["code_channels:manage", "files:read"]
const CODE_CHANNEL_EVENTS = ["code_channel_action", "message.channels", "message.groups"]
const AGENT_EVENTS = ["agent_session_stopped", "app_context_changed", "app_home_opened"]

describe("slackAppManifest", () => {
  it("includes the native Slack Agent experience", () => {
    const manifest = slackAppManifest()

    expect(manifest.features).not.toHaveProperty("code_channels")
    expect(manifest.features).toHaveProperty("agent_view")
    expect(manifest.oauth_config.scopes.bot).toContain("assistant:write")
    expect(manifest.oauth_config.scopes.bot).not.toEqual(
      expect.arrayContaining(CODE_CHANNEL_SCOPES)
    )
    expect(manifest.settings.event_subscriptions.bot_events).toEqual(
      expect.arrayContaining(AGENT_EVENTS)
    )
    expect(manifest.settings.event_subscriptions.bot_events).not.toEqual(
      expect.arrayContaining(CODE_CHANNEL_EVENTS)
    )
  })

  it("adds the complete Code Channels manifest surface when enabled", () => {
    const manifest = slackAppManifest(true)

    expect(manifest.features.code_channels).toEqual({
      enabled: true,
      slash_command_url:
        "https://<your-backend-url>/webhooks/slack/code-channel-commands",
    })
    expect(manifest.oauth_config.scopes.bot).toEqual(
      expect.arrayContaining(CODE_CHANNEL_SCOPES)
    )
    expect(manifest.settings.event_subscriptions.bot_events).toEqual(
      expect.arrayContaining(CODE_CHANNEL_EVENTS)
    )
  })
})
