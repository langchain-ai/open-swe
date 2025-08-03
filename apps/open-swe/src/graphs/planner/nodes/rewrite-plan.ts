// TODO: NOT HOOKED UP TO THE GRAPH YET
// TODO: WILL NEED TO REFACTOR TO ALLOW FOR CHATTING WITH PLANNING SUBGRAPH

import { GraphConfig, PlanItem } from "@open-swe/shared/open-swe/types";
import { z } from "zod";
import { tool } from "@langchain/core/tools";
import { traceable } from "langsmith/traceable";
import {
  PlannerGraphState,
  PlannerGraphUpdate,
} from "@open-swe/shared/open-swe/planner/types";
import {
  getInitialUserRequest,
  getRecentUserRequest,
} from "../../../utils/user-request.js";
import {
  loadModel,
  supportsParallelToolCallsParam,
} from "../../../utils/llms/index.js";
import { LLMTask } from "@open-swe/shared/open-swe/llm-task";
import { FallbackRunnable } from "../../../utils/runtime-fallback.js";

const systemPromptIdentifyChanges = `You are operating as an agentic coding assistant built by LangChain. You've previously been given a task to generate a plan of action for, to address the user's initial request.

Here is the user's initial request:
{USER_INITIAL_REQUEST}

After generating that plan, the user has submitted some feedback/change requests. You should now identify exactly which tasks in the plan should be modified based on their request.

Here is their request:
{USER_REQUEST}

The plan you generated originally, which they submitted the above request for is as follows:
{PLAN}

Please read over the generated plan, and the user's request, and identify exactly which tasks in the plan should be modified/removed. Call the 'identify_plan_changes' tool and use the indices of the tasks listed above when calling the tool.`;

const systemPrompt = `You are operating as an agentic coding assistant built by LangChain. You've previously been given a task to generate a plan of action for, to address the user's initial request.

In this step, the user has requested you rewrite/modify parts of a high-level plan. You have already identified the specific tasks in the plan that should be modified/removed based on the user's request.

Here is the user's initial request which you used to generate the initial plan:
{USER_INITIAL_REQUEST}

Here is the full plan you generated:
{PLAN}

Here is the request the user has just made which you should use to rewrite/modify the plan:
{USER_REQUEST}

And here are the specific tasks in the plan which were identified as tasks the user wants to modify/remove:
{TASKS_TO_MODIFY}

Given this context, please address the user's request to modify/remove/add the tasks in the plan.

You MUST adhere to the following criteria when generating the plan:
- Make as few changes as possible to the tasks, while still following the users request.
- You should NOT make ANY changes to the tasks in the plan that are NOT listed as tasks to modify/remove.
- Do NOT modify tasks in the plan not listed as tasks to modify/remove.
- When responding, ensure you include the unmodified tasks in the plan, as well as the modified/new tasks.
- To remove a specific task, simply do NOT include it in the response.
- To add a new task, simply include it in the response.
`;

const formatSysPromptIdentifyTasks = (
  userInitialRequest: string,
  userRequest: string,
  previousPlan: string[],
) => {
  return systemPromptIdentifyChanges
    .replace("{USER_INITIAL_REQUEST}", userInitialRequest)
    .replace("{USER_REQUEST}", userRequest)
    .replace(
      "{PLAN}",
      previousPlan.map((plan, index) => `${index}: ${plan}`).join("\n"),
    );
};

const formatSysPromptRewritePlan = (
  userInitialRequest: string,
  userRequest: string,
  previousPlan: string[],
  tasksToModify: PlanItem[],
) => {
  return systemPrompt
    .replace("{USER_INITIAL_REQUEST}", userInitialRequest)
    .replace("{USER_REQUEST}", userRequest)
    .replace(
      "{PLAN}",
      previousPlan.map((plan, index) => `${index}: ${plan}`).join("\n"),
    )
    .replace(
      "{TASKS_TO_MODIFY}",
      tasksToModify.map((p) => `${p.index}: ${p.plan}`).join("\n"),
    );
};

