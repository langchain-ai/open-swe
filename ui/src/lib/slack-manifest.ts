const BASE_BOT_SCOPES = [
  "reactions:write",
  "commands",
  "app_mentions:read",
  "channels:history",
  "channels:read",
  "channels:join",
  "chat:write",
  "files:read",
  "files:write",
  "groups:history",
  "groups:read",
  "im:history",
  "im:read",
  "im:write",
  "mpim:history",
  "mpim:read",
  "search:read.files",
  "search:read.public",
  "search:read.users",
  "team:read",
  "users:read",
  "users:read.email",
]

const BASE_BOT_EVENTS = [
  "app_mention",
  "message.im",
  "message.mpim",
  "message.channels",
  "message.groups",
  "channel_created",
  "channel_rename",
  "channel_archive",
  "agent_session_stopped",
]

const BACKEND_URL_PLACEHOLDER = "https://<your-backend-url>"

export interface SlackManifestConfig {
  backendUrl?: string | null
}

export function slackManifestPlaceholdersRemain(
  config: SlackManifestConfig = {}
): boolean {
  return !config.backendUrl?.trim()
}

export function slackAppManifest(config: SlackManifestConfig = {}) {
  const backendUrl =
    config.backendUrl?.trim().replace(/\/+$/, "") || BACKEND_URL_PLACEHOLDER

  const features: Record<string, unknown> = {
    app_home: {
      home_tab_enabled: false,
      messages_tab_enabled: true,
      messages_tab_read_only_enabled: false,
    },
    bot_user: { display_name: "Robin", always_online: true },
    shortcuts: [
      {
        name: "Explain This",
        type: "message",
        callback_id: "open_swe_explain",
        description: "Ask Robin to explain this message",
      },
    ],
  }

  return {
    display_information: {
      name: "Robin",
      description: "Enables Robin to interact with your workspace",
      background_color: "#000000",
    },
    features,
    oauth_config: {
      redirect_urls: [`${backendUrl}/dashboard/api/slack/callback`],
      scopes: {
        bot: BASE_BOT_SCOPES,
      },
    },
    settings: {
      event_subscriptions: {
        request_url: `${backendUrl}/webhooks/slack`,
        bot_events: BASE_BOT_EVENTS,
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

export function slackAppManifestJson(config: SlackManifestConfig = {}): string {
  return JSON.stringify(slackAppManifest(config), null, 2)
}
