import { describe, expect, it } from "vitest"

import { slackAppManifest } from "./slack-manifest"

const CODE_CHANNEL_SCOPES = ["code_channels:manage", "files:read"]
const CODE_CHANNEL_EVENTS = [
  "agent_session_stopped",
  "code_channel_action",
  "message.channels",
  "message.groups",
]

describe("slackAppManifest", () => {
  it("defaults to the legacy Slack integration", () => {
    const manifest = slackAppManifest()

    expect(manifest.features).not.toHaveProperty("code_channels")
    expect(manifest.oauth_config.scopes.bot).not.toEqual(
      expect.arrayContaining(CODE_CHANNEL_SCOPES)
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
