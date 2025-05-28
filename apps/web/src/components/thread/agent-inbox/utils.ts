import { BaseMessage, isBaseMessage } from "@langchain/core/messages";
import { format } from "date-fns";
import { startCase } from "lodash";
import { HumanResponseWithEdits, SubmitType } from "./types";
import { HumanInterrupt } from "@langchain/langgraph/prebuilt";

export function prettifyText(action: string) {
  return startCase(action.replace(/_/g, " "));
}

export function isArrayOfMessages(
  value: Record<string, any>[],
): value is BaseMessage[] {
  if (
    value.every(isBaseMessage) ||
    (Array.isArray(value) &&
      value.every(
        (v) =>
          typeof v === "object" &&
          "id" in v &&
          "type" in v &&
          "content" in v &&
          "additional_kwargs" in v,
      ))
  ) {
    return true;
  }
  return false;
}

export function baseMessageObject(item: unknown): string {
  if (isBaseMessage(item)) {
    const contentText =
      typeof item.content === "string"
        ? item.content
        : JSON.stringify(item.content, null);
    let toolCallText = "";
    if ("tool_calls" in item) {
      toolCallText = JSON.stringify(item.tool_calls, null);
    }
    if ("type" in item) {
      return `${item.type}:${contentText ? ` ${contentText}` : ""}${toolCallText ? ` - Tool calls: ${toolCallText}` : ""}`;
    } else if ("_getType" in item) {
      return `${item._getType()}:${contentText ? ` ${contentText}` : ""}${toolCallText ? ` - Tool calls: ${toolCallText}` : ""}`;
    }
  } else if (
    typeof item === "object" &&
    item &&
    "type" in item &&
    "content" in item
  ) {
    const contentText =
      typeof item.content === "string"
        ? item.content
        : JSON.stringify(item.content, null);
    let toolCallText = "";
    if ("tool_calls" in item) {
      toolCallText = JSON.stringify(item.tool_calls, null);
    }
    return `${item.type}:${contentText ? ` ${contentText}` : ""}${toolCallText ? ` - Tool calls: ${toolCallText}` : ""}`;
  }

  if (typeof item === "object") {
    return JSON.stringify(item, null);
  } else {
    return item as string;
  }
}

export function unknownToPrettyDate(input: unknown): string | undefined {
  try {
    if (
      Object.prototype.toString.call(input) === "[object Date]" ||
      new Date(input as string)
    ) {
      return format(new Date(input as string), "MM/dd/yyyy hh:mm a");
    }
  } catch (_) {
    // failed to parse date. no-op
  }
  return undefined;
}

export function createDefaultHumanResponse(
  interrupt: HumanInterrupt,
  initialHumanInterruptEditValue: React.MutableRefObject<
    Record<string, string>
  >,
): {
  responses: HumanResponseWithEdits[];
  defaultSubmitType: SubmitType | undefined;
  hasAccept: boolean;
} {
  const responses: HumanResponseWithEdits[] = [];
  if (interrupt.config.allow_edit) {
    if (interrupt.config.allow_accept) {
      Object.entries(interrupt.action_request.args).forEach(([k, v]) => {
        let stringValue = "";
        if (typeof v === "string") {
          stringValue = v;
        } else {
          stringValue = JSON.stringify(v, null);
        }

        if (
          !initialHumanInterruptEditValue.current ||
          !(k in initialHumanInterruptEditValue.current)
        ) {
          initialHumanInterruptEditValue.current = {
            ...initialHumanInterruptEditValue.current,
            [k]: stringValue,
          };
        } else if (
          k in initialHumanInterruptEditValue.current &&
          initialHumanInterruptEditValue.current[k] !== stringValue
        ) {
          console.error(
            "KEY AND VALUE FOUND IN initialHumanInterruptEditValue.current THAT DOES NOT MATCH THE ACTION REQUEST",
            {
              key: k,
              value: stringValue,
              expectedValue: initialHumanInterruptEditValue.current[k],
            },
          );
        }
      });
      responses.push({
        type: "edit",
        args: interrupt.action_request,
        acceptAllowed: true,
        editsMade: false,
      });
    } else {
      responses.push({
        type: "edit",
        args: interrupt.action_request,
        acceptAllowed: false,
      });
    }
  }
  if (interrupt.config.allow_respond) {
    responses.push({
      type: "response",
      args: "",
    });
  }

  if (interrupt.config.allow_ignore) {
    responses.push({
      type: "ignore",
      args: null,
    });
  }

  // Set the submit type.
  // Priority: accept > response  > edit
  const acceptAllowedConfig = interrupt.config.allow_accept;
  const ignoreAllowedConfig = interrupt.config.allow_ignore;

  const hasResponse = responses.find((r) => r.type === "response");
  const hasAccept =
    responses.find((r) => r.acceptAllowed) || acceptAllowedConfig;
  const hasEdit = responses.find((r) => r.type === "edit");

  let defaultSubmitType: SubmitType | undefined;
  if (hasAccept) {
    defaultSubmitType = "accept";
  } else if (hasResponse) {
    defaultSubmitType = "response";
  } else if (hasEdit) {
    defaultSubmitType = "edit";
  }

  if (acceptAllowedConfig && !responses.find((r) => r.type === "accept")) {
    responses.push({
      type: "accept",
      args: null,
    });
  }
  if (ignoreAllowedConfig && !responses.find((r) => r.type === "ignore")) {
    responses.push({
      type: "ignore",
      args: null,
    });
  }

  return { responses, defaultSubmitType, hasAccept: !!hasAccept };
}

export function constructOpenInStudioURL(
  deploymentUrl: string,
  threadId?: string,
) {
  const smithStudioURL = new URL("https://smith.langchain.com/studio/thread");
  // trim the trailing slash from deploymentUrl
  const trimmedDeploymentUrl = deploymentUrl.replace(/\/$/, "");

  if (threadId) {
    smithStudioURL.pathname += `/${threadId}`;
  }

  smithStudioURL.searchParams.append("baseUrl", trimmedDeploymentUrl);

  return smithStudioURL.toString();
}

export function haveArgsChanged(
  args: unknown,
  initialValues: Record<string, string>,
): boolean {
  if (typeof args !== "object" || !args) {
    return false;
  }

  const currentValues = args as Record<string, string>;

  return Object.entries(currentValues).some(([key, value]) => {
    const valueString = ["string", "number"].includes(typeof value)
      ? value.toString()
      : JSON.stringify(value, null);
    return initialValues[key] !== valueString;
  });
}
