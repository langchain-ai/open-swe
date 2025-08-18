import { EnvVarConfig } from "./open-swe/types.js";

/**
 * Type guard to check if an object is a valid EnvVarConfig
 */
export function isEnvVarConfig(obj: any): obj is EnvVarConfig {
  return (
    typeof obj === "object" &&
    obj !== null &&
    "name" in obj &&
    "apiKey" in obj &&
    "allowedInDev" in obj &&
    typeof obj.apiKey === "string"
  );
}
