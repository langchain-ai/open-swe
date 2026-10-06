"use client";

/*
 * Rules for RecordHeader.
 *
 * Shared Accounts + Campaigns object chrome (CORE 03/04): identity,
 * status, quiet meta — not a second PageBand. Title leads; supporting
 * lines sit on a real gap, not gap-none + mt hacks.
 */

import {
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { SchemaText } from "./schema-cell";
import { Box, Inline, Stack } from "../ui/box";
import { Button } from "../ui/button";
import { cn } from "../ui/cn";
import { FrameTitle } from "../ui/frame";
import { ArrowLeft, Check, Copy, type Glyph } from "../ui/glyphs";
import { Icon } from "../ui/icon";
import { Input } from "../ui/input";
import { Separator } from "../ui/separator";
import { copyText } from "../lib/clipboard";
import { Textarea } from "../ui/textarea";

const RECORD_HEADER_RULES: readonly string[] = [
  "RecordHeader is object identity inside the work pane — title, status, and quiet meta. It is not a PageBand and must not host a second band of filters.",
  "The ghost back hop is RecordHop: `h-control`, outside `PAGE_STACK_CLASS`. Shell `pt-2` plus this row is the same line as AppRailBrand. Never put the hop inside `py-6`.",
  "The title sits on a control-height row with actions trailing, the same identity band Inbox and Alerts use. Type order is title → optional short subtitle → facts. Three rungs: `text-title` (name), `text-label` (subtitle), fact rows (status, type, window, description).",
  "The identity stack uses `gap=\"md\"` under the title and `gap=\"sm\"` between support lines so the name and facts do not kiss. The hairline sits after `gap=\"lg\"`.",
  "A long object description is a RecordFact (`expandable`), not a free line under the title. Collapsed copy uses `line-clamp-3`; click the copy to expand the rest in the value lane, click again to clamp. Do not add a Show more control, and do not clip the expanded copy in a scrollport. Icon and label sit in `h-control` on every fact row so Description padding matches Status / Type / Window. A one-line Description sits in `min-h-control` and centers with that icon and label. Expanded copy stays top-aligned. A short commercial subtitle (Accounts) may still use the subtitle slot and truncates on `max-w-xl`.",
  "Status may sit on the title or in a RecordFact row as a Quiet badge. Do not draw it twice. Reporting and table filters do not belong here.",
  "Object facts use RecordFact: icon, fixed label lane, value. That is the Inbox / Alerts property row, not a chip strip. Primary actions sit trailing on the title row. Keep one solid primary; everything else Quiet or outline. A mailbox or identifier may set `copyable`: hover and focus reveal a ghost Copy in the value lane. The button name stays Copy {label}; a live region says Copied. Do not copy every fact.",
  "When identity is writable, the title is the field. Description stays an expandable RecordFact: collapsed copy uses `line-clamp-3`, click expands the same copy in the value lane. A manager's editor mounts only after that expand and grows with the copy; it is not a two-line scroll field. Campaign type reuses the create ChoiceCards (icon, label, description) from an outline Button on the control rung. Window uses the stock DatePicker on that same height. Hover and focus reveal the title field; picker commit saves type and window. The title field drops Input's default horizontal pad so the name lines up with the fact rows. Single-line values, including a one-line Description, sit in `min-h-control` and center with the icon and label. Expanded Description stays top-aligned. Do not add a sibling Edit dialog or a trailing settings section for those same fields.",
  "Compose StatReadout beside the header when the board needs a provisional figure — never invent KPI tiles in chrome.",
  "Shared by Accounts detail and Campaign Studio. Prefer this over page-local identity stacks.",
];

const SUBTITLE_CLASS = "min-w-0 text-left text-label text-ink-muted";
const SUBTITLE_TRIGGER_CLASS =
  "cursor-pointer rounded-none outline-none focus-visible:ring-2 focus-visible:ring-primary";
const FACT_DESCRIPTION_CLASS = "w-full min-w-0 text-left text-label text-ink";
const FACT_ROW_CLASS = "min-h-control w-full";
const FACT_ICON_CLASS = "flex h-control w-4 shrink-0 items-center justify-center";
const FACT_LABEL_CLASS =
  "flex h-control w-32 shrink-0 items-center truncate text-label text-ink-subtle";
const FACT_DESCRIPTION_CLAMP_CLASS = "line-clamp-3 text-pretty";
const FACT_VALUE_CLASS = "min-w-0 flex-1";
const FACT_VALUE_SINGLE_CLASS = "flex min-h-control items-center";
const FACT_DESCRIPTION_SINGLE_CLASS =
  "flex min-h-control flex-col justify-center";
const RECORD_HOP_CLASS = "h-control shrink-0";
const FACT_COPY_CLASS =
  "shrink-0 opacity-0 transition-opacity duration-fast ease-out-quint group-hover/fact:opacity-100 group-focus-within/fact:opacity-100 [@media(hover:none)]:opacity-100 motion-reduce:transition-none";
const COPIED_ACKNOWLEDGEMENT_MS = 1_600;
const TITLE_EDIT_CLASS =
  "h-auto min-h-control border-transparent bg-transparent px-0 font-semibold md:text-title hover:border-line hover:bg-hover";
const BODY_EDIT_CLASS =
  "field-sizing-content min-h-control resize-none overflow-hidden border-transparent bg-transparent px-0 text-label md:text-label hover:border-line hover:bg-hover";

type RecordInlineEditKind = "TITLE" | "BODY";

interface RecordInlineEditProps {
  error?: string;
  kind: RecordInlineEditKind;
  label: string;
  onCancel: () => void;
  onChange: (value: string) => void;
  onCommit: () => void;
  placeholder?: string;
  value: string;
}

interface RecordHeaderProps {
  title: ReactNode;
  subtitle?: string;
  status?: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  mark?: ReactNode;
  className?: string;
}

interface RecordFactProps {
  icon: Glyph;
  label: string;
  value: ReactNode;
  /** Hover and focus reveal Copy in the value lane. String values only. */
  copyable?: boolean;
  /** Long copy expands in the value lane. String values only. */
  expandable?: boolean;
  /** Mounts only after the fact expands. Collapsed copy stays text. */
  edit?: Omit<RecordInlineEditProps, "kind">;
}

interface RecordHopProps {
  label: string;
  onClick: () => void;
}

function AutoGrowDescriptionEdit({
  error,
  label,
  onCancel,
  onChange,
  onCommit,
  placeholder,
  value,
}: Omit<RecordInlineEditProps, "kind">) {
  const ref = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    const node = ref.current;
    if (node === null) return;
    node.style.height = "auto";
    node.style.height = `${node.scrollHeight}px`;
  }, [value]);

  return (
    <Textarea
      ref={ref}
      aria-invalid={error !== undefined}
      aria-label={label}
      className={BODY_EDIT_CLASS}
      placeholder={placeholder}
      rows={1}
      value={value}
      onBlur={onCommit}
      onChange={(event) => onChange(event.target.value)}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          onCancel();
        }
      }}
    />
  );
}

