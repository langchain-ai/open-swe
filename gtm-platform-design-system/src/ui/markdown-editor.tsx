"use client";

/*
 * Rich Markdown editor. The surface is TipTap so play prompts edit like
 * formatted text; onChange still emits the Markdown string the agent executes.
 * Color, highlight, and alignment are not offered because they cannot survive
 * that Markdown write.
 */

import { Placeholder } from "@tiptap/extension-placeholder";
import { EditorContent, useEditor, type Editor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { Box, Inline, Stack } from "./box";
import { Button } from "./button";
import { cn } from "./cn";
import { ExternalLink, List, MessageSquare, Terminal } from "./glyphs";
import { Icon } from "./icon";
import { Input } from "./input";
import { htmlToMarkdown, markdownToHtml } from "./markdown-codec";
import { Popover, PopoverContent, PopoverTrigger } from "./popover";
import { ScrollArea } from "./scroll-area";
import { Tooltip, TooltipContent, TooltipTrigger } from "./tooltip";

const EDITOR_CLASS =
  "w-full overflow-hidden rounded-control border border-line-strong bg-muted";
const EDITOR_FOCUS_CLASS = "focus-within:ring-2 focus-within:ring-primary";

const SURFACE_CLASS = cn(
  "px-3 py-3 text-body text-ink",
  "[&_.tiptap]:min-h-40 [&_.tiptap]:outline-none",
  "[&_.tiptap_p]:my-3 [&_.tiptap_p]:last:mb-0",
  "[&_.tiptap_h2]:mt-4 [&_.tiptap_h2]:mb-2 [&_.tiptap_h2]:text-title [&_.tiptap_h2]:font-medium",
  "[&_.tiptap_h3]:mt-3 [&_.tiptap_h3]:mb-2 [&_.tiptap_h3]:text-title [&_.tiptap_h3]:font-medium",
  "[&_.tiptap_ul]:my-3 [&_.tiptap_ul]:list-disc [&_.tiptap_ul]:pl-5",
  "[&_.tiptap_ol]:my-3 [&_.tiptap_ol]:list-decimal [&_.tiptap_ol]:pl-5",
  "[&_.tiptap_blockquote]:my-3 [&_.tiptap_blockquote]:border-l-2 [&_.tiptap_blockquote]:border-line-strong [&_.tiptap_blockquote]:pl-3 [&_.tiptap_blockquote]:text-ink-muted",
  "[&_.tiptap_code]:rounded-badge [&_.tiptap_code]:bg-hover [&_.tiptap_code]:px-1 [&_.tiptap_code]:font-mono [&_.tiptap_code]:text-meta",
  "[&_.tiptap_pre]:my-3 [&_.tiptap_pre]:rounded-compact [&_.tiptap_pre]:bg-hover [&_.tiptap_pre]:p-3 [&_.tiptap_pre]:font-mono [&_.tiptap_pre]:text-meta",
  "[&_.tiptap_a]:text-primary [&_.tiptap_a]:underline"
);

interface MarkdownEditorHandle {
  editor: Editor;
  setValue: (markdown: string) => void;
}

interface MarkdownEditorProps {
  value: string;
  onChange: (value: string) => void;
  onBlur?: () => void;
  disabled?: boolean;
  placeholder?: string;
  maxLength?: number;
  invalid?: boolean;
  describedBy?: string;
  id?: string;
  "aria-label"?: string;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean;
  "aria-required"?: boolean;
  /** Remove nested field chrome when the editor already sits in a Frame panel. */
  flush?: boolean;
  /**
   * Docs chrome: formatting bar flush to the host top, reading column on the
   * page body only. The editor fills its parent and owns the scroll.
   */
  attached?: boolean;
  className?: string;
  /** `document` is the writing surface for a prompt step. `field` is a compact slot. */
  size?: "field" | "document";
}

const EDITORS = new WeakMap<HTMLElement, MarkdownEditorHandle>();
const WEB_SCHEME = /^[a-z][a-z\d+.-]*:/i;
const INVALID_URL_CHARACTERS = /[\u0000-\u0020\u007f]/;

function normalizeWebHref(value: string): string | null {
  const candidate = value.startsWith("//")
    ? `https:${value}`
    : WEB_SCHEME.test(value)
      ? value
      : `https://${value}`;
  if (
    INVALID_URL_CHARACTERS.test(candidate) ||
    /^https?:\/\/https?:\/\//i.test(candidate)
  ) {
    return null;
  }
  try {
    const parsed = new URL(candidate);
    if (
      (parsed.protocol !== "http:" && parsed.protocol !== "https:") ||
      parsed.hostname.length === 0
    ) {
      return null;
    }
    return parsed.href;
  } catch {
    return null;
  }
}

function requireEditorHandle(from: HTMLElement): MarkdownEditorHandle {
  const root = from.closest("[data-markdown-editor]");
  if (!(root instanceof HTMLElement)) {
    throw new Error("Prompt editor is not mounted");
  }
  const handle = EDITORS.get(root);
  if (handle === undefined) {
    throw new Error("Prompt editor is not ready");
  }
  return handle;
}

function setMarkdownEditorValue(from: HTMLElement, markdown: string): void {
  requireEditorHandle(from).setValue(markdown);
}

function getMarkdownEditor(from: HTMLElement): Editor {
  return requireEditorHandle(from).editor;
}

function MarkdownToolbarButton({
  children,
  disabled,
  label,
  onClick,
  pressed,
}: {
  children: ReactNode;
  disabled: boolean;
  label: string;
  onClick: () => void;
  pressed: boolean;
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            type="button"
            variant={pressed ? "outline" : "ghost"}
            size="icon-sm"
            aria-label={label}
            aria-pressed={pressed}
            disabled={disabled}
            onClick={onClick}
          />
        }
      >
        {children}
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}

function MarkdownLinkControl({
  disabled,
  editor,
}: {
  disabled: boolean;
  editor: Editor | null;
}) {
  const [open, setOpen] = useState(false);
  const [href, setHref] = useState("");
  const [error, setError] = useState<string | null>(null);
  const errorId = useId();
  const pressed = editor?.isActive("link") ?? false;

  function apply() {
    if (editor === null) return;
    const next = href.trim();
    if (next.length === 0) {
      editor.chain().focus().extendMarkRange("link").unsetLink().run();
    } else {
      const normalized = normalizeWebHref(next);
      if (normalized === null) {
        setError("Enter a valid http or https URL.");
        return;
      }
      editor.chain().focus().extendMarkRange("link").setLink({ href: normalized }).run();
    }
    setOpen(false);
  }

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        if (next && editor !== null) {
          const current = editor.getAttributes("link").href;
          setHref(typeof current === "string" && current.length > 0 ? current : "");
          setError(null);
        }
        setOpen(next);
      }}
    >
      <Tooltip>
        <TooltipTrigger
          render={
            <PopoverTrigger
              render={
                <Button
                  type="button"
                  variant={pressed ? "outline" : "ghost"}
                  size="icon-sm"
                  aria-label="Link"
                  aria-pressed={pressed}
                  disabled={disabled || editor === null}
                />
              }
            />
          }
        >
          <Icon icon={ExternalLink} size="sm" />
        </TooltipTrigger>
        <TooltipContent>Link</TooltipContent>
      </Tooltip>
      <PopoverContent align="start" className="w-72">
        <Stack gap="sm">
          <Input
            aria-label="Link address"
            aria-describedby={error === null ? undefined : errorId}
            aria-invalid={error === null ? undefined : true}
            placeholder="https://example.com"
            value={href}
            onChange={(event) => {
              setHref(event.target.value);
              setError(null);
            }}
          />
          {error === null ? null : (
            <Box render={<p />} id={errorId} className="text-meta text-risk">
              {error}
            </Box>
          )}
          <Button type="button" size="compact" onClick={apply}>
            Apply link
          </Button>
        </Stack>
      </PopoverContent>
    </Popover>
  );
}

