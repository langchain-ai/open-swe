import { Button } from "@langchain/macaw-components/Button"
import { Select } from "@langchain/macaw-components/Select"

const PAGE_SIZE_OPTIONS = [10, 25, 50, 100].map((size) => ({
  value: String(size),
  label: String(size),
}))

export function TablePagination({
  page,
  pageSize,
  total,
  disabled = false,
  onPageChange,
  onPageSizeChange,
}: {
  page: number
  pageSize: number
  total: number
  disabled?: boolean
  onPageChange: (page: number) => void
  onPageSizeChange: (pageSize: number) => void
}) {
  if (total <= 10) return null

  const pageCount = Math.max(1, Math.ceil(total / pageSize))
  const start = total ? (page - 1) * pageSize + 1 : 0
  const end = Math.min(page * pageSize, total)

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-default px-4 py-3 text-xs text-secondary">
      <span>
        {start.toLocaleString("en-US")}–{end.toLocaleString("en-US")} of{" "}
        {total.toLocaleString("en-US")}
      </span>
      <div className="flex items-center gap-space-2">
        <span>Rows per page</span>
        <Select
          aria-label="Rows per page"
          disabled={disabled}
          hideSearch
          options={PAGE_SIZE_OPTIONS}
          size="sm"
          triggerClassName="w-20"
          value={String(pageSize)}
          onChange={(value) => {
            if (value) onPageSizeChange(Number(value))
          }}
        />
        <Button
          color="secondary"
          variant="outlined"
          disabled={disabled || page === 1}
          onClick={() => onPageChange(page - 1)}
        >
          Previous
        </Button>
        <span>
          Page {page} of {pageCount}
        </span>
        <Button
          color="secondary"
          variant="outlined"
          disabled={disabled || page >= pageCount}
          onClick={() => onPageChange(page + 1)}
        >
          Next
        </Button>
      </div>
    </div>
  )
}
