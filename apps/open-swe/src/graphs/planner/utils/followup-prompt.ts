import { TaskPlan } from "@open-swe/shared/open-swe/types";

const followupMessagePrompt = `
The user is sending a followup request for you to generate a plan for. You are provided with the following context to aid in your new plan context gathering steps:
  - The previous user requests, along with the tasks, and task summaries you generated for these previous requests.
  - You are only provided this information as context to reference when gathering context for the new plan.

Here is the complete list of previous requests made by the user, along with the tasks you generated to complete these requests, and the task summaries of each task you completed previously:
{PREVIOUS_PLAN}
`;

const formatPreviousPlans = (tasks: TaskPlan): string => {
  return tasks.tasks
    .map((task) => {
      const activePlanItems =
        task.planRevisions[task.activeRevisionIndex].plans;

      return `<previous-task index="${task.taskIndex}">
  User request: ${task.request}
  
  Overall task summary:\n</task-summary>\n${task.summary || "No overall task summary found"}\n</task-summary>
  
  Individual tasks you generated to complete this request:
  ${activePlanItems.map((planItem) => `<plan-item index="${planItem.index}">${planItem.plan}</plan-item>`).join("\n")}
  </previous-task>`;
    })
    .join("\n");
};

export function formatFollowupMessagePrompt(tasks: TaskPlan): string {
  return followupMessagePrompt.replace(
    "{PREVIOUS_PLAN}",
    formatPreviousPlans(tasks),
  );
}
