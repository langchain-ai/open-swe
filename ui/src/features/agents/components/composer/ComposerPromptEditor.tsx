import { useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef } from "react";
import { LexicalComposer } from "@lexical/react/LexicalComposer";
import { useLexicalComposerContext } from "@lexical/react/LexicalComposerContext";
import { ContentEditable } from "@lexical/react/LexicalContentEditable";
import { LexicalErrorBoundary } from "@lexical/react/LexicalErrorBoundary";
import { HistoryPlugin } from "@lexical/react/LexicalHistoryPlugin";
import { OnChangePlugin } from "@lexical/react/LexicalOnChangePlugin";
import { PlainTextPlugin } from "@lexical/react/LexicalPlainTextPlugin";
import {
  $applyNodeReplacement,
  $createLineBreakNode,
  $createParagraphNode,
  $createRangeSelection,
  $createTextNode,
  $getRoot,
  $getSelection,
  $isElementNode,
  $isLineBreakNode,
  $isRangeSelection,
  $isTextNode,
  $setSelection,
  COMMAND_PRIORITY_HIGH,
  DecoratorNode,
  KEY_ARROW_DOWN_COMMAND,
  KEY_ARROW_UP_COMMAND,
  KEY_ENTER_COMMAND,
  KEY_ESCAPE_COMMAND,
  KEY_TAB_COMMAND,
} from "lexical";
import { File as FileIcon } from "lucide-react";

import { basenameOfPath, serializeComposerFileLink } from "./composerTrigger";
import { splitPromptIntoSegments } from "./composerMentions";
import type { InitialConfigType } from "@lexical/react/LexicalComposer";
import type { EditorState, LexicalNode, NodeKey, SerializedLexicalNode, Spread } from "lexical";
import { Tooltip, TooltipPopup, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export type ComposerCommandKey = "ArrowDown" | "ArrowUp" | "Enter" | "Tab" | "Escape";

export interface ComposerPromptEditorHandle {
  focus: () => void;
  focusAtEnd: () => void;
  /** The prompt as plain text plus the caret offset into it, read straight from the editor. */
  readSnapshot: () => { value: string; cursor: number };
}

const MENTION_CHIP_CLASS_NAME =
  "inline-flex max-w-full select-none items-center gap-1 rounded-md border border-border/70 bg-accent/40 px-1.5 py-px align-middle text-[12px] font-medium leading-[1.1] text-foreground";

type SerializedComposerMentionNode = Spread<
  { path: string; source: string; type: "composer-mention"; version: 1 },
  SerializedLexicalNode
>;

function ComposerMentionChip({ path }: { path: string }) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <span className={MENTION_CHIP_CLASS_NAME} contentEditable={false} spellCheck={false}>
            <FileIcon className="size-3.5 shrink-0 opacity-70" aria-hidden />
            <span className="select-none truncate leading-tight">{basenameOfPath(path)}</span>
          </span>
        }
      />
      <TooltipPopup className="max-w-[30rem] whitespace-normal break-words leading-tight" side="top">
        {path}
      </TooltipPopup>
    </Tooltip>
  );
}

/**
 * A file mention. The chip is only a presentation over the text the user
 * actually typed — `getTextContent` returns that source verbatim, so reading
 * the root's text content reconstructs the prompt exactly.
 */
class ComposerMentionNode extends DecoratorNode<React.ReactElement> {
  __path: string;
  __source: string;

  static override getType(): string {
    return "composer-mention";
  }

  static override clone(node: ComposerMentionNode): ComposerMentionNode {
    return new ComposerMentionNode(node.__path, node.__source, node.__key);
  }

  static override importJSON(serialized: SerializedComposerMentionNode): ComposerMentionNode {
    return $createComposerMentionNode(serialized.path, serialized.source).updateFromJSON(serialized);
  }

  constructor(path: string, source: string, key?: NodeKey) {
    super(key);
    this.__path = path;
    this.__source = source;
  }

  override exportJSON(): SerializedComposerMentionNode {
    return {
      ...super.exportJSON(),
      path: this.__path,
      source: this.__source,
      type: "composer-mention",
      version: 1,
    };
  }

