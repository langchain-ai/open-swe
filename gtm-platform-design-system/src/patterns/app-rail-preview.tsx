"use client";

/*
 * Rules for AppRailPreview.
 *
 * The collapsed product rail keeps destination recognition in the icon and
 * restores context in one right-side HoverCard. The preview is a bounded
 * summary of an existing server document, never a second product page.
 */

import { Box, Inline, Stack } from "../ui/box";
import { ArrowRight, type Glyph } from "../ui/glyphs";
import { Icon } from "../ui/icon";
import { IconWell } from "../ui/icon-well";
import { Skeleton } from "../ui/skeleton";

const APP_RAIL_PREVIEW_RULES: readonly string[] = [
  "Collapsed destination icons open one contextual HoverCard to the right after hover or focus intent. Expanded rows already carry their labels and do not duplicate the card.",
  "Every destination in the expanded tree, including nested children, becomes its own collapsed icon. One bounded server briefing supplies every preview; opening another icon reuses that document.",
  "A preview shows the destination identity, at most three recent rows, and one Open destination footer. It is orientation, not a miniature page or collection browser.",
  "Rows use only the browser-safe fields the owning endpoint already projects. Never render raw provider payloads, tool data, credentials, or HTML in rail chrome.",
];

const PREVIEW_ITEM_LIMIT = 3;
const LOADING_ROWS = ["first", "second", "third"] as const;

interface AppRailPreviewItem {
  readonly id: string;
  readonly title: string;
  readonly detail?: string;
  readonly href: string;
  readonly icon: Glyph;
}

interface AppRailPreviewProps {
  readonly title: string;
  readonly description: string;
  readonly destination: string;
  readonly icon: Glyph;
  readonly items: readonly AppRailPreviewItem[];
  readonly emptyMessage: string;
  readonly loading?: boolean;
  readonly onNavigate: (href: string) => void;
}

function AppRailPreviewLoading() {
  return (
    <Stack data-slot="app-rail-preview-loading" gap="xs" aria-label="Loading preview">
      {LOADING_ROWS.map((row) => (
        <Inline key={row} gap="sm" align="center" className="h-row-record">
          <Skeleton className="size-6 shrink-0 rounded-compact" />
          <Stack gap="xs" className="min-w-0 flex-1">
            <Skeleton className="h-3 w-40" />
            <Skeleton className="h-3 w-28" />
          </Stack>
        </Inline>
      ))}
    </Stack>
  );
}

function AppRailPreview({
  description,
  destination,
  emptyMessage,
  icon,
  items,
  loading = false,
  onNavigate,
  title,
}: AppRailPreviewProps) {
  const visibleItems = items.slice(0, PREVIEW_ITEM_LIMIT);

  return (
    <Stack data-slot="app-rail-preview" gap="md">
      <Inline gap="sm" align="start">
        <IconWell>
          <Icon icon={icon} size="sm" />
        </IconWell>
        <Stack gap="xs" className="min-w-0 flex-1">
          <Box render={<h2 />} className="text-label font-semibold text-ink">
            {title}
          </Box>
          <Box render={<p />} className="text-meta text-ink-subtle">
            {description}
          </Box>
        </Stack>
      </Inline>

      {loading ? (
        <AppRailPreviewLoading />
      ) : visibleItems.length === 0 ? (
        <Box
          data-slot="app-rail-preview-empty"
          padding="sm"
          className="rounded-compact bg-muted text-meta text-ink-subtle"
        >
          {emptyMessage}
        </Box>
      ) : (
        <Stack render={<ul />} gap="xs">
          {visibleItems.map((item) => (
            <Box render={<li />} key={item.id}>
              <Inline
                data-testid="app-rail-preview-item"
                render={
                  <button
                    type="button"
                    aria-label={
                      item.detail === undefined
                        ? item.title
                        : `${item.title}: ${item.detail}`
                    }
                    onClick={() => onNavigate(item.href)}
                  />
                }
                gap="sm"
                align="center"
                className="min-h-row-record w-full rounded-compact px-2 text-left outline-none transition-colors duration-fast ease-out-quint hover:bg-hover focus-visible:ring-2 focus-visible:ring-primary motion-reduce:transition-none"
              >
                <IconWell>
                  <Icon icon={item.icon} size="sm" />
                </IconWell>
                <Stack gap="none" className="min-w-0 flex-1">
                  <Box
                    render={<span />}
                    className="truncate text-label font-medium text-ink"
                  >
                    {item.title}
                  </Box>
                  {item.detail === undefined ? null : (
                    <Box
                      render={<span />}
                      className="truncate text-meta text-ink-subtle"
                    >
                      {item.detail}
                    </Box>
                  )}
                </Stack>
              </Inline>
            </Box>
          ))}
        </Stack>
      )}

      <Inline
        render={
          <button
            type="button"
            aria-label={`Open ${title}`}
            onClick={() => onNavigate(destination)}
          />
        }
        gap="xs"
        align="center"
        justify="between"
        className="h-control-sm w-full rounded-compact px-2 text-label font-medium text-ink outline-none transition-colors duration-fast ease-out-quint hover:bg-hover focus-visible:ring-2 focus-visible:ring-primary motion-reduce:transition-none"
      >
        <Box render={<span />}>Open {title}</Box>
        <Icon icon={ArrowRight} size="sm" className="text-ink-subtle" />
      </Inline>
    </Stack>
  );
}

export { AppRailPreview, APP_RAIL_PREVIEW_RULES, PREVIEW_ITEM_LIMIT };
export type { AppRailPreviewItem, AppRailPreviewProps };
