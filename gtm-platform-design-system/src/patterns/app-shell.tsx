"use client";

/*
 * Rules for AppShell.
 *
 * The rules themselves are `APP_SHELL_RULES` below, not this comment.
 * `/design` renders that array verbatim beside the live pattern, so the shell a
 * builder reads in the gallery and the shell this file mounts are the same
 * strings.
 *
 * This is the template tier, one level above PageFrame: PageFrame decides what
 * a page looks like inside its content region, AppShell decides whether that
 * region has chrome above it at all. The decision is CORE 06 section 03
 * (Paper file 01KYQDVRVBZ6KYPDP179BS2M3G, node QJ5-0) and its per-page
 * acceptance table on CORE 07 (QKS-0).
 *
 * What the code enforces: `mode` is the only way to reach a band, so a flat
 * page cannot grow a global header and a focus page cannot keep the navigation
 * rail; and the band region is a fixed 44px slot, so the content edge sits at
 * the same y whatever the band is filled with. What the rules state: which
 * mode a page is owed, and where each thing the old permanent header used to
 * carry went instead.
 *
 * The second axis is width, and it works the same way: `contentWidth` is the
 * only way to reach a measure, so no call site can widen a route by adding a
 * class. The four values, their numbers, and every source behind them are
 * `docs/plan/gtm-agent-product/width-padding-evidence.md` section 5.3, as
 * amended by Amal on 2026-08-05 (centred is the default everywhere).
 *
 * A third axis is chrome (`bleed` | `inset`). Inset is the product default:
 * desk mat, flush shell work column. Bleed remains a gallery escape hatch.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";

import { Box, Inline, Stack } from "../ui/box";
import { ScrollArea } from "../ui/scroll-area";
import { cn } from "../ui/cn";
import {
  RAIL_WIDTH_COLLAPSED,
  RAIL_WIDTH_MAX,
  RAIL_WIDTH_MIN,
  setAppRailWidth,
  useAppRailWidth,
} from "../lib/app-rail-width";

const APP_SHELL_RULES: readonly string[] = [
  "The header is earned, not permanent. The sidebar already says where you are, so chrome above the content has to justify itself on every route rather than being inherited from the layout.",
  "Mode A, flat: Overview, Inbox, Accounts, Campaigns. One 44px local toolbar. No global header of verbs/filters — those stay on the page as PageBand. Product AppChrome may mount a flat `header` slot naming the current destination (AppHeader); that slot is location chrome, not a second band for actions.",
  "Mode B, lineage: any object route. The header appears only because the sidebar cannot express depth. It carries back, lineage, and the object action, and nothing else.",
  "Mode C, focus: agent full screen. The sidebar becomes the thread rail. The navigation rail is not hidden in this mode, it is replaced, which is why the two live in different props. The product brand row (`railHeader` / AppRailBrand) stays mounted above the thread rail, and `railFooter` keeps Ask + identity/settings under the list — same person chrome as Home.",
  "Product rail collapse is shell geometry (`railCollapsed`): open column is 246–400px (default 246, the designed Primary Sidebar) and collapses to the 48px icon rail. Drag the right seam to resize; the brand chevron still collapses. Pane stays mounted — flat and focus both honour it. Brand + collapse live in `railHeader`; identity foot in `railFooter` (flat, lineage, and focus). Focus pins the same AppRailFoot under the thread list so Settings stays reachable. Never vanish the rail on a product route; never collapse SidebarNav items. A shell with no sidebar, header, or footer mounts no rail — destinations earn the column. Design fixtures that are judging a page, not the chrome, may omit them.",
  "Scroll ownership is explicit via `contentScroll` on THIS primitive — diagnose here, not in page demos. `shell` (default for flat / lineage) wraps children in the content ScrollArea with the route gutter. `host` (default for focus; required whenever children are SplitView or ChatInterface) is a height-bounded clip host with a flush measure. Wrapping those surfaces in the shell ScrollArea collapses `h-full` to content height, so panes pile at the top of an empty viewport instead of filling the column.",
  "Focus defaults to `host`: ChatInterface docks the composer to the page bottom and scrolls the thread inside. The thread rail is also a clip host; AgentThreadRail owns its list ScrollArea.",
  "Any AppShell that mounts SplitView must pass `contentScroll=\"host\"`. Enforced by ESLint `gtm/design-law/app-shell-host-scroll`. Never paper over a missing host with page-local height, sticky composer, or a full-width strip above the split.",
  "Zero, one, or one. Never two. Between the shell and the page there is at most one 44px band on any route: a page in lineage or focus mode does not also render a toolbar band, and filters fuse into whichever band already exists. Flat AppHeader and lineage/focus PageBand never stack.",
  "Page title: deleted as a *content* masthead on top-level pages. The flat AppHeader may repeat the nav label as location chrome when the rail is collapsed or for orientation; it is not PageMasthead and spends no `text-page` rung.",
  "Command search: moves to Cmd-K only. A field used by shortcut should not park 320px of chrome on every page in the product.",
  "Contextual action: moves into the page toolbar, beside the filters it acts on. An action floating in a global header is further from the rows it changes than the filter that selected them.",
  "Posture control: the sanctioned Home/Agent toggle is the segmented switch in the sidebar (every Core 5 surface board). Keep ⌘J. A floating duplicate in a global header stays forbidden. (`docs/plan/gtm-agent-product/surface-decisions-2026-08-10.md` §2.)",
  "The band region is 44px whether it holds a lineage band, a toolbar, or the flat AppHeader, and the shell reserves it rather than letting the band measure itself. The whole point of the decision is that the first row of content does not move as a rep walks from Accounts to an account to a campaign.",
  "Chrome `inset` is the product default (Home, Agent, and every AppShell mount): desk mat behind the rail; flush `shell` work column — no outer pad, radius, or hairline on the card; rail keeps `pt-2`. Shell-scrolled work (no band) matches that `pt-2` so PageMasthead / reading columns share the brand's top air. When a dock sibling is present, the air stays on the work column — the companion header is flush to the card. **`contentScroll=\"host\"` cards stay flush** — SplitView / ChatInterface fill the column edge-to-edge; list title air lives on `ListSidebarChrome`, never as a gap above the resize handle. Pass `chrome=\"bleed\"` only for gallery miniatures that must show the old full-bleed canvas.",
  "Width is a property of the route, chosen from a closed set of four, never a class at a call site. `contentWidth` takes `reading` 768, `work` 1280 (the default), `wide` (no cap), or `interstitial` 320 for auth and empty-shell routes, and there is no fifth value and no escape hatch. Four because Primer ships four `containerWidth` values and Polaris ships three, and nobody who has run a real product ships one. (Evidence 5.3 rule 2.)",
  "Pick the measure by job, not taste: `reading` for a bounded reading/settings column; `work` for Overview's peer briefing panels and a single readable list; `wide` for splits/rosters/grids that fill the pane; `interstitial` for auth. Product AppChrome's `SHELL_GEOMETRY` is the only place a route picks one — never pad the sides on the page.",
  "Centred, everywhere. Amal, 2026-08-05, overruling the evidence's own region-count heuristic: on a route that already has a rail or a dock, the work pane's content centres within the width the pane leaves rather than flushing left. There is no left-anchored value, and the anchor is not a prop, because an anchor that varies by route is a second decision every builder would have to make again. (Evidence 5.3 rule 4 as amended; the fork it settles is section 4.2, Fork 3.)",
  "The reading measure is 768 and it is the same 768 PageFrame already is. `contentWidth='reading'` and `max-w-3xl` are one number by construction (`--gtm-container-reading`), so a PageFrame route inherits the cap once, never twice. The agent thread column keeps its board-measured 704 as a documented exception with a stated reason, not as a fifth rung. (Primer `medium` 768; Tailwind `max-w-3xl`.)",
  "Side padding is 16, stepping to 24 at the large breakpoint, and there is no third step: `px-4 lg:px-6` on the measure at every one of the four values, `wide` included. (Primer's stated table; shadcn's own `px-4 lg:px-6`; Material's 24dp at medium. Nothing in the pass supports 32 as a page margin.)",
  "`wide` drops the cap and keeps the padding, and it is the only value that drops anything. It is for content wider than any measure -- a data grid, a stage matrix -- and it arrives with an obligation: the wide thing scrolls inside its own container. The page never grows its own horizontal scrollbar. That is not taste: the CORE 07 sticky-first-column contract is defined against the scroll container, so a page-level scrollbar sticks the column to the wrong element and the first column drifts. (Canada.ca states the escape as policy; evidence 5.3 rule 6.)",
  "Geometry is declared as custom properties on the shell root, so anything mounted inside can read the measure it is sitting in rather than being told twice. `--content-width` is the first of that family and joins `--sidebar-width` and `--band-height` rather than starting a parallel system. (Evidence 5.3 rule 8, independently confirmed by shadcn's reference block setting `--sidebar-width` and `--header-height` on its provider.)",
  "The agent companion is the shell's third region, not a SplitView pane. Flat mode always keeps a dock sibling beside the work column so opening it does not remount the page or break host-scroll height. The slot is width 0 when hidden; when open it takes the session width (default 420). The live AgentSurface fills that slot. Floating does not take this slot.",
];

/*
 * Open width is 246–400 (default 246). Collapse is still the 48px icon rail.
 * Collapse and expand interpolate width; direct resizing stays instant.
 */
