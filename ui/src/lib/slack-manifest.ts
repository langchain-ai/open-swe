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
const AGENT_BOT_EVENTS = [
  "agent_session_stopped",
  "app_context_changed",
  "app_home_opened",
]

export function slackAppManifest(codeChannelsEnabled = false) {
  const features: Record<string, unknown> = {
    agent_view: {
      agent_description:
        "A software engineering agent that works in isolated sandboxes and opens pull requests.",
      suggested_prompts: [
        {
          title: "Start a coding task",
          message: "Please implement this change: ",
        },
        {
          title: "Investigate an issue",
          message: "Please investigate this issue: ",
        },
      ],
    },
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
      slash_command_url:
        "https://<your-backend-url>/webhooks/slack/code-channel-commands",
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
        "https://smith.langchain.com/host-oauth-callback/<your-provider-id>",
        "http://localhost:2024/dashboard/api/slack/callback",
      ],
      scopes: {
        bot: codeChannelsEnabled
          ? [
              ...LEGACY_BOT_SCOPES,
              "assistant:write",
              "code_channels:manage",
              "files:read",
            ]
          : [...LEGACY_BOT_SCOPES, "assistant:write"],
      },
    },
    settings: {
      event_subscriptions: {
        request_url: "https://<your-ngrok-url>/webhooks/slack",
        bot_events: codeChannelsEnabled
          ? [
              ...LEGACY_BOT_EVENTS,
              ...AGENT_BOT_EVENTS,
              "code_channel_action",
              "message.channels",
              "message.groups",
            ]
          : [...LEGACY_BOT_EVENTS, ...AGENT_BOT_EVENTS],
      },
      interactivity: {
        is_enabled: true,
        request_url: "https://<your-ngrok-url>/webhooks/slack/interactivity",
      },
      org_deploy_enabled: false,
      socket_mode_enabled: false,
      token_rotation_enabled: false,
    },
  }
}

export function slackAppManifestJson(codeChannelsEnabled = false): string {
  return JSON.stringify(slackAppManifest(codeChannelsEnabled), null, 2)
}