  override createDOM(): HTMLElement {
    const dom = document.createElement("span");
    dom.className = "relative inline-flex align-middle leading-none";
    return dom;
  }

  override updateDOM(): false {
    return false;
  }

  override getTextContent(): string {
    return this.__source;
  }

  override isInline(): true {
    return true;
  }

  override decorate(): React.ReactElement {
    return <ComposerMentionChip path={this.__path} />;
  }
}

function $createComposerMentionNode(path: string, source: string): ComposerMentionNode {
  return $applyNodeReplacement(new ComposerMentionNode(path, source));
}

function isMentionNode(node: unknown): node is ComposerMentionNode {
  return node instanceof ComposerMentionNode;
}

function nodeTextLength(node: LexicalNode): number {
  if (isMentionNode(node)) return node.getTextContent().length;
  if ($isTextNode(node)) return node.getTextContentSize();
  if ($isLineBreakNode(node)) return 1;
  if ($isElementNode(node)) {
    return node.getChildren().reduce((sum, child) => sum + nodeTextLength(child), 0);
  }
  return 0;
}

function $rootTextLength(): number {
  return nodeTextLength($getRoot());
}

/**
 * Absolute offset of a selection point. Lexical addresses a point by node key
 * plus a local offset; the prompt is a flat string, so the two have to be
 * reconciled by walking the tree in document order.
 */
function offsetOfPoint(root: LexicalNode, key: NodeKey, localOffset: number): number | null {
  let offset = 0;
  let found: number | null = null;

  const walk = (node: LexicalNode): boolean => {
    if (node.getKey() === key) {
      if ($isTextNode(node)) {
        found = offset + Math.min(localOffset, node.getTextContentSize());
        return true;
      }
      if ($isElementNode(node)) {
        // An element point addresses a gap between children, so consume every
        // child that sits before it.
        const children = node.getChildren();
        let elementOffset = offset;
        for (let index = 0; index < Math.min(localOffset, children.length); index += 1) {
          elementOffset += nodeTextLength(children[index] as LexicalNode);
        }
        found = elementOffset;
        return true;
      }
      found = offset + (localOffset > 0 ? nodeTextLength(node) : 0);
      return true;
    }

    if ($isElementNode(node)) {
      for (const child of node.getChildren()) {
        if (walk(child)) return true;
      }
      return false;
    }

    offset += nodeTextLength(node);
    return false;
  };

  walk(root);
  return found;
}

interface SelectionPoint {
  key: NodeKey;
  offset: number;
  type: "text" | "element";
}

function findPointAtOffset(node: LexicalNode, remaining: { value: number }): SelectionPoint | null {
  if (isMentionNode(node)) {
    const size = nodeTextLength(node);
    const parent = node.getParent();
    if (!parent) return null;
    const index = node.getIndexWithinParent();
    // A chip is atomic: the caret may sit before or after it, never inside.
    if (remaining.value === 0) return { key: parent.getKey(), offset: index, type: "element" };
    if (remaining.value <= size) {
      remaining.value = 0;
      return { key: parent.getKey(), offset: index + 1, type: "element" };
    }
    remaining.value -= size;
    return null;
  }

  if ($isTextNode(node)) {
    const size = node.getTextContentSize();
    if (remaining.value <= size) return { key: node.getKey(), offset: remaining.value, type: "text" };
    remaining.value -= size;
    return null;
  }

  if ($isLineBreakNode(node)) {
    const parent = node.getParent();
    if (!parent) return null;
    const index = node.getIndexWithinParent();
    if (remaining.value === 0) return { key: parent.getKey(), offset: index, type: "element" };
    if (remaining.value === 1) {
      remaining.value = 0;
      return { key: parent.getKey(), offset: index + 1, type: "element" };
    }
    remaining.value -= 1;
    return null;
  }

  if ($isElementNode(node)) {
    const children = node.getChildren();
    for (const child of children) {
      const point = findPointAtOffset(child, remaining);
      if (point) return point;
    }
    if (remaining.value === 0) {
      return { key: node.getKey(), offset: children.length, type: "element" };
    }
  }

  return null;
}

