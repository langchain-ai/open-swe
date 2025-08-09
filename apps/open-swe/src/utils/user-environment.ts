import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { decryptSecret } from "@open-swe/shared/crypto";
import { isEnvVarConfig } from "@open-swe/shared/env-config";

/**
 * Extracts user environment variables from GraphConfig and returns them as a Record
 * Only includes keys that are marked as allowedInDev
 */
export function getUserEnvironmentVariables(
  config: GraphConfig,
): Record<string, string> {
  const userEnvs: Record<string, string> = {};

  const secretsEncryptionKey = process.env.SECRETS_ENCRYPTION_KEY;
  if (!secretsEncryptionKey) {
    throw new Error("SECRETS_ENCRYPTION_KEY environment variable is required");
  }

  const apiKeys = config.configurable?.apiKeys;
  if (!apiKeys) {
    return userEnvs;
  }

  for (const envVarConfig of Object.values(apiKeys)) {
    if (isEnvVarConfig(envVarConfig) && envVarConfig.allowedInDev) {
      try {
        const decryptedKey = decryptSecret(
          envVarConfig.apiKey,
          secretsEncryptionKey,
        );
        if (decryptedKey && envVarConfig.name) {
          userEnvs[envVarConfig.name] = decryptedKey; // Use the actual env var name
        }
      } catch (error) {
        throw new Error(
          `Failed to decrypt key for env var ${envVarConfig.name}: ${error}`,
        );
      }
    }
  }

  return userEnvs;
}
