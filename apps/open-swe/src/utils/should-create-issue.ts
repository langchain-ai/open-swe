import { GraphConfig } from "@openswe/shared/open-swe/types";

export function shouldCreateIssue(config: GraphConfig): boolean {
  return config.configurable?.shouldCreateIssue === true;
}