function $setSelectionAtOffset(nextOffset: number): void {
  const root = $getRoot();
  const bounded = Math.max(0, Math.min(nextOffset, $rootTextLength()));
  const point = findPointAtOffset(root, { value: bounded }) ?? {
    key: root.getKey(),
    offset: root.getChildren().length,
    type: "element" as const,
  };
  const selection = $createRangeSelection();
  selection.anchor.set(point.key, point.offset, point.type);
  selection.focus.set(point.key, point.offset, point.type);
  $setSelection(selection);
}

function $readSelectionOffset(fallback: number): number {
  const selection = $getSelection();
  if (!$isRangeSelection(selection)) return fallback;
  const offset = offsetOfPoint($getRoot(), selection.anchor.key, selection.anchor.offset);
  return offset ?? fallback;
}

function $appendTextWithLineBreaks(parent: ReturnType<typeof $createParagraphNode>, text: string) {
  const lines = text.split("\n");
  lines.forEach((line, index) => {
    if (index > 0) parent.append($createLineBreakNode());
    if (line) parent.append($createTextNode(line));
  });
}

/** Rewrites the editor to match `value`, turning every mention it contains into a chip. */
function $setComposerPrompt(value: string): void {
  const root = $getRoot();
  root.clear();
  const paragraph = $createParagraphNode();
  for (const segment of splitPromptIntoSegments(value)) {
    if (segment.type === "text") {
      $appendTextWithLineBreaks(paragraph, segment.text);
    } else {
      paragraph.append($createComposerMentionNode(segment.path, segment.source));
    }
  }
  root.append(paragraph);
}

function CommandKeyPlugin({
  onCommandKeyDown,
}: {
  onCommandKeyDown?: (key: ComposerCommandKey, event: KeyboardEvent) => boolean;
}) {
  const handlerRef = useRef(onCommandKeyDown);
  useEffect(() => {
    handlerRef.current = onCommandKeyDown;
  }, [onCommandKeyDown]);

  const [editor] = useLexicalComposerContext();

  useEffect(() => {
    const handle = (key: ComposerCommandKey, event: KeyboardEvent | null): boolean => {
      const handler = handlerRef.current;
      if (!handler || !event) return false;
      // An IME composition ends on Enter; swallowing it here keeps the
      // composition from being submitted as a message.
      if (key === "Enter" && (event.isComposing || event.keyCode === 229)) {
        event.stopPropagation();
        return true;
      }
      const handled = handler(key, event);
      if (handled) {
        event.preventDefault();
        event.stopPropagation();
      }
      return handled;
    };

    const unregister = [
      editor.registerCommand(KEY_ARROW_DOWN_COMMAND, (e) => handle("ArrowDown", e), COMMAND_PRIORITY_HIGH),
      editor.registerCommand(KEY_ARROW_UP_COMMAND, (e) => handle("ArrowUp", e), COMMAND_PRIORITY_HIGH),
      editor.registerCommand(KEY_ENTER_COMMAND, (e) => handle("Enter", e), COMMAND_PRIORITY_HIGH),
      editor.registerCommand(KEY_TAB_COMMAND, (e) => handle("Tab", e), COMMAND_PRIORITY_HIGH),
      editor.registerCommand(KEY_ESCAPE_COMMAND, (e) => handle("Escape", e), COMMAND_PRIORITY_HIGH),
    ];
    return () => unregister.forEach((fn) => fn());
  }, [editor]);

  return null;
}

interface ComposerPromptEditorProps {
  value: string;
  /**
   * Where to put the caret after a programmatic `value` change (inserting a
   * mention, clearing a slash command). Ignored while the user types, so it
   * never fights the caret the browser already placed.
   */
  cursor?: number;
  disabled?: boolean;
  placeholder: string;
  className?: string;
  editorRef: React.RefObject<ComposerPromptEditorHandle | null>;
  onChange: (value: string, cursor: number) => void;
  onCommandKeyDown?: (key: ComposerCommandKey, event: KeyboardEvent) => boolean;
  onPaste?: React.ClipboardEventHandler<HTMLElement>;
}