function RecordInlineEdit({
  error,
  kind,
  label,
  onCancel,
  onChange,
  onCommit,
  placeholder,
  value,
}: RecordInlineEditProps) {
  switch (kind) {
    case "TITLE":
      return (
        <Input
          aria-invalid={error !== undefined}
          aria-label={label}
          autoComplete="off"
          className={TITLE_EDIT_CLASS}
          value={value}
          onBlur={onCommit}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              onCancel();
              return;
            }
            if (event.key === "Enter") {
              event.preventDefault();
              event.currentTarget.blur();
            }
          }}
        />
      );
    case "BODY":
      return (
        <AutoGrowDescriptionEdit
          error={error}
          label={label}
          placeholder={placeholder}
          value={value}
          onCancel={onCancel}
          onChange={onChange}
          onCommit={onCommit}
        />
      );
    default: {
      const exhaustive: never = kind;
      return exhaustive;
    }
  }
}

function RecordHop({ label, onClick }: RecordHopProps) {
  return (
    <Inline data-slot="record-hop" align="center" className={RECORD_HOP_CLASS}>
      <Button type="button" variant="ghost" size="compact" onClick={onClick}>
        <Icon icon={ArrowLeft} size="sm" />
        {label}
      </Button>
    </Inline>
  );
}

function ExpandableDescription({
  children,
  edit,
  layout = "subtitle",
}: {
  children: string;
  edit?: Omit<RecordInlineEditProps, "kind">;
  layout?: "fact" | "subtitle";
}) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const copy = edit !== undefined ? edit.value : children;
  const empty = copy.trim().length === 0;
  const label = empty ? (edit?.placeholder ?? children) : copy;

  function toggle() {
    if (open) {
      setEditing(false);
      setOpen(false);
      return;
    }
    setOpen(true);
    if (edit !== undefined) setEditing(true);
  }

  const fact = layout === "fact";
  const bodyClass = fact
    ? cn(
        FACT_DESCRIPTION_CLASS,
        open ? undefined : FACT_DESCRIPTION_SINGLE_CLASS,
        empty ? "text-ink-subtle" : undefined
      )
    : cn(
        SUBTITLE_CLASS,
        open ? "max-w-2xl whitespace-normal" : "max-w-xl truncate"
      );
  const copyClass = fact
    ? open
      ? "min-w-0 whitespace-normal text-pretty"
      : cn("w-full min-w-0", FACT_DESCRIPTION_CLAMP_CLASS)
    : undefined;

  if (editing && edit !== undefined) {
    return (
      <RecordInlineEdit
        kind="BODY"
        error={edit.error}
        label={edit.label}
        placeholder={edit.placeholder}
        value={edit.value}
        onCancel={() => {
          edit.onCancel();
          setEditing(false);
          setOpen(false);
        }}
        onChange={edit.onChange}
        onCommit={() => {
          edit.onCommit();
          setEditing(false);
        }}
      />
    );
  }

  return (
    <Box
      render={<button type="button" />}
      data-slot="frame-panel-description"
      aria-expanded={open}
      className={cn(SUBTITLE_TRIGGER_CLASS, bodyClass)}
      onClick={toggle}
    >
      {copyClass === undefined ? (
        label
      ) : (
        <Box render={<span />} className={copyClass}>
          {label}
        </Box>
      )}
    </Box>
  );
}

