// Migration utility to convert old API key format to new format
// Old format: { "anthropicApiKey": "key", "openaiApiKey": "key", "googleApiKey": "key" }
// New format: { "anthropic": { name: "ANTHROPIC_API_KEY", apiKey: "key", allowedInDev: false }, ... }
import { EnvVarConfig } from "@open-swe/shared/open-swe/types";
import { isEnvVarConfig } from "@open-swe/shared/env-config";

interface ApiKeyFormat {
  [providerId: string]: EnvVarConfig;
}

const OLD_TO_NEW_MAPPING = {
  anthropicApiKey: { id: "anthropic", name: "ANTHROPIC_API_KEY" },
  openaiApiKey: { id: "openai", name: "OPENAI_API_KEY" },
  googleApiKey: { id: "google", name: "GOOGLE_API_KEY" },
  daytonaApiKey: { id: "daytona", name: "DAYTONA_API_KEY" },
} as const;

/**
 * Checks if the given config contains old-format API keys
 */
export function hasOldFormatApiKeys(config: Record<string, any>): boolean {
  if (!config || typeof config !== "object") return false;
  if (!config.apiKeys || typeof config.apiKeys !== "object") return false;

  return Object.keys(OLD_TO_NEW_MAPPING).some(
    (oldKey) => oldKey in config.apiKeys,
  );
}

/**
 * Migrates old API key format to new format
 */
export function migrateApiKeys(
  config: Record<string, any>,
): Record<string, any> {
  if (!config || typeof config !== "object") return config;

  const newApiKeys: ApiKeyFormat = {};
  const updatedConfig = { ...config };

  // Migrate each old key to new format
  Object.entries(OLD_TO_NEW_MAPPING).forEach(([oldKey, mapping]) => {
    const oldValue = config.apiKeys[oldKey];
    if (!newApiKeys[mapping.id]) {
      newApiKeys[mapping.id] = {
        name: mapping.name,
        apiKey: oldValue,
        allowedInDev: false,
      };
    }
  });

  // Add any existing new-format keys that weren't migrated
  if (config.apiKeys && typeof config.apiKeys === "object") {
    Object.entries(config.apiKeys).forEach(([key, value]) => {
      if (isEnvVarConfig(value)) {
        newApiKeys[key] = value;
      }
    });
  }

  updatedConfig.apiKeys = newApiKeys;

  return updatedConfig;
}
