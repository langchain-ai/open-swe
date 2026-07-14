type MessageTimestampProps = {
  timestamp: string;
  startedAt?: string;
  align?: "left" | "right";
  className?: string;
};

const timeFormatter = new Intl.DateTimeFormat(undefined, {
  hour: "numeric",
  minute: "2-digit",
  second: "2-digit",
});

const datedTimeFormatter = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
  second: "2-digit",
});

const fullFormatter = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "medium",
});

function parseTimestamp(value?: string | null): Date | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function isSameLocalDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

function shortTimestamp(date: Date): string {
  return isSameLocalDay(date, new Date())
    ? timeFormatter.format(date)
    : datedTimeFormatter.format(date);
}

export function MessageTimestamp({
  timestamp,
  startedAt,
  align = "left",
  className = "",
}: MessageTimestampProps) {
  const date = parseTimestamp(timestamp);
  if (!date) return null;

  const startDate = parseTimestamp(startedAt);
  const title =
    startDate && Math.abs(date.getTime() - startDate.getTime()) >= 1000
      ? `Started ${fullFormatter.format(startDate)} · Last updated ${fullFormatter.format(date)}`
      : fullFormatter.format(date);

  return (
    <div
      className={`flex ${align === "right" ? "justify-end" : "justify-start"} ${className}`}
    >
      <time
        dateTime={date.toISOString()}
        title={title}
        className="text-[11px] leading-4 text-[color:var(--ui-text-dim)] tabular-nums select-none"
      >
        {shortTimestamp(date)}
      </time>
    </div>
  );
}
