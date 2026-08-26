import { useEffect, useMemo, useRef } from "react"

import type { PlanComment, PlanTextAnchor } from "@/lib/plan"
import { SandboxedHtmlFrame } from "@/features/agents/components/SandboxedHtmlFrame"
import { useResolvedTheme } from "@/lib/theme"
import { cn } from "@/lib/utils"

function frameScript(channel: string): string {
  return `(() => {
    const channel = ${JSON.stringify(channel)};
    const markers = new Map();
    const ranges = new Map();
    const ignored = new Set(["SCRIPT", "STYLE", "NOSCRIPT"]);
    let port = null;
    document.documentElement.dataset.annotationReady = "true";

    function post(message) {
      port?.postMessage(message);
    }

    function textNodes() {
      const nodes = [];
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
        acceptNode(node) {
          const parent = node.parentElement;
          return parent && !ignored.has(parent.tagName) && !parent.closest("[data-plan-annotation]")
            ? NodeFilter.FILTER_ACCEPT
            : NodeFilter.FILTER_REJECT;
        }
      });
      while (walker.nextNode()) nodes.push(walker.currentNode);
      return nodes;
    }

    function documentText(nodes) {
      return nodes.map(node => node.data).join("");
    }

    function offsetFor(nodes, target, offset) {
      let position = 0;
      for (const node of nodes) {
        if (node === target) return position + offset;
        position += node.data.length;
      }
      return -1;
    }

    function pointAt(nodes, offset) {
      let position = 0;
      for (const node of nodes) {
        const next = position + node.data.length;
        if (offset <= next) return [node, Math.max(0, offset - position)];
        position = next;
      }
      const last = nodes[nodes.length - 1];
      return last ? [last, last.data.length] : null;
    }

    function rangeAt(anchor) {
      const nodes = textNodes();
      const text = documentText(nodes);
      let start = anchor.start;
      if (text.slice(start, anchor.end) !== anchor.exact) {
        let best = null;
        let from = 0;
        while (from <= text.length) {
          const match = text.indexOf(anchor.exact, from);
          if (match < 0) break;
          const prefix = text.slice(Math.max(0, match - anchor.prefix.length), match);
          const suffix = text.slice(match + anchor.exact.length, match + anchor.exact.length + anchor.suffix.length);
          const score = (prefix === anchor.prefix ? 1000000 : 0) +
            (suffix === anchor.suffix ? 1000000 : 0) - Math.abs(match - anchor.start);
          if (!best || score > best.score) best = { start: match, score };
          from = match + Math.max(1, anchor.exact.length);
        }
        if (!best) return null;
        start = best.start;
      }
      const startPoint = pointAt(nodes, start);
      const endPoint = pointAt(nodes, start + anchor.exact.length);
      if (!startPoint || !endPoint) return null;
      const range = document.createRange();
      range.setStart(startPoint[0], startPoint[1]);
      range.setEnd(endPoint[0], endPoint[1]);
      return range;
    }

    function clearAnnotations() {
      CSS.highlights?.delete("plan-comments");
      for (const marker of markers.values()) marker.remove();
      markers.clear();
      ranges.clear();
    }

    function placeMarkers() {
      for (const [id, marker] of markers) {
        const range = ranges.get(id);
        const rects = range ? Array.from(range.getClientRects()) : [];
        const rect = rects[rects.length - 1];
        if (!rect) {
          marker.hidden = true;
          continue;
        }
        marker.hidden = false;
        marker.style.left = Math.min(window.innerWidth - 30, rect.right + 6) + "px";
        marker.style.top = Math.max(4, rect.top - 2) + "px";
      }
    }

    function setAnnotations(comments) {
      clearAnnotations();
      comments.forEach((comment, index) => {
        const range = rangeAt(comment.anchor);
        if (!range) return;
        const marker = document.createElement("button");
        marker.type = "button";
        marker.dataset.planAnnotation = "";
        marker.className = "plan-annotation-marker";
        marker.textContent = String(index + 1);
        marker.title = "Open comment " + (index + 1);
        marker.addEventListener("click", () => post({ type: "comment-selected", id: comment.id }));
        document.body.append(marker);
        markers.set(comment.id, marker);
        ranges.set(comment.id, range);
      });
      if (CSS.highlights && window.Highlight && ranges.size) {
        CSS.highlights.set("plan-comments", new Highlight(...ranges.values()));
      }
      placeMarkers();
    }

    function selectedAnchor() {
      const selection = getSelection();
      if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return null;
      const range = selection.getRangeAt(0);
      if (!document.body.contains(range.startContainer) || !document.body.contains(range.endContainer)) return null;
      const exact = range.toString();
      if (!exact.trim() || exact.length > 1000) return null;
      const nodes = textNodes();
      const text = documentText(nodes);
      const before = document.createRange();
      before.selectNodeContents(document.body);
      before.setEnd(range.startContainer, range.startOffset);
      const estimate = before.toString().length;
      let start = text.indexOf(exact);
      for (let match = start; match >= 0; match = text.indexOf(exact, match + 1)) {
        if (Math.abs(match - estimate) < Math.abs(start - estimate)) start = match;
      }
      if (start < 0) return null;
      const end = start + exact.length;
      return {
        exact,
        prefix: text.slice(Math.max(0, start - 64), start),
        suffix: text.slice(end, end + 64),
        start,
        end
      };
    }

    document.addEventListener("mouseup", () => {
      setTimeout(() => {
        const anchor = selectedAnchor();
        if (anchor) post({ type: "text-selected", anchor });
      });
    });

    function receive(message) {
      if (message.type === "set-comments" && Array.isArray(message.comments)) {
        setAnnotations(message.comments);
      }
      if (message.type === "focus-comment" && typeof message.id === "string") {
        const range = ranges.get(message.id);
        const marker = markers.get(message.id);
        if (range) range.startContainer.parentElement?.scrollIntoView({ behavior: "smooth", block: "center" });
        if (marker) {
          marker.classList.remove("plan-annotation-marker-active");
          requestAnimationFrame(() => marker.classList.add("plan-annotation-marker-active"));
        }
      }
    }

    addEventListener("message", event => {
      if (event.source !== parent || !event.data || event.data.channel !== channel || !event.ports[0]) return;
      port = event.ports[0];
      port.onmessage = portEvent => receive(portEvent.data || {});
      port.start();
      post({ type: "ready" });
    }, { once: true });

    addEventListener("scroll", placeMarkers, { passive: true });
    addEventListener("resize", placeMarkers);
    new ResizeObserver(placeMarkers).observe(document.body);
  })();`
}

