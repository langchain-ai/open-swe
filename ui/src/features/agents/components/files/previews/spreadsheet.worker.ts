import readXlsxFile from "read-excel-file/web-worker"
import type { CellValue } from "read-excel-file/web-worker"

export type SpreadsheetSheets = Array<{
  name: string
  rows: Array<Array<string>>
}>

/** Every sheet's cells as text, or why they couldn't be read; no `error` means too large. */
export type SpreadsheetReply =
  | { sheets: SpreadsheetSheets }
  | { error?: string }

// One shared string is one value here but is copied into the reply once per cell.
const MAX_TEXT_LENGTH = 50_000_000

function cellText(value: CellValue | null): string {
  if (value === null) return ""
  if (value instanceof Date) return value.toLocaleString()
  if (typeof value === "boolean") return value ? "TRUE" : "FALSE"
  return String(value)
}

self.onmessage = async (event: MessageEvent<ArrayBuffer>) => {
  let reply: SpreadsheetReply
  try {
    let length = 0
    const sheets = (await readXlsxFile(event.data)).map((sheet) => ({
      name: sheet.sheet,
      rows: sheet.data.map((row) =>
        row.map((value) => {
          const text = cellText(value)
          length += text.length
          return text
        })
      ),
    }))
    if (length > MAX_TEXT_LENGTH) throw new RangeError("Too much text.")
    reply = { sheets }
  } catch (error) {
    // The parser's patched row and cell limits throw a RangeError
    // (ui/patches/read-excel-file@9.3.14.patch), as do the text limit and V8 past its own.
    reply =
      error instanceof RangeError
        ? {}
        : {
            error:
              error instanceof Error
                ? error.message
                : "Could not read the workbook.",
          }
  }
  self.postMessage(reply)
}
