"use client";

/*
 * Rules for ComingUpCalendar.
 *
 * Home's Coming up band. List is the default. List / Calendar is a compact
 * icon switch in the section header, in the slot Paper used for the meeting
 * count. 3 days / week / month use the same compact icon switch, calendar-only.
 * Inspired by ReUI Event Calendar 1's range switch and Event Calendar 6's hover
 * detail, drawn in CORE tokens. Not the ReUI engine: no drag, no create, no
 * recurrence, no resource columns.
 */

import { useMemo, useState } from "react";
import {
  addDays,
  addMonths,
  addWeeks,
  eachDayOfInterval,
  endOfMonth,
  endOfWeek,
  format,
  isSameDay,
  isSameMonth,
  startOfDay,
  startOfMonth,
  startOfWeek,
} from "date-fns";

import {
  DEFAULT_COLLAPSED_COUNT,
  ListContinuation,
  ListViewport,
  clipList,
} from "./change-feed";
import { Badge } from "../ui/badge";
import { Box, Inline, Stack } from "../ui/box";
import { Button } from "../ui/button";
import { cn } from "../ui/cn";
import {
  Calendar,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Columns,
  LayoutGrid,
  List,
  type Glyph,
} from "../ui/glyphs";
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "../ui/hover-card";
import { Icon } from "../ui/icon";
import {
  ScrollAreaBody,
  SCROLL_HOST_CLASS,
} from "../ui/scroll-area";
import { ToggleGroup, ToggleGroupItem } from "../ui/toggle-group";
import { NO_CLOCK } from "../lib/use-clock";

const COMING_UP_CALENDAR_RULES: readonly string[] = [
  "List is the default Coming up view. List / Calendar is a compact icon switch in the section header, replacing the meeting count. 3 days, week, and month are the same compact icon switch, calendar-only. There is no drag, create, recurrence, or resource column.",
  "The list keeps five rows on the page and View more expands the same panel, capped, with the list scrolling inside. Calendar views do not clip: a day shows a few chips, then +N, and the overflow card lists the rest.",
  "HoverCard, not Tooltip: title, account, clock + day, status. Status is Soon / Later today / Ready from the meeting time against the clock, never a second API field. Chips and list rows mark it with a square vertical tone line only, never a fill, never coloured title, never a rounded chip. The badge still names it. Sweeping waits the 600ms family. Click is the verb; the card never grows an Open brief button.",
  "Flush inside the contained Coming up panel. Hairlines between cells, never a second panel. Period nav is ghost icon-sm and hides on the list. Today is weight, not a primary fill.",
  "Before the clock is read, the visible period comes from the soonest meeting so the server pass and the first client pass agree. Today is unmarked until `now` is a real time.",
];

type ComingUpMode = "list" | "calendar";
type ComingUpRange = "three-day" | "week" | "month";
type ComingUpStatus = "soon" | "today" | "ready";
type ComingUpStatusTone = "attention" | "info" | "positive";

const COMPACT_TOGGLE_TRACK = "h-control-sm rounded-compact p-px";
const COMPACT_TOGGLE_ITEM = "size-6 px-0";

const MODE_CHOICES: readonly {
  value: ComingUpMode;
  label: string;
  icon: Glyph;
}[] = [
  { value: "list", label: "List", icon: List },
  { value: "calendar", label: "Calendar", icon: CalendarDays },
];

const RANGE_CHOICES: readonly {
  value: ComingUpRange;
  label: string;
  icon: Glyph;
}[] = [
  { value: "three-day", label: "3 days", icon: Columns },
  { value: "week", label: "Week", icon: Calendar },
  { value: "month", label: "Month", icon: LayoutGrid },
];

const WEEKDAY_LABELS = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"] as const;

const GRID_COLS_CLASS: Record<ComingUpRange, string> = {
  "three-day": "grid-cols-3",
  week: "grid-cols-7",
  month: "grid-cols-7",
};

const VISIBLE_EVENTS_PER_DAY: Record<ComingUpRange, number> = {
  "three-day": 6,
  week: 4,
  month: 2,
};

const NAV_LABEL: Record<ComingUpRange, { next: string; prev: string }> = {
  "three-day": { next: "Next 3 days", prev: "Previous 3 days" },
  week: { next: "Next week", prev: "Previous week" },
  month: { next: "Next month", prev: "Previous month" },
};

const EVENT_CHIP_CLASS =
  "flex w-full min-w-0 items-stretch gap-1 px-0 text-left text-meta text-ink outline-none hover:bg-hover focus-visible:ring-2 focus-visible:ring-primary";

