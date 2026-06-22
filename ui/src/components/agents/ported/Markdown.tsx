import { Component, memo, useMemo } from "react";
import { Streamdown } from "streamdown";
import type { ReactNode } from "react";
import "streamdown/styles.css";

interface MarkdownProps {
  content: string;
  /** When true, keep Streamdown in streaming mode for the duration of the run. */
  isLive?: boolean;
}

/**
 * Must stay referentially and structurally stable while a message is streaming.
 * Streamdown keys its animate plugin on JSON.stringify(animated); changing
 * stagger/duration recreates the plugin, resets prevContentLength, and
 * re-animates text that was already on screen.
 */
const STREAMDOWN_ANIMATED = {
  sep: "word",
  animation: "slideUp",
  duration: 60,
  stagger: 10,
  easing: "ease-out",
} as const;

const STREAMDOWN_COMPONENTS = {
  h1: ({ children }: { children?: ReactNode }) => (
    <div className="text-[color:var(--ui-text)] text-[15px] font-medium mt-4 mb-1.5 tracking-tight">
      {children}
    </div>
  ),
  h2: ({ children }: { children?: ReactNode }) => (
    <div className="text-[color:var(--ui-text)] text-[14px] font-medium mt-3 mb-1.5 tracking-tight">
      {children}
    </div>
  ),
  h3: ({ children }: { children?: ReactNode }) => (
    <div className="text-[color:var(--ui-text)] text-[13px] font-medium mt-3 mb-1">
      {children}
    </div>
  ),
  p: ({ children }: { children?: ReactNode }) => (
    <p className="my-1.5 text-[color:var(--ui-text)] break-words [overflow-wrap:anywhere]">
      {children}
    </p>
  ),
  ul: ({ children }: { children?: ReactNode }) => (
    <div className="my-1.5 ml-4 min-w-0">{children}</div>
  ),
  ol: ({ children }: { children?: ReactNode }) => (
    <div className="my-1.5 ml-4 min-w-0">{children}</div>
  ),
  li: ({ children }: { children?: ReactNode }) => (
    <div className="text-[color:var(--ui-text)] break-words [overflow-wrap:anywhere] [&>p]:inline [&>p]:my-0">
      - {children}
    </div>
  ),
  blockquote: ({ children }: { children?: ReactNode }) => (
    <div className="my-2 border-l-2 border-[var(--ui-border)] pl-3 text-[color:var(--ui-text-muted)] break-words [overflow-wrap:anywhere]">
      {children}
    </div>
  ),
  hr: () => <hr className="border-[var(--ui-border-subtle)] my-3" />,
  code: ({ className, children }: { className?: string; children?: ReactNode }) => {
    const text = String(children);
    const match = /language-([^\s]+)/.exec(className || "");
    const isBlock = match || text.includes("\n");
    if (isBlock) return <code className={className}>{children}</code>;
    return (
      <code className="rounded-md bg-[var(--ui-panel-2)] px-1.5 py-0.5 font-mono text-[0.85em] text-[color:var(--ui-accent)] whitespace-pre-wrap break-words [overflow-wrap:anywhere]">
        {text}
      </code>
    );
  },
};

const SHIKI_THEME: ["github-light", "github-dark"] = ["github-light", "github-dark"];

interface BoundaryProps {
  content: string;
  children: ReactNode;
}

interface BoundaryState {
  failed: boolean;
  key: string;
}

// Streamdown bundles Mermaid and renders ```mermaid blocks itself; a diagram it
// can't parse throws during render and, with no boundary, white-screens the
// whole page. Contain it and fall back to the raw markdown text.
class MarkdownErrorBoundary extends Component<BoundaryProps, BoundaryState> {
  state: BoundaryState = { failed: false, key: this.props.content };

  static getDerivedStateFromError(): Partial<BoundaryState> {
    return { failed: true };
  }

  static getDerivedStateFromProps(
    props: BoundaryProps,
    state: BoundaryState
  ): Partial<BoundaryState> | null {
    if (props.content !== state.key) return { failed: false, key: props.content };
    return null;
  }

  render(): ReactNode {
    if (this.state.failed) {
      return (
        <pre className="whitespace-pre-wrap break-words [overflow-wrap:anywhere] font-sans text-[color:var(--ui-text)]">
          {this.props.content}
        </pre>
      );
    }
    return this.props.children;
  }
}

export const Markdown = memo(function Markdown({
  content,
  isLive = false,
}: MarkdownProps) {
  const components = useMemo(
    () => ({
      ...STREAMDOWN_COMPONENTS,
      a: ({ href, children }: { href?: string; children?: ReactNode }) => (
        <a
          className="text-[color:var(--ui-accent)] underline decoration-[color:var(--ui-accent)]/50 break-words [overflow-wrap:anywhere]"
          href={href}
          target="_blank"
          rel="noreferrer"
        >
          {children}
        </a>
      ),
    }),
    []
  );

  return (
    <div className="min-w-0 max-w-full text-[13px] leading-6 break-words [overflow-wrap:anywhere] [&_.streamdown]:text-[color:var(--ui-text)]">
      <MarkdownErrorBoundary content={content}>
        <Streamdown
          mode={isLive ? "streaming" : "static"}
          parseIncompleteMarkdown={isLive}
          isAnimating={isLive}
          animated={isLive ? STREAMDOWN_ANIMATED : false}
          shikiTheme={SHIKI_THEME}
          className="streamdown-agent min-w-0 max-w-full"
          components={components}
        >
          {content}
        </Streamdown>
      </MarkdownErrorBoundary>
    </div>
  );
});
