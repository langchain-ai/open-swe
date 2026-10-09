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
    <div className="my-space-2 overflow-hidden rounded-lg border border-subtle bg-surface-level-2 text-xs">
      <div className="flex items-center justify-between gap-space-3 border-b border-subtle px-space-3 py-space-2 text-secondary">
        <span>
          {result.row_count.toLocaleString()} row
          {result.row_count === 1 ? "" : "s"}
        </span>
        {result.truncated && <span>Results truncated</span>}
      </div>
      <div className="max-h-[28rem] overflow-auto">
        <table className="min-w-max border-collapse text-left">
          <thead className="sticky top-0 z-10 bg-surface-level-3">
            <tr>
              {result.columns.map((column, index) => (
                <th
                  key={`${index}:${column}`}
                  scope="col"
                  className="border-b border-default px-space-3 py-space-2 font-medium whitespace-nowrap text-primary"
                >
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {result.rows.map((row, rowIndex) => (
              <tr
                key={rowIndex}
                className="border-t border-subtle first:border-t-0"
              >
                {row.map((value, columnIndex) => (
                  <td
                    key={columnIndex}
                    className={cn(
                      "max-w-96 px-space-3 py-space-2 align-top font-mono text-xxs leading-5 text-primary",
                      value === null && "text-secondary italic"
                    )}
                  >
                    <div className="max-h-24 overflow-auto break-words whitespace-pre-wrap">
                      {displayCell(value)}
                    </div>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {result.rows.length === 0 && (
          <div className="px-space-3 py-space-6 text-center text-secondary">
            No rows
          </div>
        )}
      </div>
    </div>
  )
}
