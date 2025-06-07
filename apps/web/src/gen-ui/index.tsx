import { ApplyPatch } from "./apply-patch"
import { DiagnoseError } from "./diagnose-error"
import { InitializeStep } from "./initialize-step"
import { PushChanges } from "./push-changes"
import { ReplanningStep } from "./replanning-step"
import { Shell } from "./shell-command"
import { TaskSummary } from "./task-summary"

export default {
  "apply-patch": ApplyPatch,
  "diagnose-error": DiagnoseError,
  "initialize-step": InitializeStep,
  "push-changes": PushChanges,
  "replanning-step": ReplanningStep,
  "shell-command": Shell,
  "task-summary": TaskSummary,
}