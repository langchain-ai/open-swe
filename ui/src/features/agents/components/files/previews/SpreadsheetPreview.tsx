import { useState } from "react"
import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"

import type { ViewerProps } from "@/features/agents/lib/filePreview"
import { usePreviewData } from "@/features/agents/lib/workspaceFiles"
import { DataTable } from "./TablePreview"
import { queryMessage } from "./PreviewMessage"
import type { SpreadsheetReply, SpreadsheetSheets } from "./spreadsheet.worker"

// The patched parser bounds memory, not time; a slow workbook parses in a worker stopped past this.
const PARSE_LIMIT_MS = 10_000

const TOO_LARGE = "This workbook is too large to preview."

async function readSheets(bytes: ArrayBuffer): Promise<SpreadsheetSheets> {
  const worker = new Worker(
    new URL("./spreadsheet.worker.ts", import.meta.url),
    { type: "module" }
  )
  let timer = 0
  try {
    return await new Promise<SpreadsheetSheets>((resolve, reject) => {
      timer = window.setTimeout(
        () => reject(new Error(TOO_LARGE)),
        PARSE_LIMIT_MS
      )
      worker.onerror = () => reject(new Error(TOO_LARGE))
      worker.onmessage = (event: MessageEvent<SpreadsheetReply>) =>
        "sheets" in event.data
          ? resolve(event.data.sheets)
          : reject(new Error(event.data.error ?? TOO_LARGE))
      worker.postMessage(bytes, [bytes])
    })
  } finally {
    window.clearTimeout(timer)
    worker.terminate()
  }
}

/** An Excel workbook, one sheet at a time. */
export default function SpreadsheetPreview(props: ViewerProps) {
  const sheets = usePreviewData(props.url, readSheets)
  const [selected, setSelected] = useState(0)

  const message = queryMessage(sheets)
  if (message || !sheets.data) return message
  const index = Math.min(selected, sheets.data.length - 1)
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {sheets.data.length > 1 ? (
        <div className="shrink-0 overflow-x-auto border-b border-default px-space-3 py-space-1">
          <GroupedTabs<number>
            size="xs"
            value={index}
            onChange={setSelected}
            options={sheets.data.map((sheet, position) => ({
              value: position,
              display: sheet.name,
            }))}
          />
        </div>
      ) : null}
      <DataTable rows={sheets.data[index]?.rows ?? []} />
    </div>
  )
}