function ComposerPromptEditorInner({
  value,
  cursor,
  disabled = false,
  placeholder,
  className,
  editorRef,
  onChange,
  onCommandKeyDown,
  onPaste,
}: ComposerPromptEditorProps) {
  const [editor] = useLexicalComposerContext();
  const onChangeRef = useRef(onChange);
  const snapshotRef = useRef({ value, cursor: value.length });
  // Set while a controlled `value` change is being written into the editor, so
  // the resulting OnChange doesn't echo back to the parent as a user edit.
  const applyingControlledUpdateRef = useRef(false);

  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);

  useEffect(() => {
    editor.setEditable(!disabled);
  }, [disabled, editor]);

  useLayoutEffect(() => {
    if (snapshotRef.current.value === value) return;
    const nextCursor = Math.max(0, Math.min(cursor ?? value.length, value.length));
    snapshotRef.current = { value, cursor: nextCursor };
    applyingControlledUpdateRef.current = true;
    editor.update(() => {
      $setComposerPrompt(value);
      $setSelectionAtOffset(nextCursor);
    });
    queueMicrotask(() => {
      applyingControlledUpdateRef.current = false;
    });
  }, [cursor, editor, value]);

  const readSnapshot = useCallback(() => {
    let snapshot = snapshotRef.current;
    editor.getEditorState().read(() => {
      const nextValue = $getRoot().getTextContent();
      snapshot = {
        value: nextValue,
        cursor: Math.min($readSelectionOffset(snapshotRef.current.cursor), nextValue.length),
      };
    });
    snapshotRef.current = snapshot;
    return snapshot;
  }, [editor]);

  const focusAt = useCallback(
    (cursor: number) => {
      const rootElement = editor.getRootElement();
      if (!rootElement) return;
      rootElement.focus({ preventScroll: true });
      editor.update(() => {
        $setSelectionAtOffset(cursor);
      });
    },
    [editor],
  );

  useImperativeHandle(
    editorRef,
    () => ({
      focus: () => focusAt(snapshotRef.current.cursor),
      focusAtEnd: () => focusAt(snapshotRef.current.value.length),
      readSnapshot,
    }),
    [focusAt, readSnapshot],
  );

  const handleEditorChange = useCallback((editorState: EditorState) => {
    editorState.read(() => {
      const nextValue = $getRoot().getTextContent();
      const nextCursor = Math.min(
        $readSelectionOffset(snapshotRef.current.cursor),
        nextValue.length,
      );
      const previous = snapshotRef.current;
      if (previous.value === nextValue && previous.cursor === nextCursor) return;
      snapshotRef.current = { value: nextValue, cursor: nextCursor };
      if (applyingControlledUpdateRef.current) return;
      onChangeRef.current(nextValue, nextCursor);
    });
  }, []);

  return (
    <div className="relative">
      <PlainTextPlugin
        ErrorBoundary={LexicalErrorBoundary}
        contentEditable={
          <ContentEditable
            aria-label="Message"
            aria-placeholder={placeholder}
            className={cn(
              "block max-h-50 w-full overflow-y-auto break-words whitespace-pre-wrap bg-transparent text-[13px] leading-relaxed text-foreground focus:outline-none",
              className,
            )}
            data-testid="composer-editor"
            onPaste={onPaste}
            placeholder={<span />}
          />
        }
        placeholder={
          <div className="pointer-events-none absolute inset-0 text-[13px] leading-relaxed text-muted-foreground/60">
            {placeholder}
          </div>
        }
      />
      <OnChangePlugin onChange={handleEditorChange} />
      <CommandKeyPlugin {...(onCommandKeyDown ? { onCommandKeyDown } : {})} />
      <HistoryPlugin />
    </div>
  );
}

export function ComposerPromptEditor(props: ComposerPromptEditorProps) {
  const initialValueRef = useRef(props.value);
  const initialConfig = useMemo<InitialConfigType>(
    () => ({
      namespace: "open-swe-composer",
      editable: true,
      nodes: [ComposerMentionNode],
      editorState: () => {
        $setComposerPrompt(initialValueRef.current);
      },
      onError: (error: Error) => {
        throw error;
      },
    }),
    [],
  );

  return (
    <LexicalComposer initialConfig={initialConfig}>
      <ComposerPromptEditorInner {...props} />
    </LexicalComposer>
  );
}

/** Builds the text a mention insertion replaces the trigger with (chip source + trailing space). */
export function mentionReplacementText(path: string): string {
  return `${serializeComposerFileLink(path)} `;
}