const RAIL_CLASS = "relative shrink-0";
const RAIL_WIDTH_MOTION =
  "transition-[width] duration-fast ease-out-quint motion-reduce:transition-none";
const RAIL_BLEED_CLASS = "border-r border-line";
const RAIL_INSET_CLASS = "border-r border-line pt-2";
const RAIL_RESIZE_CLASS =
  "absolute inset-y-0 right-0 z-10 w-1.5 translate-x-1/2 cursor-col-resize touch-none bg-transparent hover:bg-line-strong/40 active:bg-line-strong/60";

/** Inset: desk mat behind the rail; work fills the content column flush (no pad, radius, or border). */
const INSET_ROOT_CLASS = "gap-0 bg-desk";
/** Same top air as the rail — shell scroll only; host fills flush. */
const INSET_CARD_CLASS =
  "min-h-0 min-w-0 flex-1 overflow-hidden rounded-none border-0 bg-shell";
const INSET_CARD_TOP_CLASS = "pt-2";
/** Band sits on the desk mat — not inside the card. */
const INSET_BAND_CLASS = "bg-transparent";

const SCROLL_REGION_CLASS = "min-h-0 flex-1";

const BAND_SLOT_CLASS = "h-toolbar shrink-0";
/** Inset desk masthead — same 44px rung as bleed; PageBand / AppHeader fill it. */
const INSET_BAND_SLOT_CLASS = "h-toolbar shrink-0";

