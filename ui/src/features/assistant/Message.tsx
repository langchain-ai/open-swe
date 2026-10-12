import {
  ActionBarPrimitive,
  MessagePrimitive,
  ReadonlyThreadProvider,
  ThreadPrimitive,
  groupPartByType,
  useAuiState,
  useExternalMessageConverter,
} from "@assistant-ui/react"
import type { ToolCallMessagePartProps, Toolkit } from "@assistant-ui/react"
import {
  convertLangChainBaseMessage,
  useLangChainStream,
  useLangChainSubagents,
} from "@assistant-ui/react-langchain"
import { useMessages } from "@langchain/react"
import type { AnyStream, SubagentDiscoverySnapshot } from "@langchain/react"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { CopyIcon } from "@phosphor-icons/react/dist/ssr/Copy"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { parseStructuredInput } from "@/features/agents/lib/structuredInputMessages"
import {
  maybeDiffFromArgs,
  outputIframeDisplay,
} from "@/features/agents/lib/toolDisplay"
import { OutputIframe } from "@/features/agents/components/chat/OutputIframe"
import { DiffView } from "@/features/agents/components/chat/DiffView"

function HumanText({ text }: { text: string }) {
  const parsed = parseStructuredInput(text)
  if (
    parsed.type === "entity" ||
    (parsed.type === "message" && parsed.senderKind === "system")
  ) {
    return (
      <details className="text-xs text-secondary">
        <summary className="cursor-pointer">Context</summary>
        <pre className="whitespace-pre-wrap">{text}</pre>
      </details>
    )
  }
  return <div className="whitespace-pre-wrap">{parsed.content}</div>
}

function outputText(value: unknown) {
  return typeof value === "string"
    ? value
    : value === undefined
      ? ""
      : JSON.stringify(value, null, 2)
}

export function ToolResult({
  toolName,
  args,
  result,
  isError,
  status,
}: ToolCallMessagePartProps) {
  return (
    <details
      className={`my-space-2 rounded-xl border p-space-3 text-sm ${isError ? "border-error text-error-secondary" : "border-default"}`}
      open={isError || undefined}
    >
      <summary className="cursor-pointer font-medium">
        {toolName}{" "}
        <span className="ml-space-2 text-xs text-secondary">
          {isError
            ? "Failed"
            : status.type === "running"
              ? "Running"
              : "Complete"}
        </span>
      </summary>
      <pre className="mt-space-2 max-h-48 overflow-auto text-xs whitespace-pre-wrap">
        {JSON.stringify(args, null, 2)}
      </pre>
      {result !== undefined && (
        <pre className="mt-space-2 max-h-80 overflow-auto rounded-lg bg-surface-level-2 p-space-3 text-xs whitespace-pre-wrap">
          {outputText(result)}
        </pre>
      )}
    </details>
  )
}

function SubagentTranscript({
  stream,
  target,
  running,
}: {
  stream: AnyStream
  target: SubagentDiscoverySnapshot
  running: boolean
}) {
  const messages = useMessages(stream, target)
  const converted = useExternalMessageConverter({
    messages,
    callback: convertLangChainBaseMessage,
    isRunning: running,
  })
  return (
    <ReadonlyThreadProvider messages={converted}>
      <ThreadPrimitive.Root>
        <ThreadPrimitive.Viewport>
          <ThreadPrimitive.Messages>
            {() => <AssistantMessage />}
          </ThreadPrimitive.Messages>
        </ThreadPrimitive.Viewport>
      </ThreadPrimitive.Root>
    </ReadonlyThreadProvider>
  )
}

function SubagentTool(props: ToolCallMessagePartProps) {
  const stream = useLangChainStream()
  const subagents = useLangChainSubagents()
  const target = subagents.get(props.toolCallId)
  return (
    <details className="my-space-2 rounded-xl border border-default p-space-3 text-sm">
      <summary className="cursor-pointer">
        Subagent:{" "}
        {typeof props.args.description === "string"
          ? props.args.description
          : props.toolName}
      </summary>
      {stream && target ? (
        <div className="mt-space-3 max-h-96 overflow-auto">
          <SubagentTranscript
            stream={stream}
            target={target}
            running={props.status.type === "running"}
          />
        </div>
      ) : (
        <ToolResult {...props} />
      )}
    </details>
  )
}

function OutputTool(props: ToolCallMessagePartProps) {
  const display = outputIframeDisplay(props.artifact)
  return display && !props.isError ? (
    <OutputIframe display={display} />
  ) : (
    <ToolResult {...props} />
  )
}

