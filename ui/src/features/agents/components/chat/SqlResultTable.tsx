import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import { Box, Inline } from "@langchain/gtm-platform-design-system/ui/box"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@langchain/gtm-platform-design-system/ui/table"
import { cn } from "@/lib/utils"

export interface SqlResult {
  ok: true
  columns: Array<string>
  rows: Array<Array<unknown>>
  row_count: number
  truncated: boolean
}

export function parseSqlResult(output: string | undefined): SqlResult | null {
  if (!output) return null
  try {
    const value: unknown = JSON.parse(output)
    if (!value || typeof value !== "object" || Array.isArray(value)) return null
    const result = value as Record<string, unknown>
    if (result.ok !== true || !Array.isArray(result.columns)) return null
    const columns = result.columns
    if (
      !columns.every((column) => typeof column === "string") ||
      !Array.isArray(result.rows) ||
      !result.rows.every(
        (row) => Array.isArray(row) && row.length === columns.length
      ) ||
      typeof result.row_count !== "number" ||
      typeof result.truncated !== "boolean"
    ) {
      return null
    }
    return result as unknown as SqlResult
  } catch {
    return null
  }
}

function displayCell(value: unknown): string {
  if (value === null) return "NULL"
  if (typeof value === "string") return value
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value)
  }
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

export function SqlResultTable({ output }: { output: string | undefined }) {
  const result = parseSqlResult(output)
  if (!result) return null

  return (
    <Box
      data-slot="sql-result"
      bg="panel"
      border="line"
      radius="compact"
      className="overflow-hidden"
    >
      <Inline
        align="center"
        justify="between"
        gap="md"
        className="min-h-control border-b border-line px-3 py-1.5 text-meta text-ink-subtle"
      >
        <span>
          {result.row_count.toLocaleString()} row
          {result.row_count === 1 ? "" : "s"}
        </span>
        {result.truncated && (
          <Badge tier="quiet" tone="attention">
            Results truncated
          </Badge>
        )}
      </Inline>
      <Box className="max-h-112 overflow-auto">
        <Table container={false} className="min-w-max text-label">
          <TableHeader className="sticky top-0 z-10 bg-muted">
            <TableRow>
              {result.columns.map((column, index) => (
                <TableHead
                  key={`${index}:${column}`}
                  scope="col"
                  className="px-3 text-ink"
                >
                  {column}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {result.rows.map((row, rowIndex) => (
              <TableRow key={rowIndex}>
                {row.map((value, columnIndex) => (
                  <TableCell
                    key={columnIndex}
                    className={cn(
                      "max-w-96 px-3 py-2 align-top font-mono text-meta whitespace-normal",
                      value === null && "text-ink-subtle italic"
                    )}
                  >
                    <div className="max-h-24 overflow-auto break-words whitespace-pre-wrap">
                      {displayCell(value)}
                    </div>
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {result.rows.length === 0 && (
          <Box
            render={<p />}
            padding="lg"
            className="text-center text-label text-ink-subtle"
          >
            No rows
          </Box>
        )}
      </Box>
    </Box>
  )
}