type AppShellMode = "flat" | "lineage" | "focus";

type AppShellContentWidth = "reading" | "work" | "wide" | "interstitial";

type AppShellChrome = "bleed" | "inset";

const CONTENT_MEASURE_CLASS = "mx-auto w-full min-w-0 px-4 lg:px-6";

const CONTENT_WIDTH_CLASS: Record<AppShellContentWidth, string> = {
  reading: "max-w-reading",
  work: "max-w-work",
  wide: "max-w-none",
  interstitial: "max-w-interstitial",
};

const CONTENT_WIDTH_PROPERTY: Record<AppShellContentWidth, string> = {
  reading: "var(--gtm-container-reading)",
  work: "var(--gtm-container-work)",
  wide: "none",
  interstitial: "var(--gtm-container-interstitial)",
};

type AppShellContentScroll = "shell" | "host";

interface AppShellBaseProps {
  children: ReactNode;
  contentWidth?: AppShellContentWidth;
  contentScroll?: AppShellContentScroll;
  /**
   * `bleed` — full-bleed canvas (gallery / escape hatch).
   * `inset` — desk mat + flush shell column (product default).
   */
  chrome?: AppShellChrome;
}

interface NavigationRailProps {
  /** Destinations (normally SidebarNav). Omit to mount no rail. */
  sidebar?: ReactNode;
  /** Brand row: logo + name + collapse. Pinned above the scroll. */
  railHeader?: ReactNode;
  /** Identity foot. Pinned below the scroll — person chrome, not a nav list. */
  railFooter?: ReactNode;
  /** Narrow the rail to the 48px icon width. Focus mode ignores this. */
  railCollapsed?: boolean;
}

