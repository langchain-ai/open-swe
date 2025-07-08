"use client";

import { JSX, useState } from "react";
import {
  Terminal,
  FileCode,
  Loader2,
  CheckCircle,
  XCircle,
  ChevronDown,
  ChevronUp,
  MessageSquare,
  FileText,
  CloudDownload,
  Search,
  Globe,
  Hash,
} from "lucide-react";
import {
  createApplyPatchToolFields,
  createShellToolFields,
  createInstallDependenciesToolFields,
  createTakePlannerNotesFields,
  createGetURLContentToolFields,
  createFindInstancesOfToolFields,
  formatRgCommand,
  RipgrepCommand,
} from "@open-swe/shared/open-swe/tools";
import { z } from "zod";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "../ui/tooltip";
import { cn } from "@/lib/utils";

// Used only for Zod type inference.
const dummyRepo = { owner: "dummy", repo: "dummy" };
const shellTool = createShellToolFields(dummyRepo);
type ShellToolArgs = z.infer<typeof shellTool.schema>;
const applyPatchTool = createApplyPatchToolFields(dummyRepo);
type ApplyPatchToolArgs = z.infer<typeof applyPatchTool.schema>;
const installDependenciesTool = createInstallDependenciesToolFields(dummyRepo);
type InstallDependenciesToolArgs = z.infer<
  typeof installDependenciesTool.schema
>;
const plannerNotesTool = createTakePlannerNotesFields();
type PlannerNotesToolArgs = z.infer<typeof plannerNotesTool.schema>;
const getURLContentTool = createGetURLContentToolFields();
type GetURLContentToolArgs = z.infer<typeof getURLContentTool.schema>;
const findInstancesOfTool = createFindInstancesOfToolFields(dummyRepo);
type FindInstancesOfToolArgs = z.infer<typeof findInstancesOfTool.schema>;

// Common props for all action types
type BaseActionProps = {
  status: "loading" | "generating" | "done";
  success?: boolean;
  reasoningText?: string;
  summaryText?: string;
};

// Shell command specific props. We need to wrap the args in Partial<...>
// because even though they're required, they may be undefined at a point in time
// due to streaming.
type ShellActionProps = BaseActionProps &
  Partial<ShellToolArgs> & {
    actionType: "shell";
    output?: string;
    errorCode?: number;
  };

type PatchActionProps = BaseActionProps &
  Partial<ApplyPatchToolArgs> & {
    actionType: "apply-patch";
    errorMessage?: string;
    fixedDiff?: string;
  };

type RgActionProps = BaseActionProps &
  Partial<RipgrepCommand> & {
    actionType: "rg";
    output?: string;
    errorCode?: number;
  };

type InstallDependenciesActionProps = BaseActionProps &
  Partial<InstallDependenciesToolArgs> & {
    actionType: "install_dependencies";
    output?: string;
    errorCode?: number;
  };

type PlannerNotesActionProps = BaseActionProps &
  Partial<PlannerNotesToolArgs> & {
    actionType: "planner_notes";
  };

type GetURLContentActionProps = BaseActionProps &
  Partial<GetURLContentToolArgs> & {
    actionType: "get_url_content";
    output?: string;
  };

type FindInstancesOfActionProps = BaseActionProps &
  Partial<FindInstancesOfToolArgs> & {
    actionType: "find_instances_of";
    output?: string;
    errorCode?: number;
  };

export type ActionItemProps =
  | (BaseActionProps & { status: "loading" })
  | ShellActionProps
  | PatchActionProps
  | RgActionProps
  | InstallDependenciesActionProps
  | PlannerNotesActionProps
  | GetURLContentActionProps
  | FindInstancesOfActionProps;

export type ActionStepProps = {
  actions: ActionItemProps[];
  reasoningText?: string;
  summaryText?: string;
};

const ACTION_GENERATING_TEXT_MAP = {
  [shellTool.name]: "Executing...",
  [applyPatchTool.name]: "Applying patch...",
  ["rg"]: "Searching...",
  [installDependenciesTool.name]: "Installing dependencies...",
  [plannerNotesTool.name]: "Saving notes...",
  [getURLContentTool.name]: "Fetching URL content...",
  [findInstancesOfTool.name]: "Finding instances...",
};

