import { Box, Inline } from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@langchain/gtm-platform-design-system/ui/select"

import { ChevronLeft, ChevronRight } from "@/components/glyphs"

const PAGE_SIZES = [10, 25, 50, 100] as const
const ROWS_PER_PAGE = "Rows per page"

/** The pagination strip under a table: page size leading, readout and pager trailing. */
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
    <Inline
      gap="md"
      align="center"
      justify="between"
      wrap
      className="border-t border-line px-4 py-2 text-label text-ink-subtle"
    >
      <Inline gap="sm" align="center">
        <Box render={<span />}>{ROWS_PER_PAGE}</Box>
        <Select
          disabled={disabled}
          value={String(pageSize)}
          onValueChange={(value) => {
            if (value !== null) onPageSizeChange(Number(value))
          }}
        >
          <SelectTrigger
            size="compact"
            aria-label={ROWS_PER_PAGE}
            className="w-16"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PAGE_SIZES.map((size) => (
              <SelectItem key={size} value={String(size)}>
                {size}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Inline>
      <Inline gap="md" align="center">
        <Box render={<span />} className="font-mono tabular-nums">
          {start.toLocaleString("en-US")}–{end.toLocaleString("en-US")} of{" "}
          {total.toLocaleString("en-US")}
        </Box>
        <Box render={<span />}>
          Page {page} of {pageCount}
        </Box>
        <Inline gap="xs" align="center">
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label="Previous"
            disabled={disabled || page === 1}
            onClick={() => onPageChange(page - 1)}
          >
            <Icon icon={ChevronLeft} size="sm" />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label="Next"
            disabled={disabled || page >= pageCount}
            onClick={() => onPageChange(page + 1)}
          >
            <Icon icon={ChevronRight} size="sm" />
          </Button>
        </Inline>
      </Inline>
    </Inline>
  )
}