function MarkdownEditorToolbar({
  attached,
  disabled,
  editor,
  flush,
}: {
  attached: boolean;
  disabled: boolean;
  editor: Editor | null;
  flush: boolean;
}) {
  const ready = editor !== null && !disabled;
  return (
    <Inline
      render={<Box role="toolbar" aria-label="Markdown formatting" />}
      gap="xs"
      align="center"
      wrap
      className={cn(
        "min-h-toolbar border-b border-line py-1",
        flush ? "px-0" : "px-2",
        attached && "shrink-0 bg-shell"
      )}
    >
      <MarkdownToolbarButton
        label="Bold"
        pressed={editor?.isActive("bold") ?? false}
        disabled={!ready}
        onClick={() => editor?.chain().focus().toggleBold().run()}
      >
        <Box render={<span />} className="text-label font-semibold">
          B
        </Box>
      </MarkdownToolbarButton>
      <MarkdownToolbarButton
        label="Italic"
        pressed={editor?.isActive("italic") ?? false}
        disabled={!ready}
        onClick={() => editor?.chain().focus().toggleItalic().run()}
      >
        <Box render={<span />} className="text-label italic">
          I
        </Box>
      </MarkdownToolbarButton>
      <MarkdownToolbarButton
        label="Strikethrough"
        pressed={editor?.isActive("strike") ?? false}
        disabled={!ready}
        onClick={() => editor?.chain().focus().toggleStrike().run()}
      >
        <Box render={<span />} className="text-label line-through">
          S
        </Box>
      </MarkdownToolbarButton>
      <Box className="h-control-sm w-px bg-line" aria-hidden />
      <MarkdownToolbarButton
        label="H2"
        pressed={editor?.isActive("heading", { level: 2 }) ?? false}
        disabled={!ready}
        onClick={() => editor?.chain().focus().toggleHeading({ level: 2 }).run()}
      >
        <Box render={<span />} className="text-label font-semibold">
          H2
        </Box>
      </MarkdownToolbarButton>
      <MarkdownToolbarButton
        label="H3"
        pressed={editor?.isActive("heading", { level: 3 }) ?? false}
        disabled={!ready}
        onClick={() => editor?.chain().focus().toggleHeading({ level: 3 }).run()}
      >
        <Box render={<span />} className="text-label font-semibold">
          H3
        </Box>
      </MarkdownToolbarButton>
      <MarkdownToolbarButton
        label="Bullets"
        pressed={editor?.isActive("bulletList") ?? false}
        disabled={!ready}
        onClick={() => editor?.chain().focus().toggleBulletList().run()}
      >
        <Icon icon={List} size="sm" />
      </MarkdownToolbarButton>
      <MarkdownToolbarButton
        label="Numbered"
        pressed={editor?.isActive("orderedList") ?? false}
        disabled={!ready}
        onClick={() => editor?.chain().focus().toggleOrderedList().run()}
      >
        <Box render={<span />} className="text-meta font-medium">
          1.
        </Box>
      </MarkdownToolbarButton>
      <MarkdownToolbarButton
        label="Quote"
        pressed={editor?.isActive("blockquote") ?? false}
        disabled={!ready}
        onClick={() => editor?.chain().focus().toggleBlockquote().run()}
      >
        <Icon icon={MessageSquare} size="sm" />
      </MarkdownToolbarButton>
      <MarkdownToolbarButton
        label="Code"
        pressed={editor?.isActive("code") ?? false}
        disabled={!ready}
        onClick={() => editor?.chain().focus().toggleCode().run()}
      >
        <Icon icon={Terminal} size="sm" />
      </MarkdownToolbarButton>
      <Box className="h-control-sm w-px bg-line" aria-hidden />
      <MarkdownLinkControl disabled={disabled} editor={editor} />
    </Inline>
  );
}

