import { GraphConfig } from "@open-swe/shared/open-swe/types";

export function shouldUseCustomFramework(config: GraphConfig): boolean {
  return config.configurable?.customFramework === true;
}