const OVERFLOW_CHIP_CLASS =
  "w-full truncate px-1 text-left text-meta text-ink-subtle outline-none hover:bg-hover focus-visible:ring-2 focus-visible:ring-primary";

/** Same 4-hour window the queue uses for an imminent meeting. */
const SOON_MS = 4 * 60 * 60 * 1000;

const STATUS_LABEL: Record<ComingUpStatus, string> = {
  soon: "Soon",
  today: "Later today",
  ready: "Ready",
};

const STATUS_TONE: Record<ComingUpStatus, ComingUpStatusTone> = {
  soon: "attention",
  today: "info",
  ready: "positive",
};

const STATUS_BAR_CLASS: Record<ComingUpStatus, string> = {
  soon: "bg-attention",
  today: "bg-info",
  ready: "bg-positive",
};

const STATUS_BAR = "w-0.5 self-stretch shrink-0";

interface ComingUpMeeting {
  id: string;
  title: string;
  account?: string | null;
  startsAt: string;
  resourceId: string;
}

interface ComingUpCalendarProps {
  meetings: readonly ComingUpMeeting[];
  now: number;
  onOpen: (meeting: ComingUpMeeting) => void;
  /** Controlled from the section header. Default is the list. */
  mode?: ComingUpMode;
  /** First range when Calendar is opened. Tests may start on month. */
  defaultRange?: ComingUpRange;
}

