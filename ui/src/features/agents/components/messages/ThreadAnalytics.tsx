import type {
  AgentRunUsage,
  Message,
  ToolExecutionChunk,
} from "@/features/agents/lib/types"
import { cn } from "@/lib/utils"

type ChartPoint = {
  turn: number
  value: number | null
}

type ChartProps = {
  title: string
  points: Array<ChartPoint>
  format: (value: number) => string
  color: string
}

function chartSegments(points: Array<ChartPoint>): Array<string> {
  const max = Math.max(...points.map((point) => point.value ?? 0), 1)
  const segments: Array<Array<string>> = []
  let segment: Array<string> = []
  points.forEach((point, index) => {
    if (point.value === null) {
      if (segment.length) segments.push(segment)
      segment = []
      return
    }
    const x =
      points.length === 1 ? 160 : 20 + (index / (points.length - 1)) * 280
    const y = 88 - (point.value / max) * 72
    segment.push(`${x},${y}`)
  })
  if (segment.length) segments.push(segment)
  return segments.map((coordinates) => coordinates.join(" "))
}

function TurnChart({ title, points, format, color }: ChartProps) {
  const known = points.filter(
    (point): point is ChartPoint & { value: number } => point.value !== null
  )
  const max = Math.max(...known.map((point) => point.value), 1)
  const total = known.reduce((sum, point) => sum + point.value, 0)
  return (
    <div className="min-w-0 rounded-lg border border-border/70 bg-background/70 p-3">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-xs font-medium text-foreground">{title}</h3>
        <span className="text-[11px] text-muted-foreground tabular-nums">
          {known.length ? `${format(total)} total` : "Unavailable"}
        </span>
      </div>
      <svg
        viewBox="0 0 320 112"
        className="mt-2 h-28 w-full overflow-visible"
        role="img"
        aria-label={`${title} by turn`}
      >
        <line x1="20" y1="88" x2="300" y2="88" className="stroke-border" />
        {chartSegments(points).map((segment, index) => (
          <polyline
            key={`${title}-${index}`}
            points={segment}
            fill="none"
            stroke={color}
            strokeWidth="2"
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        ))}
        {points.map((point, index) => {
          const x =
            points.length === 1 ? 160 : 20 + (index / (points.length - 1)) * 280
          const y = point.value === null ? 88 : 88 - (point.value / max) * 72
          const label =
            point.value === null ? "Unavailable" : format(point.value)
          return (
            <g key={point.turn}>
              <circle
                cx={x}
                cy={y}
                r="3.5"
                fill={
                  point.value === null ? "var(--color-muted-foreground)" : color
                }
              >
                <title>{`Turn ${point.turn}: ${label}`}</title>
              </circle>
              <text
                x={x}
                y="105"
                textAnchor="middle"
                className="fill-muted-foreground text-[9px]"
              >
                {point.turn}
              </text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}

function formatTokens(value: number): string {
  return new Intl.NumberFormat("en-US", { notation: "compact" }).format(value)
}

function formatCost(value: number): string {
  return value < 0.01 && value > 0
    ? `$${value.toFixed(4)}`
    : `$${value.toFixed(2)}`
}

type ToolTurn = {
  turn: number
  tools: Array<ToolExecutionChunk>
}

function agentTurns(messages: Array<Message>): Array<Message> {
  return messages.filter((message) => message.author === "agent")
}

function chartPoints(
  messages: Array<Message>,
  runs: Array<AgentRunUsage>,
  value: (run: AgentRunUsage) => number | null
): Array<ChartPoint> {
  const turns = agentTurns(messages)
  const runsByTurn = new Map(
    runs.filter((run) => run.turn_key).map((run) => [run.turn_key, run])
  )
  const legacyOrderMatches =
    runs.length === turns.length && runs.every((run) => !run.turn_key)
  return turns.map((message, index) => {
    const run = message.turnKey ? runsByTurn.get(message.turnKey) : undefined
    const fallback = legacyOrderMatches ? runs[index] : undefined
    return {
      turn: index + 1,
      value: run ? value(run) : fallback ? value(fallback) : null,
    }
  })
}

function toolTurns(messages: Array<Message>): Array<ToolTurn> {
  return agentTurns(messages).flatMap((message, index) => {
    const tools = message.chunks.filter(
      (chunk): chunk is ToolExecutionChunk => chunk.kind === "tool-execution"
    )
    return tools.length ? [{ turn: index + 1, tools }] : []
  })
}

function ToolSequence({ turns }: { turns: Array<ToolTurn> }) {
  const tools = turns.flatMap(({ turn, tools: turnTools }) =>
    turnTools.map((tool) => ({ turn, tool }))
  )
  return (
    <div className="rounded-lg border border-border/70 bg-background/70 p-3">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-xs font-medium text-foreground">
          Tool call sequence
        </h3>
        <span className="text-[11px] text-muted-foreground">
          {tools.length} top-level call{tools.length === 1 ? "" : "s"}
        </span>
      </div>
      <div
        className="mt-3 overflow-x-auto pb-1"
        data-testid="tool-call-sequence"
      >
        <div className="flex w-max min-w-full items-center gap-1.5">
          {tools.map(({ turn, tool }, index) => {
            const startsTurn = index === 0 || tools[index - 1]?.turn !== turn
            return (
              <div key={tool.toolCallId} className="flex items-center gap-1.5">
                {index > 0 && (
                  <span className="text-muted-foreground/50">→</span>
                )}
                {startsTurn && (
                  <span className="rounded bg-muted px-1.5 py-1 text-[10px] font-medium text-muted-foreground">
                    Turn {turn}
                  </span>
                )}
                <div
                  className={cn(
                    "max-w-48 rounded-md border px-2 py-1.5",
                    tool.status === "error"
                      ? "border-destructive/40 bg-destructive/5"
                      : tool.status === "in_progress" ||
                          tool.status === "pending"
                        ? "border-amber-500/40 bg-amber-500/5"
                        : "border-border bg-card"
                  )}
                  title={tool.title}
                >
                  <div className="truncate text-[11px] font-medium text-foreground">
                    {tool.title}
                  </div>
                  <div className="text-[9px] tracking-wide text-muted-foreground uppercase">
                    {tool.toolKind} · {tool.status.replace("_", " ")}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

export function ThreadAnalytics({
  messages,
  runs,
}: {
  messages: Array<Message>
  runs: Array<AgentRunUsage>
}) {
  const turns = toolTurns(messages)
  const tokenPoints = chartPoints(messages, runs, (run) => run.total_tokens)
  const costPoints = chartPoints(messages, runs, (run) => run.cost_usd)
  if (!runs.length && !turns.length) return null

  return (
    <section className="mb-6 space-y-3" aria-label="Thread analytics">
      {runs.length > 0 && tokenPoints.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2">
          <TurnChart
            title="Tokens"
            points={tokenPoints}
            format={formatTokens}
            color="var(--color-chart-1, #6366f1)"
          />
          <TurnChart
            title="Cost"
            points={costPoints}
            format={formatCost}
            color="var(--color-chart-2, #10b981)"
          />
        </div>
      )}
      {turns.length > 0 && <ToolSequence turns={turns} />}
    </section>
  )
}
