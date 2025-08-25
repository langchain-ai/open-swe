import { GraphConfig } from "@open-swe/shared/open-swe/types";

export function shouldUseLangEng(config: GraphConfig): boolean {
  return config.configurable?.langEng === true;
}