function ToolIconWithTooltip({
  toolNamePretty,
  icon,
}: {
  toolNamePretty: string;
  icon: JSX.Element;
}) {
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          {icon}
        </TooltipTrigger>
        <TooltipContent>
          {toolNamePretty}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function MatchCaseIcon({ matchCase }: { matchCase: boolean }) {
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger className={cn("rounded-sm px-1 py-[2px] text-xs border-1 border-gray-200", matchCase ? "bg-blue-500/60 border-blue-500 text-white" : "bg-none")}>
          <p className="font-mono">Aa</p>
        </TooltipTrigger>
        <TooltipContent>
          Match case {matchCase ? "on" : "off"}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

function MatchWholeWordIcon({ matchWholeWord }: { matchWholeWord: boolean }) {
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger className={cn("rounded-sm px-1 py-[2px] text-xs border-1 border-gray-200", matchWholeWord ? "bg-blue-500/60 border-blue-500 text-white" : "bg-none")}>
          <span className="relative inline-block px-0.5">
            <p className="font-mono relative z-10 m-0 text-center">ab</p>
            <div className={cn("absolute bottom-0 left-0 h-1/5 w-[1px]", matchWholeWord ? "bg-white" : "bg-black dark:bg-white")}></div>
            <div className={cn("absolute bottom-0 left-0 right-0 h-[1px] w-full", matchWholeWord ? "bg-white" : "bg-black dark:bg-white")}></div>
            <div className={cn("absolute bottom-0 right-0 h-1/5 w-[1px]", matchWholeWord ? "bg-white" : "bg-black dark:bg-white")}></div>
          </span>
        </TooltipTrigger>
        <TooltipContent>
          Match whole word {matchWholeWord ? "on" : "off"}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

function ActionItem(props: ActionItemProps) {
  const [expanded, setExpanded] = useState(false);

  const getStatusIcon = () => {
    switch (props.status) {
      case "loading":
        return <div className="border-border size-3.5 rounded-full border" />;
      case "generating":
        return (
          <Loader2 className="text-muted-foreground size-3.5 animate-spin" />
        );
      case "done":
        return props.success ? (
          <CheckCircle className="size-3.5 text-green-500" />
        ) : (
          <XCircle className="size-3.5 text-red-500" />
        );
    }
  };

  const getStatusText = () => {
    if (props.status === "loading") {
      return "Preparing action...";
    }

    if (props.status === "generating") {
      return ACTION_GENERATING_TEXT_MAP[props.actionType];
    }

    if (props.status === "done") {
      if (props.actionType === "shell") {
        return props.success ? "Command completed" : "Command failed";
      } else if (props.actionType === "apply-patch") {
        return props.success ? "Patch applied" : "Patch failed";
      } else if (props.actionType === "rg") {
        return props.success ? "Search completed" : "Search failed";
      } else if (props.actionType === "install_dependencies") {
        return props.success ? "Dependencies installed" : "Installation failed";
      } else if (props.actionType === "planner_notes") {
        return props.success ? "Notes saved" : "Failed to save notes";
      } else if (props.actionType === "get_url_content") {
        return props.success
          ? "URL content fetched"
          : "Failed to fetch URL content";
      } else if (props.actionType === "find_instances_of") {
        return props.success ? "Search completed" : "Search failed";
      }
    }

    return "";
  };

  // Determine if we should show the content toggle button
  const shouldShowToggle = () => {
    if (props.status !== "done") return false;

    if (
      props.actionType === "shell" ||
      props.actionType === "rg" ||
      props.actionType === "install_dependencies" ||
      props.actionType === "get_url_content"
    ) {
      return !!props.output;
    } else if (props.actionType === "apply-patch") {
      return !!props.diff;
    } else if (props.actionType === "planner_notes") {
      return !!(props.notes && props.notes.length > 0);
    }

    return false;
  };

  // Render the header icon based on action type
  const renderHeaderIcon = () => {
    const defaultIconStyling = "text-muted-foreground mr-2 size-3.5";
    if (props.status === "loading" || !("actionType" in props)) {
      // In loading state, we don't know the type yet, use a generic icon
      return <ToolIconWithTooltip toolNamePretty="Loading" icon={<Loader2 className={cn(defaultIconStyling)} />} />
    }

    if (props.actionType === "planner_notes") {
      return <ToolIconWithTooltip toolNamePretty="Planner Notes" icon={<FileText className={cn(defaultIconStyling)} />} />
    } else if (props.actionType === "install_dependencies") {
      return <ToolIconWithTooltip toolNamePretty="Install Dependencies" icon={<CloudDownload className={cn(defaultIconStyling)} />} />
    } else if (props.actionType === "apply-patch") {
      return <ToolIconWithTooltip toolNamePretty="Apply Patch" icon={<FileCode className={cn(defaultIconStyling)} />} />
    } else if (props.actionType === "rg") {
      return <ToolIconWithTooltip toolNamePretty="Ripgrep (rg)" icon={<Search className={cn(defaultIconStyling)} />} />
    } else if (props.actionType === "get_url_content") {
      return <ToolIconWithTooltip toolNamePretty="Get URL Contents" icon={<Globe className={cn(defaultIconStyling)} />} />
    } else if (props.actionType === "find_instances_of") {
      return <ToolIconWithTooltip toolNamePretty="Find Instances of" icon={<Hash className={cn(defaultIconStyling)} />} />
    } else {
      return <ToolIconWithTooltip toolNamePretty="Tool Call" icon={<Terminal className={cn(defaultIconStyling)} />} />
    }
  };

  // Render the header content based on action type
  const renderHeaderContent = () => {
    if (props.status === "loading" || !("actionType" in props)) {
      return (
        <span className="text-foreground/80 text-xs font-normal">
          Preparing action...
        </span>
      );
    }

    if (props.actionType === "planner_notes") {
      return (
        <div className="flex-1">
          <span className="text-foreground/80 text-xs font-normal">
            Planner Notes
          </span>
        </div>
      );
    }

    if (props.actionType === "get_url_content") {
      return (
        <div className="flex-1">
          <code className="text-foreground/80 text-xs font-normal">
            {props.url}
          </code>
        </div>
      );
    }
    
    if (props.actionType === "find_instances_of") {
      return (
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <code className="text-foreground/80 text-xs font-normal">
              {props.query}
            </code>
            <div className="w-[1px] h-4 bg-border dark:bg-white"></div>
            <MatchCaseIcon matchCase={!!props.case_sensitive} />
            <MatchWholeWordIcon matchWholeWord={!!props.match_word} />
          </div>
          {(props.include_files || props.exclude_files) && (
            <div className="text-muted-foreground mt-0.5 text-xs font-normal">
              {props.include_files && <span>Include: {props.include_files}</span>}
              {props.include_files && props.exclude_files && <span> | </span>}
              {props.exclude_files && <span>Exclude: {props.exclude_files}</span>}
            </div>
          )}
        </div>
      );
    }

    if (
      props.actionType === "shell" ||
      props.actionType === "install_dependencies"
    ) {
      let commandStr = "";
      if (props.command) {
        if (Array.isArray(props.command)) {
          commandStr = props.command.join(" ");
        } else if (
          typeof props.command === "string" &&
          (props.command as string).length > 0
        ) {
          try {
            commandStr = JSON.parse(props.command);
          } catch {
            commandStr = props.command;
          }
        }
      }
      return (
        <div className="flex-1">
          {props.workdir && (
            <div className="text-muted-foreground mb-0.5 text-xs font-normal">
              {props.workdir}
            </div>
          )}
          <code className="text-foreground/80 text-xs font-normal">
            {commandStr}
          </code>
        </div>
      );
    } else if (props.actionType === "rg") {
      let formattedRgCommand = "";
      try {
        formattedRgCommand =
          formatRgCommand({
            pattern: props.pattern,
            paths: props.paths,
            flags: props.flags,
          })?.join(" ") ?? "";
      } catch {
        // no-op
      }
      return (
        <div className="flex-1">
          <code className="text-foreground/80 text-xs font-normal">
            {formattedRgCommand}
          </code>
        </div>
      );
    } else {
      return (
        <code className="text-foreground/80 flex-1 text-xs font-normal">
          {props.file_path}
        </code>
      );
    }
  };

  // Render the content based on action type
  const renderContent = () => {
    if (props.status !== "done" || !("actionType" in props)) return null;

    if (!expanded) return null;

    if (
      (props.actionType === "shell" ||
        props.actionType === "rg" ||
        props.actionType === "find_instances_of" ||
        props.actionType === "install_dependencies") &&
      props.output
    ) {
      return (
        <div className="bg-muted text-foreground/90 overflow-x-auto p-2 dark:bg-gray-900">
          <pre className="text-xs font-normal whitespace-pre-wrap">
            {props.output}
          </pre>
          {props.errorCode !== undefined && !props.success && (
            <div className="mt-1 text-xs text-red-500 dark:text-red-400">
              Exit code: {props.errorCode}
            </div>
          )}
        </div>
      );
    } else if (props.actionType === "get_url_content" && props.output) {
      return (
        <div className="bg-muted text-foreground/90 overflow-x-auto p-2 dark:bg-gray-900">
          <pre className="text-xs font-normal whitespace-pre-wrap">
            {props.output}
          </pre>
        </div>
      );
    } else if (props.actionType === "apply-patch" && props.diff) {
      return (
        <div className="bg-muted overflow-x-auto p-2 dark:bg-gray-900">
          <pre
            className="text-foreground/90 text-xs font-normal whitespace-pre-wrap"
            dangerouslySetInnerHTML={{ __html: formatDiff(props.diff) }}
          />

          {!props.success && props.errorMessage && (
            <div className="mt-2 rounded border border-red-700/30 bg-red-100/30 p-2 text-xs text-red-600 dark:bg-red-900/30 dark:text-red-400">
              {props.errorMessage}
            </div>
          )}

          {!props.success && props.fixedDiff && (
            <div className="mt-2">
              <div className="text-muted-foreground mb-1 text-xs">
                Fixed diff:
              </div>
              <pre
                className="text-foreground/90 text-xs font-normal whitespace-pre-wrap"
                dangerouslySetInnerHTML={{
                  __html: formatDiff(props.fixedDiff),
                }}
              />
            </div>
          )}
        </div>
      );
    } else if (
      props.actionType === "planner_notes" &&
      props.notes &&
      props.notes.length > 0
    ) {
      return (
        <div className="bg-muted overflow-x-auto p-2 dark:bg-gray-900">
          <ul className="list-disc pl-5 text-xs font-normal">
            {props.notes.map((note, i) => (
              <li
                key={i}
                className="text-foreground/90 mb-1 whitespace-pre-wrap"
              >
                {note}
              </li>
            ))}
          </ul>
        </div>
      );
    }

    return null;
  };

  return (
    <div className="border-border mb-2 overflow-hidden rounded-md border last:mb-0">
      <div className="border-border flex items-center border-b bg-gray-50 p-2 dark:bg-gray-800">
        {renderHeaderIcon()}
        {renderHeaderContent()}
        <div className="flex items-center gap-2">
          <span className="text-muted-foreground text-xs font-normal">
            {getStatusText()}
          </span>
          {getStatusIcon()}
          {shouldShowToggle() && (
            <button
              onClick={() => setExpanded(!expanded)}
              className="text-muted-foreground hover:text-foreground"
            >
              {expanded ? (
                <ChevronUp className="size-3.5" />
              ) : (
                <ChevronDown className="size-3.5" />
              )}
            </button>
          )}
        </div>
      </div>

      {renderContent()}
    </div>
  );
}

export function ActionStep(props: ActionStepProps) {
  const [showReasoning, setShowReasoning] = useState(false);
  const [showSummary, setShowSummary] = useState(false);

  const reasoningText =
    "reasoningText" in props ? props.reasoningText : undefined;
  const summaryText = "summaryText" in props ? props.summaryText : undefined;

  const anyActionDone = props.actions.some(
    (action: ActionItemProps) => action.status === "done",
  );

  return (
    <div className="border-border overflow-hidden rounded-md border">
      <div className="border-b border-blue-300 bg-blue-100/50 p-2 dark:border-blue-800 dark:bg-blue-900/50">
        <button
          onClick={() => setShowReasoning(!showReasoning)}
          className="flex cursor-pointer items-center gap-1 text-xs font-normal text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300"
        >
          <MessageSquare className="h-3 w-3" />
          {showReasoning ? "Hide reasoning" : "Show reasoning"}
        </button>
        {showReasoning && (
          <p className="mt-1 text-xs font-normal text-blue-700 dark:text-blue-300">
            {reasoningText || "No reasoning provided."}
          </p>
        )}
      </div>

      <div className="p-2">
        {props.actions.map((action: ActionItemProps, index: number) => (
          <ActionItem
            key={index}
            {...action}
          />
        ))}
      </div>

      {summaryText && anyActionDone && (
        <div className="border-t border-green-300 bg-green-100/50 p-2 dark:border-green-800 dark:bg-green-900/50">
          <button
            onClick={() => setShowSummary(!showSummary)}
            className="flex cursor-pointer items-center gap-1 text-xs font-normal text-green-600 hover:text-green-700 dark:text-green-400 dark:hover:text-green-300"
          >
            <FileText className="h-3 w-3" />
            {showSummary ? "Hide summary" : "Show summary"}
          </button>
          {showSummary && (
            <p className="mt-1 text-xs font-normal text-green-700 dark:text-green-300">
              {summaryText}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function formatDiff(diff: string) {
  return diff
    .split("\n")
    .map((line) => {
      if (line.startsWith("+")) {
        return `<span class="text-green-600 dark:text-green-400">${line}</span>`;
      } else if (line.startsWith("-")) {
        return `<span class="text-red-600 dark:text-red-400">${line}</span>`;
      } else if (line.startsWith("@")) {
        return `<span class="text-blue-600 dark:text-blue-400">${line}</span>`;
      }
      return line;
    })
    .join("\n");
}