function withViewerPolicy(
  html: string,
  theme: "light" | "dark",
  channel: string
): string {
  const nonce = channel
  const policy = [
    "default-src 'none'",
    `script-src 'nonce-${nonce}'`,
    "style-src 'unsafe-inline' https://fonts.googleapis.com",
    "font-src https://fonts.gstatic.com data:",
    "img-src data:",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-src 'none'",
    "connect-src 'none'",
  ].join("; ")
  const head = `<meta http-equiv="Content-Security-Policy" content="${policy}"><style>::highlight(plan-comments){background:#facc15;color:inherit}.plan-annotation-marker{position:fixed;z-index:2147483647;width:24px;height:24px;padding:0;border:2px solid white;border-radius:999px;background:#2563eb;color:white;font:700 12px/20px system-ui;box-shadow:0 2px 8px #0005;cursor:pointer}.plan-annotation-marker-active{animation:plan-comment-pulse 1.2s ease-out}@keyframes plan-comment-pulse{0%{box-shadow:0 0 0 0 #2563ebaa}100%{box-shadow:0 0 0 12px transparent}}</style>`
  const script = `<script nonce="${nonce}">${frameScript(channel)}</script>`
  const themed = html
    .replace(
      /<meta\s+[^>]*http-equiv\s*=\s*["']?Content-Security-Policy["']?[^>]*>/gi,
      ""
    )
    .replace(
      /<html(?=\s|>)/i,
      `<html data-theme="${theme}" data-viewer-theme="${theme}"`
    )
  const withHead = /<head(?=\s|>)/i.test(themed)
    ? themed.replace(/<head([^>]*)>/i, `<head$1>${head}`)
    : `<!doctype html><html data-theme="${theme}" data-viewer-theme="${theme}"><head>${head}</head><body>${themed}</body></html>`
  return /<\/body\s*>/i.test(withHead)
    ? withHead.replace(/<\/body\s*>/i, `${script}</body>`)
    : `${withHead}${script}`
}

function isAnchor(value: unknown): value is PlanTextAnchor {
  if (!value || typeof value !== "object") return false
  const anchor = value as Record<string, unknown>
  return (
    typeof anchor.exact === "string" &&
    anchor.exact.length > 0 &&
    anchor.exact.length <= 1000 &&
    typeof anchor.prefix === "string" &&
    typeof anchor.suffix === "string" &&
    typeof anchor.start === "number" &&
    typeof anchor.end === "number" &&
    anchor.start >= 0 &&
    anchor.end - anchor.start === anchor.exact.length
  )
}

export function PlanArtifactFrame({
  html,
  comments = [],
  onTextSelected,
  onCommentSelected,
  focusCommentId,
  focusCommentKey = 0,
  title = "Plan artifact",
  className,
}: {
  html: string
  comments?: Array<PlanComment>
  onTextSelected?: (anchor: PlanTextAnchor) => void
  onCommentSelected?: (id: string) => void
  focusCommentId?: string | null
  focusCommentKey?: number
  title?: string
  className?: string
}) {
  const theme = useResolvedTheme()
  const frameRef = useRef<HTMLIFrameElement>(null)
  const portRef = useRef<MessagePort | null>(null)
  const channel = useMemo(() => globalThis.crypto.randomUUID(), [])
  const srcDoc = useMemo(
    () => withViewerPolicy(html, theme, channel),
    [channel, html, theme]
  )
  const anchoredComments = useMemo(
    () =>
      comments
        .filter(
          (comment): comment is PlanComment & { anchor: PlanTextAnchor } =>
            isAnchor(comment.anchor)
        )
        .map(({ id, anchor }) => ({ id, anchor })),
    [comments]
  )
  const commentsRef = useRef(anchoredComments)
  useEffect(() => {
    commentsRef.current = anchoredComments
  }, [anchoredComments])

  const connect = () => {
    portRef.current?.close()
    const messageChannel = new MessageChannel()
    portRef.current = messageChannel.port1
    messageChannel.port1.onmessage = (event: MessageEvent) => {
      if (event.data?.type === "ready")
        portRef.current?.postMessage({
          type: "set-comments",
          comments: commentsRef.current,
        })
      if (event.data?.type === "text-selected" && isAnchor(event.data.anchor))
        onTextSelected?.(event.data.anchor)
      if (
        event.data?.type === "comment-selected" &&
        typeof event.data.id === "string"
      )
        onCommentSelected?.(event.data.id)
    }
    messageChannel.port1.start()
    frameRef.current?.contentWindow?.postMessage(
      { type: "connect-annotations", channel },
      "*",
      [messageChannel.port2]
    )
  }

  useEffect(
    () => () => {
      portRef.current?.close()
      portRef.current = null
    },
    []
  )

  useEffect(() => {
    portRef.current?.postMessage({
      type: "set-comments",
      comments: anchoredComments,
    })
  }, [anchoredComments])

  useEffect(() => {
    if (!focusCommentId) return
    portRef.current?.postMessage({ type: "focus-comment", id: focusCommentId })
  }, [focusCommentId, focusCommentKey])

  return (
    <SandboxedHtmlFrame
      ref={frameRef}
      testId="plan-artifact-frame"
      title={title}
      html={srcDoc}
      sandbox="allow-scripts"
      onLoad={connect}
      className={cn("bg-background", className)}
    />
  )
}
