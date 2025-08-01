"use client";

import { JSX, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  Terminal,
  FileText,
  ChevronDown,
  ChevronUp,
  MessageSquare,
  Search,
  CheckCircle,
  XCircle,
  Loader2,
  Globe,
  Zap,
  FileCode,
  CloudDownload,
} from "lucide-react";
import { BasicMarkdownText } from "../thread/markdown-text";
import {
  createApplyPatchToolFields,
  createShellToolFields,
  createInstallDependenciesToolFields,
  createScratchpadFields,
  createGetURLContentToolFields,
  createGrepToolFields,
  createSearchDocumentForToolFields,
  createTextEditorToolFields,
} from "@open-swe/shared/open-swe/tools";
import { z } from "zod";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "../ui/tooltip";
import { cn } from "@/lib/utils";
import { ToolIconWithTooltip } from "./tool-icon-tooltip";

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
const scratchpadTool = createScratchpadFields("");
type ScratchpadToolArgs = z.infer<typeof scratchpadTool.schema>;
const getURLContentTool = createGetURLContentToolFields();
type GetURLContentToolArgs = z.infer<typeof getURLContentTool.schema>;
const grepTool = createGrepToolFields(dummyRepo);
type GrepToolArgs = z.infer<typeof grepTool.schema>;
const searchDocumentForTool = createSearchDocumentForToolFields();
type SearchDocumentForToolArgs = z.infer<typeof searchDocumentForTool.schema>;
const textEditorTool = createTextEditorToolFields(dummyRepo, {});
type TextEditorToolArgs = z.infer<typeof textEditorTool.schema>;

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

type InstallDependenciesActionProps = BaseActionProps &
  Partial<InstallDependenciesToolArgs> & {
    actionType: "install_dependencies";
    output?: string;
    errorCode?: number;
  };

type ScratchpadActionProps = BaseActionProps &
  Partial<ScratchpadToolArgs> & {
    actionType: "scratchpad";
  };

type GetURLContentActionProps = BaseActionProps &
  Partial<GetURLContentToolArgs> & {
    actionType: "get_url_content";
    output?: string;
  };

type SearchActionProps = BaseActionProps &
  Partial<GrepToolArgs> & {
    actionType: "grep";
    output?: string;
    errorCode?: number;
  };

type SearchDocumentForActionProps = BaseActionProps &
  Partial<SearchDocumentForToolArgs> & {
    actionType: "search_document_for";
    output?: string;
  };

type TextEditorActionProps = BaseActionProps &
  Partial<TextEditorToolArgs> & {
    actionType: "text_editor";
    output?: string;
  };

type McpActionProps = BaseActionProps & {
  actionType: "mcp";
  toolName: string;
  args: Record<string, any>;
  output?: string;
};

export type ActionItemProps =
  | (BaseActionProps & { status: "loading" })
  | ShellActionProps
  | PatchActionProps
  | InstallDependenciesActionProps
  | ScratchpadActionProps
  | GetURLContentActionProps
  | McpActionProps
  | SearchActionProps
  | SearchDocumentForActionProps
  | TextEditorActionProps;

export type ActionStepProps = {
  actions: ActionItemProps[];
  reasoningText?: string;
  summaryText?: string;
};

const ACTION_GENERATING_TEXT_MAP = {
  [shellTool.name]: "Executing...",
  [applyPatchTool.name]: "Applying patch...",
  [installDependenciesTool.name]: "Installing dependencies...",
  [scratchpadTool.name]: "Saving notes...",
  [getURLContentTool.name]: "Fetching URL content...",
  [searchDocumentForTool.name]: "Searching document...",
  [grepTool.name]: "Searching...",
  [textEditorTool.name]: "Editing file...",
};

