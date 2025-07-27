import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { Task, TASK_TO_CONFIG_DEFAULTS_MAP } from "./constants.js";

export function getModelName(config: GraphConfig, task: Task): string {
  return (
    config.configurable?.[`${task}ModelName`] ??
    TASK_TO_CONFIG_DEFAULTS_MAP[task].modelName
  );
}