function CopyFactButton({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);

  function handleCopy() {
    void copyText(value).then((succeeded) => {
      if (succeeded) {
        setCopied(true);
        window.setTimeout(() => setCopied(false), COPIED_ACKNOWLEDGEMENT_MS);
      }
    });
  }

  return (
    <>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        data-slot="record-fact-copy"
        aria-label={`Copy ${label}`}
        className={FACT_COPY_CLASS}
        onClick={handleCopy}
      >
        <Icon icon={copied ? Check : Copy} size="sm" />
      </Button>
      <Box
        render={<span />}
        data-slot="record-fact-copy-announcement"
        aria-live="polite"
        className="sr-only"
      >
        {copied ? "Copied to clipboard" : null}
      </Box>
    </>
  );
}

function RecordFact({
  copyable = false,
  edit,
  expandable = false,
  icon,
  label,
  value,
}: RecordFactProps) {
  const text = typeof value === "string" ? value : null;
  const expands = (expandable || edit !== undefined) && text !== null;
  const copies = copyable && text !== null && !expands;
  const valueClass = cn(
    FACT_VALUE_CLASS,
    expands ? undefined : FACT_VALUE_SINGLE_CLASS
  );

  return (
    <Inline
      data-slot="record-fact"
      gap="sm"
      align="start"
      className={cn(FACT_ROW_CLASS, copies ? "group/fact" : undefined)}
    >
      <Box className={FACT_ICON_CLASS}>
        <Icon icon={icon} size="sm" className="text-ink-subtle" />
      </Box>
      <Box render={<span />} className={FACT_LABEL_CLASS}>
        {label}
      </Box>
      {copies && text !== null ? (
        <Inline gap="xs" align="center" className={valueClass}>
          <SchemaText value={text} />
          <CopyFactButton label={label} value={text} />
        </Inline>
      ) : (
        <Box className={valueClass}>
          {expands && text !== null ? (
            <ExpandableDescription edit={edit} layout="fact">
              {text}
            </ExpandableDescription>
          ) : text !== null ? (
            <SchemaText value={text} />
          ) : (
            value
          )}
        </Box>
      )}
    </Inline>
  );
}

function RecordHeader({
  actions,
  className,
  mark,
  meta,
  status,
  subtitle,
  title,
}: RecordHeaderProps) {
  const hasSupport = subtitle !== undefined || meta !== undefined;

  return (
    <Stack
      data-slot="record-header"
      data-testid="record-header"
      gap="lg"
      className={cn("w-full", className)}
    >
      <Stack gap="md" className="min-w-0">
        <Inline gap="md" align="center" justify="between" wrap className="min-w-0">
          <Inline gap="sm" align="center" className="min-h-control min-w-0 flex-1">
            {mark}
            <Inline gap="sm" align="center" className="min-w-0 flex-1">
              {typeof title === "string" ? (
                <FrameTitle className="truncate font-semibold">{title}</FrameTitle>
              ) : (
                <Box className="min-w-0 flex-1">{title}</Box>
              )}
              {status}
            </Inline>
          </Inline>
          {actions ? (
            <Inline gap="sm" align="center" className="shrink-0">
              {actions}
            </Inline>
          ) : null}
        </Inline>
        {hasSupport ? (
          <Stack gap="sm" className="min-w-0">
            {subtitle ? (
              <ExpandableDescription>{subtitle}</ExpandableDescription>
            ) : null}
            {meta ? <Box className="min-w-0">{meta}</Box> : null}
          </Stack>
        ) : null}
      </Stack>
      <Separator />
    </Stack>
  );
}

export {
  RecordFact,
  RecordHeader,
  RecordHop,
  RecordInlineEdit,
  RECORD_HEADER_RULES,
};
export type {
  RecordFactProps,
  RecordHeaderProps,
  RecordHopProps,
  RecordInlineEditKind,
  RecordInlineEditProps,
};
