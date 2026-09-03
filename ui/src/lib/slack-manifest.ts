const LEGACY_BOT_SCOPES = [
  "reactions:write",
  "app_mentions:read",
  "channels:history",
  "channels:read",
  "chat:write",
  "files:write",
  "groups:history",
  "groups:read",
  "im:history",
  "im:read",
  "im:write",
  "mpim:history",
  "mpim:read",
  "team:read",
  "users:read",
  "users:read.email",
]

const LEGACY_BOT_EVENTS = ["app_mention", "message.im", "message.mpim"]

const BACKEND_URL_PLACEHOLDER = "https://<your-backend-url>"
const PROVIDER_ID_PLACEHOLDER = "<your-provider-id>"

export interface SlackManifestConfig {
  backendUrl?: string | null
  providerId?: string | null
}

export function slackManifestPlaceholdersRemain(
  config: SlackManifestConfig = {}
): boolean {
  return !config.backendUrl?.trim() || !config.providerId?.trim()
}

export function slackAppManifest(
  codeChannelsEnabled = false,
  config: SlackManifestConfig = {}
) {
  const backendUrl =
    config.backendUrl?.trim().replace(/\/+$/, "") || BACKEND_URL_PLACEHOLDER
  const providerId = config.providerId?.trim() || PROVIDER_ID_PLACEHOLDER

  const features: Record<string, unknown> = {
    app_home: {
      home_tab_enabled: false,
      messages_tab_enabled: true,
      messages_tab_read_only_enabled: false,
    },
    bot_user: { display_name: "Open SWE", always_online: true },
  }
  if (codeChannelsEnabled) {
    features.code_channels = {
      enabled: true,
      slash_command_url: `${backendUrl}/webhooks/slack/code-channel-commands`,
    }
  }

  return {
    display_information: {
      name: "Open SWE",
      description: "Enables Open SWE to interact with your workspace",
      background_color: "#000000",
    },
    features,
    oauth_config: {
      redirect_urls: [
        `https://smith.langchain.com/host-oauth-callback/${providerId}`,
        `${backendUrl}/dashboard/api/slack/callback`,
      ],
      scopes: {
        bot: codeChannelsEnabled
          ? [...LEGACY_BOT_SCOPES, "code_channels:manage", "files:read"]
          : LEGACY_BOT_SCOPES,
      },
    },
    settings: {
      event_subscriptions: {
        request_url: `${backendUrl}/webhooks/slack`,
        bot_events: codeChannelsEnabled
          ? [
              "app_mention",
              "agent_session_stopped",
              "code_channel_action",
              "message.channels",
              "message.groups",
              "message.im",
              "message.mpim",
            ]
          : LEGACY_BOT_EVENTS,
      },
      interactivity: {
        is_enabled: true,
        request_url: `${backendUrl}/webhooks/slack/interactivity`,
      },
      org_deploy_enabled: false,
      socket_mode_enabled: false,
      token_rotation_enabled: false,
    },
  }
}

export function slackAppManifestJson(
  codeChannelsEnabled = false,
  config: SlackManifestConfig = {}
): string {
  return JSON.stringify(slackAppManifest(codeChannelsEnabled, config), null, 2)
}
