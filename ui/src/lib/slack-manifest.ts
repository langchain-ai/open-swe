const BASE_BOT_SCOPES = [
  "reactions:write",
  "commands",
  "app_mentions:read",
  "channels:history",
  "channels:read",
  "channels:join",
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

const BASE_BOT_EVENTS = [
  "app_mention",
  "message.im",
  "message.mpim",
  "message.channels",
  "channel_created",
  "channel_rename",
  "channel_archive",
]

const BACKEND_URL_PLACEHOLDER = "https://<your-backend-url>"

export const ASK_COMMAND = "/swe"

export interface SlackManifestConfig {
  backendUrl?: string | null
}

export function slackManifestPlaceholdersRemain(
  config: SlackManifestConfig = {}
): boolean {
  return !config.backendUrl?.trim()
}

export function slackAppManifest(
  codeChannelsEnabled = false,
  config: SlackManifestConfig = {}
) {
  const backendUrl =
    config.backendUrl?.trim().replace(/\/+$/, "") || BACKEND_URL_PLACEHOLDER

  const features: Record<string, unknown> = {
    app_home: {
      home_tab_enabled: false,
      messages_tab_enabled: true,
      messages_tab_read_only_enabled: false,
    },
    bot_user: { display_name: "Open SWE", always_online: true },
    slash_commands: [
      {
        command: ASK_COMMAND,
        url: `${backendUrl}/webhooks/slack/commands`,
        description: "Ask Open SWE a single question",
        usage_hint: "how does thread routing work?",
        should_escape: false,
      },
    ],
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
      redirect_urls: [`${backendUrl}/dashboard/api/slack/callback`],
      scopes: {
        bot: codeChannelsEnabled
          ? [
              ...BASE_BOT_SCOPES,
              "code_channels:manage",
              "files:read",
              // conversations.invite, for public and private channels.
              "channels:manage",
              "groups:write",
            ]
          : BASE_BOT_SCOPES,
      },
    },
    settings: {
      event_subscriptions: {
        request_url: `${backendUrl}/webhooks/slack`,
        bot_events: codeChannelsEnabled
          ? [
              ...BASE_BOT_EVENTS,
              "agent_session_stopped",
              "code_channel_action",
              "message.groups",
            ]
          : BASE_BOT_EVENTS,
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
