import { useState } from "react"
import type { CSSProperties } from "react"
import Papa from "papaparse"
import { useVirtualizer } from "@tanstack/react-virtual"

import type { ViewerProps } from "@/features/agents/lib/filePreview"
import { usePreviewData } from "@/features/agents/lib/workspaceFiles"
import { PreviewMessage, queryMessage } from "./PreviewMessage"

const ROW_HEIGHT = 28
// Columns render whole on every visible row; a one-line file can name a million.
const MAX_COLUMNS = 500

function parseTable(bytes: ArrayBuffer, path: string): Array<Array<string>> {
  const text = new TextDecoder().decode(bytes)
  const { data } = Papa.parse<Array<string>>(text, {
    delimiter: path.toLowerCase().endsWith(".tsv") ? "\t" : "",
    skipEmptyLines: "greedy",
  })
  return data
}

const CELL =
  "truncate border-r border-b border-subtle px-space-2 leading-7 whitespace-pre"

function TableRows(props: {
  body: Array<Array<string>>
  columns: number
  template: CSSProperties
  scroller: HTMLDivElement | null
}) {
  // The virtualizer's functions cannot be memoized, so the React Compiler
  // skips this component; it is kept small for exactly that reason.
  // oxlint-disable-next-line react/incompatible-library
  const virtualizer = useVirtualizer({
    count: props.body.length,
    getScrollElement: () => props.scroller,
    estimateSize: () => ROW_HEIGHT,
    overscan: 12,
  })
  return (
    <div className="relative" style={{ height: virtualizer.getTotalSize() }}>
      {virtualizer.getVirtualItems().map((item) => (
        <div
          key={item.key}
          role="row"
          className="absolute inset-x-0 grid text-primary"
          style={{
            ...props.template,
            height: ROW_HEIGHT,
            transform: `translateY(${item.start}px)`,
          }}
        >
          <span className={`${CELL} text-right text-tertiary`}>
            {item.index + 1}
          </span>
          {Array.from({ length: props.columns }, (_, column) => (
            <span key={column} role="cell" className={CELL}>
              {props.body[item.index]?.[column] ?? ""}
            </span>
          ))}
        </div>
      ))}
    </div>
  )
}

/** Rows of cells as a table whose first row is the header; rows render as they scroll in. */
export function DataTable(props: { rows: Array<Array<string>> }) {
  // State, not a ref: the rows render once the virtualizer has the element to measure.
  const [scroller, setScroller] = useState<HTMLDivElement | null>(null)
  const [header = [], ...body] = props.rows
  // A loop, not Math.max(...rows): spreading 100k+ rows overflows the stack.
  const width = props.rows.reduce((most, row) => Math.max(most, row.length), 0)
  const columns = Math.min(width, MAX_COLUMNS)
  if (!props.rows.length) return <PreviewMessage>Empty.</PreviewMessage>

  const template = {
    gridTemplateColumns: `3.5rem repeat(${columns}, minmax(8rem, max-content))`,
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 border-b border-default px-space-3 py-space-1 text-xxs text-secondary">
        {body.length.toLocaleString()} rows · {width.toLocaleString()} columns
        {width > columns ? ` · showing the first ${columns}` : ""}
      </div>
      <div
        ref={setScroller}
        className="min-h-0 flex-1 overflow-auto font-mono text-xs"
      >
        <div className="w-max min-w-full">
          <div
            role="row"
            className="z-sticky sticky top-0 grid bg-surface-level-2 font-medium text-primary"
            style={template}
          >
            <span className={CELL} />
            {Array.from({ length: columns }, (_, column) => (
              <span key={column} role="columnheader" className={CELL}>
                {header[column] ?? ""}
              </span>
            ))}
          </div>
          <TableRows
            body={body}
            columns={columns}
            template={template}
            scroller={scroller}
          />
        </div>
      </div>
    </div>
  )
}

/** A CSV or TSV file. */
export default function TablePreview(props: ViewerProps) {
  const rows = usePreviewData(props.url, (bytes) =>
    parseTable(bytes, props.path)
  )
  return queryMessage(rows) ?? <DataTable rows={rows.data ?? []} />
}
