import type { ComponentProps } from "react"
import { useAgent } from "@copilotkit/react-core/v2"
import type { ReactToolCallRenderer } from "@copilotkit/react-core/v2"
import { DiffView } from "@/features/agents/components/chat/DiffView"
import { maybeDiffFromArgs } from "@/features/agents/lib/toolDisplay"

type ToolArgs = Record<string, unknown>
type ToolCallProps = ComponentProps<ReactToolCallRenderer<ToolArgs>["render"]>

function ToolCard({ name, args, status, result }: ToolCallProps) {
  const { agent } = useAgent()
  const label =
    status === "complete" ? "Complete" : agent.isRunning ? "Running" : "Stopped"
  return (
    <details className="my-2 rounded-xl border border-border p-3 text-sm">
      <summary className="cursor-pointer font-medium">
        {name}{" "}
        <span className="ml-2 text-xs text-muted-foreground">{label}</span>
      </summary>
      <pre className="mt-2 max-h-48 overflow-auto text-xs whitespace-pre-wrap">
        {JSON.stringify(args, null, 2)}
      </pre>
      {result !== undefined && (
        <pre className="mt-2 max-h-80 overflow-auto rounded-lg bg-muted p-3 text-xs whitespace-pre-wrap">
          {result}
        </pre>
      )}
    </details>
  )
}

function FileEditCard(props: ToolCallProps) {
  const diff = maybeDiffFromArgs(props.args)
  return (
    <>
      <ToolCard {...props} />
      {diff && <DiffView diffData={diff} />}
    </>
  )
}

function SubagentCard(props: ToolCallProps) {
  const description = props.args.description
  return (
    <ToolCard
      {...props}
      name={
        typeof description === "string"
          ? `Subagent: ${description}`
          : props.name
      }
    />
  )
}

export const toolRenderers: ReactToolCallRenderer<ToolArgs>[] = [
  { name: "edit_file", render: FileEditCard },
  { name: "write_file", render: FileEditCard },
  { name: "task", render: SubagentCard },
  { name: "*", render: ToolCard },
]