function MatchCaseIcon({ matchCase }: { matchCase: boolean }) {
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger
          className={cn(
            "rounded-sm border border-gray-300 px-1 py-[2px] text-xs dark:border-gray-600",
            matchCase
              ? "border-blue-500 bg-blue-500/80 text-white"
              : "dark:bg-muted dark:text-muted-foreground bg-muted text-gray-800",
          )}
        >
          <p className="font-mono">Aa</p>
        </TooltipTrigger>
        <TooltipContent>Match case {matchCase ? "on" : "off"}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

const coerceStringToArray = (str: string | string[]) => {
  if (Array.isArray(str)) {
    return str;
  }
  try {
    return JSON.parse(str);
  } catch {
    return [str];
  }
};

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

  const getStatusText = (): string | JSX.Element => {
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
      } else if (props.actionType === "install_dependencies") {
        return props.success ? "Dependencies installed" : "Installation failed";
      } else if (props.actionType === "scratchpad") {
        return props.success
          ? "Scratchpad updated"
          : "Failed to update scratchpad";
      } else if (props.actionType === "get_url_content") {
        return props.success
          ? "URL content fetched"
          : "Failed to fetch URL content";
      } else if (props.actionType === "search_document_for") {
        return props.success
          ? "Document search completed"
          : "Document search failed";
      } else if (props.actionType === "grep") {
        return props.success ? "Search completed" : "Search failed";
      } else if (props.actionType === "text_editor") {
        const command = props.command || "unknown";
        return props.success ? (
          <span className="flex items-center gap-1">
            <p className="text-muted-foreground bg-muted rounded px-1 font-mono text-xs">
              {command}
            </p>{" "}
            command completed
          </span>
        ) : (
          <span className="flex items-center gap-1">
            <p className="text-muted-foreground bg-muted rounded px-1 font-mono text-xs">
              {command}
            </p>{" "}
            command failed
          </span>
        );
      } else if (props.actionType === "mcp") {
        return props.success
          ? `${props.toolName} completed`
          : `${props.toolName} failed`;
      }
    }

    return "";
  };

  const shouldShowToggle = () => {
    if (props.status !== "done") return false;

    if (
      props.actionType === "shell" ||
      props.actionType === "install_dependencies" ||
      props.actionType === "get_url_content" ||
      props.actionType === "search_document_for" ||
      props.actionType === "grep" ||
      props.actionType === "text_editor"
    ) {
      return !!props.output;
    } else if (props.actionType === "apply-patch") {
      return !!props.diff;
    } else if (props.actionType === "scratchpad") {
      return !!(props.scratchpad && props.scratchpad.length > 0);
    } else if (props.actionType === "mcp") {
      const hasArgs = props.args && Object.keys(props.args).length > 0;
      const hasOutput = !!props.output;
      return hasArgs || hasOutput;
    }

    return false;
  };

  // Render the header icon based on action type
  const renderHeaderIcon = () => {
    const defaultIconStyling = "text-muted-foreground mr-2 size-3.5";
    if (props.status === "loading" || !("actionType" in props)) {
      // In loading state, we don't know the type yet, use a generic icon
      return (
        <ToolIconWithTooltip
          toolNamePretty="Loading"
          icon={<Loader2 className={cn(defaultIconStyling)} />}
        />
      );
    }

    if (props.actionType === "scratchpad") {
      return (
        <ToolIconWithTooltip
          toolNamePretty="Scratchpad"
          icon={<FileText className={cn(defaultIconStyling)} />}
        />
      );
    } else if (props.actionType === "install_dependencies") {
      return (
        <ToolIconWithTooltip
          toolNamePretty="Install Dependencies"
          icon={<CloudDownload className={cn(defaultIconStyling)} />}
        />
      );
    } else if (props.actionType === "apply-patch") {
      return (
        <ToolIconWithTooltip
          toolNamePretty="Apply Patch"
          icon={<FileCode className={cn(defaultIconStyling)} />}
        />
      );
    } else if (props.actionType === "get_url_content") {
      return (
        <ToolIconWithTooltip
          toolNamePretty="Get URL Contents"
          icon={<Globe className={cn(defaultIconStyling)} />}
        />
      );
    } else if (props.actionType === "search_document_for") {
      return (
        <ToolIconWithTooltip
          toolNamePretty="Search Document"
          icon={<FileText className={cn(defaultIconStyling)} />}
        />
      );
    } else if (props.actionType === "mcp") {
      return (
        <ToolIconWithTooltip
          toolNamePretty={props.toolName}
          icon={<Zap className={cn(defaultIconStyling)} />}
        />
      );
    } else if (props.actionType === "grep") {
      return (
        <ToolIconWithTooltip
          toolNamePretty="Search"
          icon={<Search className={cn(defaultIconStyling)} />}
        />
      );
    } else if (props.actionType === "text_editor") {
      return (
        <ToolIconWithTooltip
          toolNamePretty="Text Editor"
          icon={<FileCode className={cn(defaultIconStyling)} />}
        />
      );
    } else {
      return (
        <ToolIconWithTooltip
          toolNamePretty="Tool Call"
          icon={<Terminal className={cn(defaultIconStyling)} />}
        />
      );
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

    if (props.actionType === "scratchpad") {
      return (
        <div className="flex items-center">
          <span className="text-foreground/80 text-xs font-normal">
            Scratchpad
          </span>
        </div>
      );
    }

    if (props.actionType === "get_url_content") {
      return (
        <div className="flex items-center">
          <code className="text-foreground/80 text-xs font-normal">
            {props.url}
          </code>
        </div>
      );
    }

    if (props.actionType === "grep") {
      const castProps = props as SearchActionProps;
      return (
        <div className="flex flex-col">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <code className="text-foreground/80 text-xs font-normal">
                {castProps.query}
              </code>
              <div className="bg-border h-4 w-[1px] dark:bg-white"></div>
              <MatchCaseIcon matchCase={!!castProps.case_sensitive} />
              {!castProps.match_string && (
                <span className="text-muted-foreground bg-muted/50 rounded px-1 text-xs font-normal">
                  regex
                </span>
              )}
            </div>
          </div>
          <div className="text-muted-foreground mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs font-normal">
            {castProps.include_files && (
              <span>Include: {castProps.include_files}</span>
            )}
            {castProps.exclude_files && (
              <span>Exclude: {castProps.exclude_files}</span>
            )}
            {castProps.context_lines !== undefined &&
              castProps.context_lines > 0 && (
                <span>Context: {castProps.context_lines} lines</span>
              )}
            {castProps.max_results !== undefined &&
              castProps.max_results > 0 && (
                <span>Max results: {castProps.max_results}</span>
              )}
            {castProps.file_types && castProps.file_types.length > 0 && (
              <span>
                File types:{" "}
                {coerceStringToArray(castProps.file_types).join(", ")}
              </span>
            )}
            {castProps.follow_symlinks && <span>Follow symlinks</span>}
          </div>
        </div>
      );
    }

    if (
      props.actionType === "shell" ||
      props.actionType === "install_dependencies"
    ) {
      const shellProps = props as
        | ShellActionProps
        | InstallDependenciesActionProps;
      let commandStr = "";
      if (shellProps.command) {
        if (Array.isArray(shellProps.command)) {
          commandStr = shellProps.command.join(" ");
        } else if (
          typeof shellProps.command === "string" &&
          (shellProps.command as string).length > 0
        ) {
          try {
            commandStr = JSON.parse(shellProps.command);
          } catch {
            commandStr = shellProps.command;
          }
        }
      }
      return (
        <div className="flex items-center">
          {shellProps.workdir && (
            <div className="text-muted-foreground mb-0.5 text-xs font-normal">
              {shellProps.workdir}
            </div>
          )}
          <code className="text-foreground/80 text-xs font-normal">
            {commandStr}
          </code>
        </div>
      );
    } else if (props.actionType === "search_document_for") {
      return (
        <div className="flex flex-col">
          <code className="text-foreground/80 text-xs font-normal">
            {props.query}
          </code>
          <div className="text-muted-foreground mt-1 text-xs font-normal">
            {props.url}
          </div>
        </div>
      );
    } else if (props.actionType === "text_editor") {
      const command = props.command || "unknown";
      const path = props.path || "";
      return (
        <div className="flex flex-col">
          <div className="flex items-center gap-2">
            <span className="text-muted-foreground bg-muted/50 rounded px-1 text-xs font-normal">
              {command}
            </span>
            <code className="text-foreground/80 text-xs font-normal">
              {path}
            </code>
          </div>
          {command === "view" && props.view_range && (
            <div className="text-muted-foreground mt-1 text-xs font-normal">
              Lines {props.view_range[0]}-
              {props.view_range[1] === -1 ? "end" : props.view_range[1]}
            </div>
          )}
          {command === "str_replace" && props.old_str && (
            <div className="text-muted-foreground mt-1 truncate text-xs font-normal">
              Replace: {props.old_str.substring(0, 50)}
              {props.old_str.length > 50 ? "..." : ""}
            </div>
          )}
          {command === "insert" && props.insert_line !== undefined && (
            <div className="text-muted-foreground mt-1 text-xs font-normal">
              Insert at line {props.insert_line}
            </div>
          )}
        </div>
      );
    } else if (props.actionType === "mcp") {
      return (
        <div className="flex items-center">
          <span className="text-foreground/80 text-xs font-normal">
            {props.toolName}
          </span>
        </div>
      );
    } else {
      return (
        <code className="text-foreground/80 flex items-center text-xs font-normal">
          {props.actionType === "apply-patch" && "file_path" in props
            ? props.file_path
            : ""}
        </code>
      );
    }
  };

  // Render the content based on action type
  const renderContent = () => {
    if (!("actionType" in props)) return null;
    if (props.status !== "done") return null;
    if (!expanded) return null;

    if (
      (props.actionType === "shell" ||
        props.actionType === "grep" ||
        props.actionType === "search_document_for" ||
        props.actionType === "install_dependencies" ||
        props.actionType === "text_editor") &&
      props.output
    ) {
      return (
        <div className="bg-muted text-foreground/90 overflow-x-auto p-2">
          <pre className="text-xs font-normal whitespace-pre-wrap">
            {props.output}
          </pre>
          {(props.actionType === "shell" || props.actionType === "grep") &&
            "errorCode" in props &&
            props.errorCode !== undefined &&
            !props.success && (
              <div className="mt-1 text-xs text-red-500 dark:text-red-400">
                Exit code: {props.errorCode}
              </div>
            )}
        </div>
      );
    } else if (props.actionType === "mcp") {
      const hasArgs = props.args && Object.keys(props.args).length > 0;
      const hasOutput = !!props.output;

      if (!hasArgs && !hasOutput) {
        return null;
      }

      return (
        <div className="bg-muted overflow-x-auto p-2">
          {hasArgs && (
            <div className="mb-3">
              <div className="text-muted-foreground mb-2 text-xs font-medium tracking-wide uppercase">
                Arguments
              </div>
              <div className="bg-muted-foreground/5 rounded border p-3">
                {Object.entries(props.args).map(([key, value]) => (
                  <div
                    key={key}
                    className="mb-2 last:mb-0"
                  >
                    <div className="text-foreground mb-1 text-xs font-medium">
                      {key}
                    </div>
                    <div className="text-foreground/80 text-xs">
                      {typeof value === "object" ? (
                        <code className="bg-muted rounded px-2 py-1 font-mono text-xs break-all">
                          {JSON.stringify(value, null, 2)}
                        </code>
                      ) : (
                        <code className="bg-muted rounded px-2 py-1 font-mono text-xs break-all">
                          {String(value)}
                        </code>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {hasOutput && (
            <>
              <div className="text-muted-foreground mb-2 text-xs font-medium tracking-wide uppercase">
                Output
              </div>
              <pre className="text-foreground/90 text-xs font-normal whitespace-pre-wrap">
                {props.output}
              </pre>
            </>
          )}
        </div>
      );
    } else if (props.actionType === "get_url_content" && props.output) {
      return (
        <div className="bg-muted text-foreground/90 overflow-x-auto p-2">
          <pre className="text-xs font-normal whitespace-pre-wrap">
            {props.output}
          </pre>
        </div>
      );
    } else if (props.actionType === "apply-patch" && props.diff) {
      return (
        <div className="bg-muted overflow-x-auto p-2">
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
      props.actionType === "scratchpad" &&
      props.scratchpad &&
      props.scratchpad.length > 0
    ) {
      return (
        <div className="bg-muted overflow-x-auto p-2">
          <ul className="list-disc pl-5 text-xs font-normal">
            {props.scratchpad.map((note, i) => (
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
      <div className="border-border bg-muted/50 flex w-full items-center border-b p-2">
        {renderHeaderIcon()}
        {renderHeaderContent()}
        <div className="ml-auto flex items-center gap-2">
          <span className="text-muted-foreground text-xs font-normal">
            {getStatusText()}
          </span>
          {getStatusIcon()}
          {shouldShowToggle() && (
            <button
              onClick={() => setExpanded(!expanded)}
              className="text-muted-foreground hover:text-foreground cursor-pointer"
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

      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: "easeInOut" }}
            className="overflow-hidden"
          >
            {renderContent()}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export function ActionStep(props: ActionStepProps) {
  const reasoningText =
    "reasoningText" in props ? props.reasoningText : undefined;
  const summaryText = "summaryText" in props ? props.summaryText : undefined;

  const anyActionDone = props.actions.some(
    (action: ActionItemProps) => action.status === "done",
  );

  const [showReasoning, setShowReasoning] = useState(!!reasoningText);
  const [showSummary, setShowSummary] = useState(!!summaryText);

  return (
    <div className="border-border bg-muted overflow-hidden rounded-md border">
      <div className="bg-muted dark:bg-muted border-b border-l-4 border-gray-200 border-l-gray-500 p-2 dark:border-gray-600 dark:border-l-gray-400">
        <button
          onClick={() => setShowReasoning(!showReasoning)}
          className="flex cursor-pointer items-center gap-1.5 text-xs font-medium text-gray-700 transition-colors duration-200 hover:text-gray-800 dark:text-gray-300 dark:hover:text-gray-200"
        >
          <ChevronDown
            className={cn(
              "h-3 w-3 transition-transform duration-200",
              showReasoning && "rotate-180",
            )}
          />
          <span>{showReasoning ? "Hide reasoning" : "Show reasoning"}</span>
        </button>
        <AnimatePresence>
          {showReasoning && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.2, ease: "easeInOut" }}
              className="overflow-hidden"
            >
              <div className="mt-3 rounded-md bg-gray-50/50 px-3 py-2 dark:bg-gray-800/30">
                <BasicMarkdownText className="text-xs leading-relaxed text-gray-700 dark:text-gray-300">
                  {reasoningText || "No reasoning provided."}
                </BasicMarkdownText>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
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
        <div className="bg-muted dark:bg-muted border-t border-gray-300 p-2 dark:border-gray-600">
          <button
            onClick={() => setShowSummary(!showSummary)}
            className="flex cursor-pointer items-center gap-1.5 text-xs font-medium text-gray-600 transition-colors duration-200 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300"
          >
            <ChevronDown
              className={cn(
                "h-3 w-3 transition-transform duration-200",
                showSummary && "rotate-180",
              )}
            />
            <span>{showSummary ? "Hide summary" : "Show summary"}</span>
          </button>
          <AnimatePresence>
            {showSummary && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.2, ease: "easeInOut" }}
                className="overflow-hidden"
              >
                <div className="mt-3 rounded-md bg-gray-50/50 px-3 py-2 dark:bg-gray-800/30">
                  <BasicMarkdownText className="text-xs leading-relaxed text-gray-700 dark:text-gray-300">
                    {summaryText}
                  </BasicMarkdownText>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
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