interface FlatShellProps extends AppShellBaseProps, NavigationRailProps {
  mode: "flat";
  /**
   * Optional 44px content-edge header (AppHeader). Location chrome only —
   * never a PageBand of verbs/filters.
   */
  header?: ReactNode;
  /**
   * Third region beside the work column. Product chrome always passes
   * AgentDockSlot so the work tree stays put when the companion opens.
   */
  dock?: ReactNode;
}

interface LineageShellProps extends AppShellBaseProps, NavigationRailProps {
  mode: "lineage";
  band: ReactNode;
}

interface FocusShellProps extends AppShellBaseProps {
  mode: "focus";
  threadRail: ReactNode;
  /**
   * Optional. Empty Focus has none (posture already names Agent). An open
   * thread mounts a flush toolbar band with the thread title.
   */
  band?: ReactNode;
  railHeader?: ReactNode;
  /** Same identity / settings foot as the product rail, pinned under the list. */
  railFooter?: ReactNode;
  railCollapsed?: boolean;
}

type AppShellProps = FlatShellProps | LineageShellProps | FocusShellProps;

function AppShellWorkColumn({
  children,
  contentWidth,
  hostScroll,
  topAir = false,
}: {
  children: ReactNode;
  contentWidth: AppShellContentWidth;
  hostScroll: boolean;
  topAir?: boolean;
}) {
  return (
    <Stack
      data-slot="app-shell-content"
      render={<main />}
      grow
      className={cn("h-full min-h-0 min-w-0", topAir && "pt-2")}
    >
      {hostScroll ? (
        <Stack
          data-slot="app-shell-measure"
          data-scroll="host"
          className={cn(
            "mx-auto h-full min-h-0 w-full min-w-0 flex-1 overflow-hidden",
            CONTENT_WIDTH_CLASS[contentWidth]
          )}
        >
          {children}
        </Stack>
      ) : (
        <ScrollArea recoveryKey="page" className={SCROLL_REGION_CLASS}>
          <Stack
            data-slot="app-shell-measure"
            data-scroll="shell"
            grow
            className={cn(
              CONTENT_MEASURE_CLASS,
              CONTENT_WIDTH_CLASS[contentWidth]
            )}
          >
            {children}
          </Stack>
        </ScrollArea>
      )}
    </Stack>
  );
}

function RailResizeHandle({
  onResize,
  width,
}: {
  onResize: (width: number) => void;
  width: number;
}) {
  const dragging = useRef(false);

  const onPointerDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      dragging.current = true;
      event.currentTarget.setPointerCapture?.(event.pointerId);
      const startX = event.clientX;
      const startWidth = width;

      const onMove = (moveEvent: PointerEvent) => {
        if (!dragging.current) return;
        onResize(startWidth + (moveEvent.clientX - startX));
      };

      const onUp = (upEvent: PointerEvent) => {
        dragging.current = false;
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        try {
          event.currentTarget.releasePointerCapture?.(upEvent.pointerId);
        } catch {
          /* capture already released */
        }
      };

      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [onResize, width]
  );

  const onKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      const direction = event.key === "ArrowRight" ? 1 : -1;
      const step = event.shiftKey ? 32 : 8;
      onResize(width + direction * step);
    },
    [onResize, width]
  );

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize sidebar"
      aria-valuemax={RAIL_WIDTH_MAX}
      aria-valuemin={RAIL_WIDTH_MIN}
      aria-valuenow={Math.round(width)}
      tabIndex={0}
      data-slot="app-shell-rail-resize"
      className={RAIL_RESIZE_CLASS}
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
    />
  );
}