async function identifyTasksToModifyFunc(
  state: PlannerGraphState,
  model: FallbackRunnable,
  supportsParallelToolCallsParam: boolean,
): Promise<PlanItem[]> {
  if (!state.planChangeRequest) {
    throw new Error("No plan change request found.");
  }

  const identifyPlanChangesSchema = z.object({
    task_change_indices: z
      .array(z.number())
      .describe(
        "The indices of the tasks in the plan that should be modified/removed.",
      ),
  });

  const identifyPlanChangesTool = tool(
    (input): PlanItem[] => {
      const { task_change_indices } = input;
      const tasksToModify = state.proposedPlan.flatMap((plan, planIndex) => {
        const planItem = task_change_indices.some(
          (changeIndex) => changeIndex === planIndex,
        );
        if (!planItem) {
          return [];
        }
        return {
          index: planIndex,
          plan: plan,
          completed: false,
        };
      });

      return tasksToModify;
    },
    {
      name: "identify_plan_changes",
      schema: identifyPlanChangesSchema,
      description:
        "Identify which tasks in the plan should be modified/removed based on the user's request.",
    },
  );

  const modelWithIdentifyChangesTool = model.bindTools(
    [identifyPlanChangesTool],
    {
      // The model should always call the tool when identifying plan changes.
      tool_choice: identifyPlanChangesTool.name,
      ...(supportsParallelToolCallsParam
        ? {
            parallel_tool_calls: false,
          }
        : {}),
    },
  );

  const userInitialRequest = getInitialUserRequest(state.messages);
  const userFollowupRequest = getRecentUserRequest(state.messages);
  const userRequest =
    userFollowupRequest ?? userInitialRequest ?? "No user message found";

  const response = await modelWithIdentifyChangesTool.invoke([
    {
      role: "user",
      content: formatSysPromptIdentifyTasks(
        userRequest,
        state.planChangeRequest,
        state.proposedPlan,
      ),
    },
  ]);

  const toolCall = response.tool_calls?.[0];
  if (!toolCall) {
    throw new Error(
      "Tool call not returned when attempting to identify plan changes.",
    );
  }

  const tasksToModify = await identifyPlanChangesTool.invoke(
    toolCall.args as z.infer<typeof identifyPlanChangesSchema>,
  );
  return tasksToModify;
}

const identifyTasksToModify = traceable(identifyTasksToModifyFunc, {
  name: "identify_tasks_to_modify",
});

async function updatePlanTasksFunc(
  state: PlannerGraphState,
  tasksToModify: PlanItem[],
  model: FallbackRunnable,
  supportsParallelToolCallsParam: boolean,
): Promise<string[]> {
  if (!state.planChangeRequest) {
    throw new Error("No plan change request found.");
  }

  const updatePlanTasksSchema = z.object({
    updated_plan_tasks: z
      .array(
        z
          .string()
          .describe(
            "The updated or unmodified plan for the task. Do NOT include the task index.",
          ),
      )
      .describe(
        "The updated plan tasks. Must be in the order of which they should be executed in.",
      ),
  });
  const updatePlanTasksTool = {
    name: "update_plan_tasks",
    description: "Call this tool to respond with the updated plan.",
    schema: updatePlanTasksSchema,
  };

  const modelWithUpdatePlanTasksTool = model.bindTools([updatePlanTasksTool], {
    // The model should always call the tool when identifying plan changes.
    tool_choice: updatePlanTasksTool.name,
    ...(supportsParallelToolCallsParam
      ? {
          parallel_tool_calls: false,
        }
      : {}),
  });

  const userInitialRequest = getInitialUserRequest(state.messages);
  const userFollowupRequest = getRecentUserRequest(state.messages);
  const userRequest =
    userFollowupRequest ?? userInitialRequest ?? "No user message found";

  const response = await modelWithUpdatePlanTasksTool.invoke([
    {
      role: "user",
      content: formatSysPromptRewritePlan(
        userRequest,
        state.planChangeRequest,
        state.proposedPlan,
        tasksToModify,
      ),
    },
  ]);

  const toolCall = response.tool_calls?.[0];
  if (!toolCall) {
    throw new Error(
      "Tool call not returned when attempting to update plan tasks.",
    );
  }

  return (
    toolCall.args as z.infer<typeof updatePlanTasksSchema>
  ).updated_plan_tasks.map((p) => p);
}

const updatePlanTasks = traceable(updatePlanTasksFunc, {
  name: "update_plan_tasks",
});

export async function rewritePlan(
  state: PlannerGraphState,
  config: GraphConfig,
): Promise<PlannerGraphUpdate> {
  if (!state.planChangeRequest) {
    throw new Error("No plan change request found.");
  }

  const model = await loadModel(config, LLMTask.PLANNER);
  const modelSupportsParallelToolCallsParam = supportsParallelToolCallsParam(
    config,
    LLMTask.PLANNER,
  );
  const tasksToModify = await identifyTasksToModify(
    state,
    model,
    modelSupportsParallelToolCallsParam,
  );
  const updatedPlanTasks = await updatePlanTasks(
    state,
    tasksToModify,
    model,
    modelSupportsParallelToolCallsParam,
  );

  return {
    proposedPlan: updatedPlanTasks,
  };
}
