import { GraphConfig } from "@open-swe/shared/open-swe/types";

export function shouldCreateIssue(config: GraphConfig): boolean {
  return config.configurable?.shouldCreateIssue !== false;
}
