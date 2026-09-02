import { describe, expect, it } from "vitest"

import {
  slackAppManifest,
  slackManifestPlaceholdersRemain,
} from "./slack-manifest"

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
    expect(manifest.oauth_config.redirect_urls).toContain(
      "https://smith.langchain.com/host-oauth-callback/<your-provider-id>"
    )
    expect(manifest.oauth_config.scopes.bot).toEqual(
      expect.arrayContaining(CODE_CHANNEL_SCOPES)
    )
    expect(manifest.settings.event_subscriptions.bot_events).toEqual(
      expect.arrayContaining(CODE_CHANNEL_EVENTS)
    )
  })

  it("fills in the running deployment's backend URL and provider id", () => {
    const manifest = slackAppManifest(true, {
      backendUrl: "https://openswe.example.com/",
      providerId: "acme-github-oauth",
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
      "https://smith.langchain.com/host-oauth-callback/acme-github-oauth",
      "https://openswe.example.com/dashboard/api/slack/callback",
    ])
  })

  it("reports whether any placeholder survives the given config", () => {
    expect(slackManifestPlaceholdersRemain()).toBe(true)
    expect(
      slackManifestPlaceholdersRemain({ backendUrl: "https://a.example.com" })
    ).toBe(true)
    expect(
      slackManifestPlaceholdersRemain({
        backendUrl: "https://a.example.com",
        providerId: "p",
      })
    ).toBe(false)
  })
})
