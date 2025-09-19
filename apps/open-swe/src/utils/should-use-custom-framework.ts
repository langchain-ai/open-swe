import { GraphConfig } from "@openswe/shared/open-swe/types";

export function shouldUseCustomFramework(config: GraphConfig): boolean {
  return config.configurable?.customFramework === true;
}