function editorSurfaceMin(size: "field" | "document"): string {
  switch (size) {
    case "document":
      return "min-h-72";
    case "field":
      return "min-h-48";
    default: {
      const _exhaustive: never = size;
      return _exhaustive;
    }
  }
}

function MarkdownEditor({
  "aria-label": ariaLabel = "Prompt",
  "aria-describedby": ariaDescribedBy,
  "aria-invalid": ariaInvalid,
  "aria-required": ariaRequired,
  attached = false,
  className,
  disabled = false,
  describedBy,
  flush = false,
  size = "field",
  id,
  invalid = false,
  maxLength,
  onBlur,
  onChange,
  placeholder = "Write the play prompt",
  value,
}: MarkdownEditorProps) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const onChangeRef = useRef(onChange);
  const onBlurRef = useRef(onBlur);

  useEffect(() => {
    onChangeRef.current = onChange;
    onBlurRef.current = onBlur;
  }, [onBlur, onChange]);

  const editor = useEditor({
    immediatelyRender: false,
    shouldRerenderOnTransaction: true,
    extensions: [
      StarterKit.configure({
        heading: { levels: [2, 3] },
        horizontalRule: false,
        link: {
          openOnClick: false,
          autolink: true,
          defaultProtocol: "https",
        },
      }),
      Placeholder.configure({ placeholder }),
    ],
    content: markdownToHtml(value),
    editable: !disabled,
    editorProps: {
      attributes: {
        role: "textbox",
        "aria-multiline": "true",
        class: "tiptap",
        "aria-label": ariaLabel,
        ...(id === undefined ? {} : { id }),
        ...(ariaInvalid === true || invalid ? { "aria-invalid": "true" } : {}),
        ...((ariaDescribedBy ?? describedBy) === undefined
          ? {}
          : { "aria-describedby": ariaDescribedBy ?? describedBy ?? "" }),
        ...(ariaRequired === true ? { "aria-required": "true" } : {}),
        ...(maxLength === undefined ? {} : { maxlength: String(maxLength) }),
      },
    },
    onUpdate: ({ editor: next }) => {
      onChangeRef.current(htmlToMarkdown(next.getHTML()));
    },
    onBlur: () => {
      onBlurRef.current?.();
    },
  });

  useEffect(() => {
    if (editor === null) return;
    editor.setEditable(!disabled);
  }, [disabled, editor]);

  useEffect(() => {
    if (editor === null) return;
    const current = htmlToMarkdown(editor.getHTML());
    if (current === value.trim()) return;
    editor.commands.setContent(markdownToHtml(value), { emitUpdate: false });
  }, [editor, value]);

  useEffect(() => {
    const root = rootRef.current;
    if (root === null || editor === null) return;
    EDITORS.set(root, {
      editor,
      setValue: (markdown: string) => {
        editor.commands.setContent(markdownToHtml(markdown), { emitUpdate: false });
        onChangeRef.current(markdown);
      },
    });
    return () => {
      EDITORS.delete(root);
    };
  }, [editor]);

  const page = attached || flush;
  const surface = (
    <Box
      className={cn(
        SURFACE_CLASS,
        attached
          ? "mx-auto w-full max-w-reading px-6 py-6"
          : cn(editorSurfaceMin(size), flush && "px-0 py-0")
      )}
    >
      <EditorContent editor={editor} />
    </Box>
  );

  return (
    <Stack
      ref={rootRef}
      gap="none"
      data-slot="markdown-editor"
      data-markdown-editor=""
      data-chrome={attached ? "attached" : undefined}
      className={cn(
        EDITOR_CLASS,
        page ? "rounded-none border-0 bg-transparent" : EDITOR_FOCUS_CLASS,
        flush && !attached && "first:-mt-(--frame-panel-pad)",
        attached && "h-full min-h-0",
        className
      )}
    >
      <MarkdownEditorToolbar
        attached={attached}
        disabled={disabled}
        editor={editor}
        flush={flush && !attached}
      />
      {attached ? (
        <ScrollArea className="min-h-0 flex-1">{surface}</ScrollArea>
      ) : (
        surface
      )}
      {maxLength === undefined ? null : (
        <Box
          render={<p />}
          className={cn(
            "border-t border-line px-3 py-2 text-meta text-ink-subtle",
            attached && "shrink-0 bg-shell"
          )}
        >
          {`${Math.max(0, maxLength - value.length).toLocaleString("en-US")} characters left`}
        </Box>
      )}
    </Stack>
  );
}

export { getMarkdownEditor, MarkdownEditor, setMarkdownEditorValue };
export type { MarkdownEditorProps };