function ComingUpIconToggle<Value extends string>({
  ariaLabel,
  items,
  onValueChange,
  value,
}: {
  ariaLabel: string;
  items: readonly { value: Value; label: string; icon: Glyph }[];
  onValueChange: (next: Value) => void;
  value: Value;
}) {
  return (
    <ToggleGroup
      value={[value]}
      aria-label={ariaLabel}
      className={COMPACT_TOGGLE_TRACK}
      onValueChange={(next: Value[]) => {
        const picked = next[0];
        if (picked === undefined) return;
        onValueChange(picked);
      }}
    >
      {items.map((item) => (
        <ToggleGroupItem
          key={item.value}
          value={item.value}
          aria-label={item.label}
          className={COMPACT_TOGGLE_ITEM}
        >
          <Icon icon={item.icon} size="sm" />
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}

/** Clock time for a preview — Paper leads with when, not "In 2h". */
function comingUpClock(isoTime: string, now: number): string | undefined {
  if (now === NO_CLOCK) return undefined;
  const startsAt = new Date(isoTime);
  if (Number.isNaN(startsAt.getTime())) return undefined;
  return format(startsAt, "h:mm");
}

/** Day chip beside the clock: Today / Tomorrow / weekday. */
function comingUpDay(isoTime: string, now: number): string | undefined {
  if (now === NO_CLOCK) return undefined;
  const startsAt = new Date(isoTime);
  if (Number.isNaN(startsAt.getTime())) return undefined;
  const today = new Date(now);
  if (isSameDay(startsAt, today)) return "Today";
  if (isSameDay(startsAt, addDays(today, 1))) return "Tomorrow";
  return format(startsAt, "EEE");
}

/** Soon / Later today / Ready from the meeting time. Unread clock stays Ready. */
function comingUpStatus(isoTime: string, now: number): ComingUpStatus {
  if (now === NO_CLOCK) return "ready";
  const startsAt = new Date(isoTime).getTime();
  if (Number.isNaN(startsAt)) return "ready";
  if (startsAt <= now + SOON_MS) return "soon";
  if (isSameDay(new Date(startsAt), new Date(now))) return "today";
  return "ready";
}

function ComingUpStatusBadge({ status }: { status: ComingUpStatus }) {
  return (
    <Badge tier="quiet" tone={STATUS_TONE[status]}>
      {STATUS_LABEL[status]}
    </Badge>
  );
}

function originFromMeetings(
  meetings: readonly ComingUpMeeting[],
  now: number
): Date {
  if (now !== NO_CLOCK) return startOfDay(new Date(now));
  const first = meetings[0];
  if (first !== undefined) {
    const startsAt = new Date(first.startsAt);
    if (!Number.isNaN(startsAt.getTime())) return startOfDay(startsAt);
  }
  return startOfDay(new Date());
}

function monthGridDays(month: Date): Date[] {
  return eachDayOfInterval({
    start: startOfWeek(startOfMonth(month)),
    end: endOfWeek(endOfMonth(month)),
  });
}

function daysForView(origin: Date, view: ComingUpRange): Date[] {
  if (view === "three-day") {
    const start = startOfDay(origin);
    return eachDayOfInterval({ start, end: addDays(start, 2) });
  }
  if (view === "week") {
    return eachDayOfInterval({
      start: startOfWeek(origin),
      end: endOfWeek(origin),
    });
  }
  return monthGridDays(origin);
}

function chunkWeeks(days: readonly Date[]): Date[][] {
  const weeks: Date[][] = [];
  for (let index = 0; index < days.length; index += 7) {
    weeks.push(days.slice(index, index + 7));
  }
  return weeks;
}

function shiftOrigin(
  origin: Date,
  view: ComingUpRange,
  direction: 1 | -1
): Date {
  if (view === "three-day") return addDays(origin, direction * 3);
  if (view === "week") return addWeeks(origin, direction);
  return addMonths(origin, direction);
}

function periodLabel(origin: Date, view: ComingUpRange): string {
  if (view === "month") return format(origin, "MMMM yyyy");
  const days = daysForView(origin, view);
  const start = days[0];
  const end = days[days.length - 1];
  if (start === undefined || end === undefined) return "";
  if (
    start.getMonth() === end.getMonth() &&
    start.getFullYear() === end.getFullYear()
  ) {
    return `${format(start, "d")}-${format(end, "d MMM")}`;
  }
  return `${format(start, "d MMM")} - ${format(end, "d MMM")}`;
}

function dayKey(day: Date): string {
  return format(day, "yyyy-MM-dd");
}

function groupMeetingsByDay(
  meetings: readonly ComingUpMeeting[]
): Map<string, ComingUpMeeting[]> {
  const grouped = new Map<string, ComingUpMeeting[]>();
  for (const meeting of meetings) {
    const startsAt = new Date(meeting.startsAt);
    if (Number.isNaN(startsAt.getTime())) continue;
    const key = dayKey(startsAt);
    const bucket = grouped.get(key);
    if (bucket === undefined) {
      grouped.set(key, [meeting]);
    } else {
      bucket.push(meeting);
    }
  }
  return grouped;
}

function ComingUpMeetingPreview({
  meeting,
  now,
}: {
  meeting: ComingUpMeeting;
  now: number;
}) {
  const clock = comingUpClock(meeting.startsAt, now);
  const day = comingUpDay(meeting.startsAt, now);
  const when = [clock, day].filter(Boolean).join(" · ");
  const status = comingUpStatus(meeting.startsAt, now);

  return (
    <Stack gap="sm" data-slot="coming-up-preview">
      <Stack gap="xs">
        <Box render={<span />} className="text-label font-medium text-ink">
          {meeting.title}
        </Box>
        {meeting.account ? (
          <Box render={<span />} className="text-meta text-ink-subtle">
            {meeting.account}
          </Box>
        ) : null}
        {when.length > 0 ? (
          <Box render={<span />} className="text-meta text-ink-subtle">
            {when}
          </Box>
        ) : null}
      </Stack>
      <ComingUpStatusBadge status={status} />
    </Stack>
  );
}

function ComingUpEventChip({
  meeting,
  now,
  onOpen,
}: {
  meeting: ComingUpMeeting;
  now: number;
  onOpen: ComingUpCalendarProps["onOpen"];
}) {
  const status = comingUpStatus(meeting.startsAt, now);

  return (
    <HoverCard>
      <HoverCardTrigger
        render={<button type="button" />}
        data-slot="coming-up-event"
        data-status={status}
        data-testid={`coming-up-event-${meeting.id}`}
        aria-label={`${meeting.title}, ${STATUS_LABEL[status]}`}
        className={EVENT_CHIP_CLASS}
        onClick={() => onOpen(meeting)}
      >
        <Box
          aria-hidden
          className={cn(STATUS_BAR, STATUS_BAR_CLASS[status])}
        />
        <Box render={<span />} className="min-w-0 flex-1 truncate">
          {meeting.title}
        </Box>
      </HoverCardTrigger>
      <HoverCardContent side="top" align="start" sideOffset={6}>
        <ComingUpMeetingPreview meeting={meeting} now={now} />
      </HoverCardContent>
    </HoverCard>
  );
}

function ComingUpOverflowItem({
  meeting,
  now,
  onOpen,
}: {
  meeting: ComingUpMeeting;
  now: number;
  onOpen: ComingUpCalendarProps["onOpen"];
}) {
  const clock = comingUpClock(meeting.startsAt, now);
  const status = comingUpStatus(meeting.startsAt, now);

  return (
    <Inline
      render={<button type="button" />}
      data-slot="coming-up-overflow-item"
      data-status={status}
      data-testid={`coming-up-overflow-item-${meeting.id}`}
      gap="sm"
      align="center"
      className="w-full cursor-pointer rounded-compact px-1 py-1 text-left outline-none hover:bg-hover focus-visible:ring-2 focus-visible:ring-primary"
      onClick={() => onOpen(meeting)}
    >
      <Box
        aria-hidden
        className={cn(STATUS_BAR, STATUS_BAR_CLASS[status])}
      />
      <Box
        render={<span />}
        className="w-10 shrink-0 text-meta text-ink-subtle tabular-nums"
      >
        {clock ?? ""}
      </Box>
      <Stack gap="none" className="min-w-0 flex-1">
        <Box
          render={<span />}
          className="truncate text-label font-medium text-ink"
        >
          {meeting.title}
        </Box>
        {meeting.account ? (
          <Box
            render={<span />}
            className="truncate text-meta text-ink-subtle"
          >
            {meeting.account}
          </Box>
        ) : null}
      </Stack>
    </Inline>
  );
}

function ComingUpOverflowChip({
  day,
  meetings,
  now,
  onOpen,
}: {
  day: Date;
  meetings: readonly ComingUpMeeting[];
  now: number;
  onOpen: ComingUpCalendarProps["onOpen"];
}) {
  return (
    <HoverCard>
      <HoverCardTrigger
        render={<button type="button" />}
        data-slot="coming-up-overflow"
        data-testid={`coming-up-overflow-${dayKey(day)}`}
        className={OVERFLOW_CHIP_CLASS}
      >
        {`+${meetings.length}`}
      </HoverCardTrigger>
      <HoverCardContent
        side="top"
        align="start"
        sideOffset={6}
        className={SCROLL_HOST_CLASS}
      >
        <ScrollAreaBody>
          <Stack gap="xs">
            {meetings.map((meeting) => (
              <ComingUpOverflowItem
                key={meeting.id}
                meeting={meeting}
                now={now}
                onOpen={onOpen}
              />
            ))}
          </Stack>
        </ScrollAreaBody>
      </HoverCardContent>
    </HoverCard>
  );
}

function ComingUpRow({
  meeting,
  now,
  onOpen,
}: {
  meeting: ComingUpMeeting;
  now: number;
  onOpen: ComingUpCalendarProps["onOpen"];
}) {
  const open = () => onOpen(meeting);
  const clock = comingUpClock(meeting.startsAt, now);
  const day = comingUpDay(meeting.startsAt, now);
  const status = comingUpStatus(meeting.startsAt, now);

  return (
    <Inline
      data-slot="coming-up-row"
      data-status={status}
      data-testid={`coming-up-row-${meeting.id}`}
      render={<li />}
      role="option"
      aria-selected={false}
      tabIndex={0}
      onClick={open}
      onKeyDown={(event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        open();
      }}
      gap="md"
      align="center"
      className="min-h-row-record w-full cursor-pointer border-b border-line px-4 py-2.5 text-left outline-none last:border-b-0 hover:bg-hover focus-visible:ring-2 focus-visible:ring-primary"
    >
      <Stack gap="none" className="w-14 shrink-0">
        <Box
          render={<span />}
          className="text-label font-medium text-ink tabular-nums"
        >
          {clock ?? ""}
        </Box>
        <Box render={<span />} className="text-meta text-ink-subtle">
          {day ?? ""}
        </Box>
      </Stack>
      <Box
        aria-hidden
        className={cn(STATUS_BAR, STATUS_BAR_CLASS[status])}
      />
      <Stack gap="none" className="min-w-0 flex-1">
        <Box
          render={<span />}
          className="truncate text-label font-medium text-ink"
        >
          {meeting.title}
        </Box>
        {meeting.account ? (
          <Box
            render={<span />}
            className="truncate text-meta text-ink-subtle"
          >
            {meeting.account}
          </Box>
        ) : null}
      </Stack>
      <ComingUpStatusBadge status={status} />
    </Inline>
  );
}

function ComingUpList({
  meetings,
  now,
  onOpen,
}: {
  meetings: readonly ComingUpMeeting[];
  now: number;
  onOpen: ComingUpCalendarProps["onOpen"];
}) {
  const clipped = clipList(meetings, DEFAULT_COLLAPSED_COUNT);
  const [expanded, setExpanded] = useState(false);
  const shown = clipped.clipped && !expanded ? clipped.visible : meetings;

  return (
    <Stack gap="none" className="w-full">
      <ListViewport constrained={clipped.clipped && expanded}>
        <Stack
          data-slot="coming-up-list"
          render={<ul />}
          role="listbox"
          aria-label="Coming up"
          gap="none"
          className="w-full"
        >
          {shown.map((meeting) => (
            <ComingUpRow
              key={meeting.id}
              meeting={meeting}
              now={now}
              onOpen={onOpen}
            />
          ))}
        </Stack>
      </ListViewport>
      {clipped.clipped ? (
        <ListContinuation
          testId="coming-up-continuation"
          expanded={expanded}
          onToggle={() => setExpanded((open) => !open)}
        />
      ) : null}
    </Stack>
  );
}

function ComingUpDayCell({
  columns,
  day,
  dimmed,
  index,
  lastRow,
  meetings,
  now,
  onOpen,
  showDate,
  visibleLimit,
}: {
  columns: number;
  day: Date;
  dimmed: boolean;
  index: number;
  lastRow: boolean;
  meetings: readonly ComingUpMeeting[];
  now: number;
  onOpen: ComingUpCalendarProps["onOpen"];
  showDate: boolean;
  visibleLimit: number;
}) {
  const currentDay = now !== NO_CLOCK && isSameDay(day, new Date(now));
  const visible = meetings.slice(0, visibleLimit);
  const hidden = meetings.slice(visibleLimit);
  const col = index % columns;

  return (
    <Stack
      role="gridcell"
      data-slot="coming-up-day"
      data-date={dayKey(day)}
      data-today={currentDay ? "" : undefined}
      padding="xs"
      gap="xs"
      className={cn(
        "min-h-row-convo min-w-0",
        col < columns - 1 && "border-r border-line",
        !lastRow && "border-b border-line",
        dimmed && "opacity-50"
      )}
    >
      {showDate ? (
        <Box
          render={<span />}
          className={cn(
            "text-meta tabular-nums",
            currentDay ? "font-medium text-ink" : "text-ink-subtle"
          )}
        >
          {format(day, "d")}
        </Box>
      ) : null}
      {visible.map((meeting) => (
        <ComingUpEventChip
          key={meeting.id}
          meeting={meeting}
          now={now}
          onOpen={onOpen}
        />
      ))}
      {hidden.length > 0 ? (
        <ComingUpOverflowChip
          day={day}
          meetings={hidden}
          now={now}
          onOpen={onOpen}
        />
      ) : null}
    </Stack>
  );
}

function ComingUpDatedHeader({
  columns,
  day,
  index,
  now,
}: {
  columns: number;
  day: Date;
  index: number;
  now: number;
}) {
  const currentDay = now !== NO_CLOCK && isSameDay(day, new Date(now));

  return (
    <Box
      role="columnheader"
      className={cn(
        "px-1 py-1 text-center text-meta tracking-caps text-ink-subtle border-b border-line",
        index < columns - 1 && "border-r border-line",
        currentDay && "font-medium text-ink"
      )}
    >
      {format(day, "EEE d")}
    </Box>
  );
}

function ComingUpGrid({
  meetings,
  now,
  onOpen,
  origin,
  view,
}: {
  meetings: readonly ComingUpMeeting[];
  now: number;
  onOpen: ComingUpCalendarProps["onOpen"];
  origin: Date;
  view: ComingUpRange;
}) {
  const days = useMemo(() => daysForView(origin, view), [origin, view]);
  const weeks = useMemo(
    () => (view === "month" ? chunkWeeks(days) : []),
    [days, view]
  );
  const byDay = useMemo(() => groupMeetingsByDay(meetings), [meetings]);
  const columns = view === "three-day" ? 3 : 7;
  const visibleLimit = VISIBLE_EVENTS_PER_DAY[view];
  const showDate = view === "month";

  return (
    <Box
      data-slot="coming-up-calendar"
      data-view={view}
      role="grid"
      aria-label="Coming up"
      className={cn("grid", GRID_COLS_CLASS[view])}
    >
      {view === "month" ? (
        <Box role="row" className="contents">
          {WEEKDAY_LABELS.map((label, index) => (
            <Box
              key={label}
              role="columnheader"
              className={cn(
                "px-1 py-1 text-center text-meta tracking-caps text-ink-subtle border-b border-line",
                index < 6 && "border-r border-line"
              )}
            >
              {label}
            </Box>
          ))}
        </Box>
      ) : (
        <Box role="row" className="contents">
          {days.map((day, index) => (
            <ComingUpDatedHeader
              key={dayKey(day)}
              columns={columns}
              day={day}
              index={index}
              now={now}
            />
          ))}
        </Box>
      )}
      {view === "month"
        ? weeks.map((week, weekIndex) => (
            <Box key={dayKey(week[0] ?? origin)} role="row" className="contents">
              {week.map((day, dayIndex) => (
                <ComingUpDayCell
                  key={dayKey(day)}
                  columns={7}
                  day={day}
                  dimmed={!isSameMonth(day, origin)}
                  index={weekIndex * 7 + dayIndex}
                  lastRow={weekIndex === weeks.length - 1}
                  meetings={byDay.get(dayKey(day)) ?? []}
                  now={now}
                  onOpen={onOpen}
                  showDate={showDate}
                  visibleLimit={visibleLimit}
                />
              ))}
            </Box>
          ))
        : (
            <Box role="row" className="contents">
              {days.map((day, index) => (
                <ComingUpDayCell
                  key={dayKey(day)}
                  columns={columns}
                  day={day}
                  dimmed={false}
                  index={index}
                  lastRow
                  meetings={byDay.get(dayKey(day)) ?? []}
                  now={now}
                  onOpen={onOpen}
                  showDate={showDate}
                  visibleLimit={visibleLimit}
                />
              ))}
            </Box>
          )}
    </Box>
  );
}

function ComingUpModeToggle({
  label = "Coming up view",
  onValueChange,
  value,
}: {
  /** Accessible name for the List / Calendar switch. */
  label?: string;
  onValueChange: (mode: ComingUpMode) => void;
  value: ComingUpMode;
}) {
  return (
    <ComingUpIconToggle
      ariaLabel={label}
      items={MODE_CHOICES}
      value={value}
      onValueChange={onValueChange}
    />
  );
}

function ComingUpRangeToolbar({
  onNext,
  onPrevious,
  onRangeChange,
  origin,
  range,
}: {
  onNext: () => void;
  onPrevious: () => void;
  onRangeChange: (range: ComingUpRange) => void;
  origin: Date;
  range: ComingUpRange;
}) {
  return (
    <Inline
      data-slot="coming-up-toolbar"
      justify="between"
      align="center"
      wrap
      className="border-b border-line px-3 py-2"
    >
      <ComingUpIconToggle
        ariaLabel="Calendar range"
        items={RANGE_CHOICES}
        value={range}
        onValueChange={onRangeChange}
      />
      <Inline gap="xs" align="center">
        <Box
          render={<p />}
          data-slot="coming-up-period"
          className="text-label font-medium text-ink"
        >
          {periodLabel(origin, range)}
        </Box>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label={NAV_LABEL[range].prev}
          onClick={onPrevious}
        >
          <Icon icon={ChevronLeft} size="sm" />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label={NAV_LABEL[range].next}
          onClick={onNext}
        >
          <Icon icon={ChevronRight} size="sm" />
        </Button>
      </Inline>
    </Inline>
  );
}

/**
 * Coming up body: list, or a calendar whose range lives in this toolbar.
 */
function ComingUpCalendar({
  defaultRange = "week",
  meetings,
  mode = "list",
  now,
  onOpen,
}: ComingUpCalendarProps) {
  const [range, setRange] = useState<ComingUpRange>(defaultRange);
  const [cursor, setCursor] = useState<Date | null>(null);
  const origin = cursor ?? originFromMeetings(meetings, now);

  if (mode === "list") {
    return <ComingUpList meetings={meetings} now={now} onOpen={onOpen} />;
  }

  return (
    <Stack gap="none" className="w-full">
      <ComingUpRangeToolbar
        origin={origin}
        range={range}
        onRangeChange={setRange}
        onPrevious={() => setCursor(shiftOrigin(origin, range, -1))}
        onNext={() => setCursor(shiftOrigin(origin, range, 1))}
      />
      <ComingUpGrid
        meetings={meetings}
        now={now}
        onOpen={onOpen}
        origin={origin}
        view={range}
      />
    </Stack>
  );
}

export { ComingUpCalendar, ComingUpModeToggle, COMING_UP_CALENDAR_RULES };
export type {
  ComingUpCalendarProps,
  ComingUpMeeting,
  ComingUpMode,
  ComingUpRange,
};