function FileEditTool(props: ToolCallMessagePartProps) {
  const diff = maybeDiffFromArgs(props.args)
  return (
    <>
      <ToolResult {...props} />
      {diff && !props.isError && <DiffView diffData={diff} />}
    </>
  )
}

export const toolkit = {
  task: { type: "backend", render: SubagentTool },
  output_iframe: { type: "backend", display: "standalone", render: OutputTool },
  edit_file: { type: "backend", render: FileEditTool },
  write_file: { type: "backend", render: FileEditTool },
} satisfies Toolkit

type ActivityGroup = "group-activity" | `group-${"read" | "edit"}:${string}`

const groupByType = groupPartByType<ActivityGroup>({
  reasoning: ["group-activity"],
  "tool-call": ["group-activity"],
  "standalone-tool-call": [],
})

const groupActivity: typeof groupByType = (part, context) => {
  if (part.type === "tool-call" && part.isError) return []
  const groups = groupByType(part, context)
  if (
    part.type !== "tool-call" ||
    groups.length === 0 ||
    (part.toolName !== "read_file" && part.toolName !== "edit_file")
  ) {
    return groups
  }
  const path = part.args.file_path ?? part.args.path ?? part.args.target_file
  if (typeof path !== "string" || !path.trim()) return groups
  const operation = part.toolName === "read_file" ? "read" : "edit"
  return [...groups, `group-${operation}:${path}`]
}

export function AssistantMessage() {
  const role = useAuiState((state) => state.message.role)
  const id = useAuiState((state) => state.message.id)
  return (
    <MessagePrimitive.Root
      data-message-id={id}
      className={`group/message my-space-5 min-w-0 ${role === "user" ? "ml-auto max-w-[85%] rounded-xl bg-surface-level-2 px-space-4 py-space-3" : "w-full leading-7"}`}
    >
      <MessagePrimitive.GroupedParts groupBy={groupActivity}>
        {({ part, children }) => {
          if ("indices" in part && part.type !== "group-activity") {
            if (part.indices.length < 2) return children
            const reading = part.type.startsWith("group-read:")
            const path = part.type.slice(part.type.indexOf(":") + 1)
            return (
              <details className="my-space-2 rounded-xl border border-default p-space-3 text-sm">
                <summary className="cursor-pointer font-medium break-all">
                  {reading ? "Read" : "Edit"} {path} · {part.indices.length}{" "}
                  calls
                  {part.status.type === "running" && (
                    <span className="ml-space-2 text-xs text-secondary">
                      Running
                    </span>
                  )}
                </summary>
                <div className="mt-space-3 space-y-space-3">{children}</div>
              </details>
            )
          }
          switch (part.type) {
            case "group-activity":
              return (
                <details className="my-space-3 rounded-xl border border-default p-space-3 text-sm text-secondary">
                  <summary className="cursor-pointer">
                    {part.status.type === "running"
                      ? "Working…"
                      : "Show activity"}
                  </summary>
                  <div className="mt-space-3 space-y-space-3">{children}</div>
                </details>
              )
            case "text":
              return role === "user" ? (
                <HumanText text={part.text} />
              ) : (
                <Markdown
                  content={part.text}
                  isLive={part.status.type === "running"}
                />
              )
            case "reasoning":
              return (
                <div
                  data-testid="reasoning"
                  className="border-l-2 border-default pl-space-3 whitespace-pre-wrap"
                >
                  {part.text}
                </div>
              )
            case "tool-call":
              return part.toolUI ?? <ToolResult {...part} />
            case "image":
              return (
                <img
                  src={part.image}
                  alt={part.filename ?? "Attached image"}
                  className="my-space-2 max-h-72 rounded-xl"
                />
              )
            case "file":
              return (
                <span className="text-sm">{part.filename ?? "Attachment"}</span>
              )
            case "indicator":
              return (
                <p role="status" className="text-sm text-secondary">
                  Working…
                </p>
              )
            default:
              return null
          }
        }}
      </MessagePrimitive.GroupedParts>
      {role === "assistant" && (
        <ActionBarPrimitive.Root className="mt-space-2">
          <ActionBarPrimitive.Copy asChild>
            <IconButton
              icon={CopyIcon}
              label="Copy message"
              color="secondary"
              variant="plain"
              className="opacity-0 group-hover/message:opacity-100 focus-visible:opacity-100"
            />
          </ActionBarPrimitive.Copy>
        </ActionBarPrimitive.Root>
      )}
    </MessagePrimitive.Root>
  )
}