function AppShell(props: AppShellProps) {
  const {
    children,
    chrome = "inset",
    contentWidth = "work",
    mode,
  } = props;
  const contentScroll =
    props.contentScroll ?? (mode === "focus" ? "host" : "shell");
  const hostScroll = contentScroll === "host";
  const inset = chrome === "inset";

  const railCollapsed =
    mode === "focus"
      ? (props.railCollapsed ?? false)
      : (props.railCollapsed ?? false);
  const railHeader =
    mode === "focus" ? (props.railHeader ?? null) : props.railHeader;
  const railFooter = props.railFooter ?? null;
  const railBody = mode === "focus" ? props.threadRail : (props.sidebar ?? null);
  const showRail =
    mode === "focus" ||
    railBody !== null ||
    railHeader != null ||
    railFooter !== null;
  const band =
    mode === "flat"
      ? (props.header ?? null)
      : mode === "focus" || mode === "lineage"
        ? (props.band ?? null)
        : null;
  const dock = mode === "flat" ? (props.dock ?? null) : null;
  const shellTopAir = inset && band === null && !hostScroll;
  const railWidth = useAppRailWidth();
  const [resizing, setResizing] = useState(false);

  const onRailResize = useCallback((next: number) => {
    setResizing(true);
    setAppRailWidth(next);
  }, []);

  useEffect(() => {
    if (!resizing) return;
    const end = () => setResizing(false);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
    window.addEventListener("keyup", end);
    return () => {
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      window.removeEventListener("keyup", end);
    };
  }, [resizing]);

  const displayedRailWidth = railCollapsed ? RAIL_WIDTH_COLLAPSED : railWidth;
  const sidebarWidth = `${displayedRailWidth}px`;

  return (
    <Inline
      data-slot="app-shell"
      data-mode={mode}
      data-chrome={chrome}
      data-content-width={contentWidth}
      data-content-scroll={contentScroll}
      data-rail-collapsed={railCollapsed ? "true" : "false"}
      align="stretch"
      bg={inset ? "none" : "canvas"}
      className={cn(
        "h-full min-h-0 w-full overflow-hidden",
        inset ? INSET_ROOT_CLASS : undefined
      )}
      style={
        {
          "--content-width": CONTENT_WIDTH_PROPERTY[contentWidth],
          "--sidebar-width": sidebarWidth,
        } as CSSProperties
      }
    >
      {showRail ? (
        <Stack
          data-slot="app-shell-rail"
          render={<aside />}
          bg="sidebar"
          style={{ width: displayedRailWidth }}
          className={cn(
            RAIL_CLASS,
            inset ? RAIL_INSET_CLASS : RAIL_BLEED_CLASS,
            !resizing && RAIL_WIDTH_MOTION
          )}
        >
          {railHeader}
          {mode === "focus" ? (
            <Box className={cn(SCROLL_REGION_CLASS, "overflow-hidden")}>
              {railBody}
            </Box>
          ) : (
            <ScrollArea className={SCROLL_REGION_CLASS}>{railBody}</ScrollArea>
          )}
          {railFooter}
          {railCollapsed ? null : (
            <RailResizeHandle width={railWidth} onResize={onRailResize} />
          )}
        </Stack>
      ) : null}

      <Stack
        data-slot="app-shell-main"
        grow
        className="min-h-0 min-w-0"
      >
        {band === null ? null : (
          <Box
            data-slot="app-shell-band"
            className={cn(
              inset ? INSET_BAND_SLOT_CLASS : BAND_SLOT_CLASS,
              inset ? INSET_BAND_CLASS : undefined
            )}
          >
            {band}
          </Box>
        )}
        <Stack
          data-slot="app-shell-card"
          grow
          className={cn(
            inset ? INSET_CARD_CLASS : "min-h-0 min-w-0",
            /*
             * Top air is for shell-scrolled pages that share the brand inset.
             * Host scroll (Inbox SplitView, Agent chat) fills flush — otherwise
             * the list resize handle floats 8px under the desk mat. A dock
             * sibling takes the card edge; the work column keeps the air.
             */
            shellTopAir && dock === null ? INSET_CARD_TOP_CLASS : undefined
          )}
        >
          <Inline
            grow
            align="stretch"
            className="h-full min-h-0 min-w-0 overflow-hidden"
          >
            <AppShellWorkColumn
              contentWidth={contentWidth}
              hostScroll={hostScroll}
              topAir={shellTopAir && dock !== null}
            >
              {children}
            </AppShellWorkColumn>
            {dock}
          </Inline>
        </Stack>
      </Stack>
    </Inline>
  );
}

export { AppShell, APP_SHELL_RULES };
export type {
  AppShellChrome,
  AppShellContentScroll,
  AppShellContentWidth,
  AppShellMode,
  AppShellProps,
  FlatShellProps,
  FocusShellProps,
  LineageShellProps,
};
