import type { ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { CodeBlock } from './CodeBlock';
import { MarkdownTable } from './MarkdownTable';

interface MarkdownProps {
  content: string;
}

function extractText(children: ReactNode): string {
  return String(children);
}

export function Markdown({ content }: MarkdownProps) {
  return (
    <div className="min-w-0 max-w-full text-[13px] leading-6 break-words [overflow-wrap:anywhere]">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => <div className="text-[color:var(--ui-accent)] text-[20px] font-semibold mt-4 mb-2 tracking-tight">{children}</div>,
          h2: ({ children }) => <div className="text-[color:var(--ui-accent)] text-[17px] font-semibold mt-3 mb-2 tracking-tight">{children}</div>,
          h3: ({ children }) => <div className="text-[color:var(--ui-accent)] text-[15px] font-semibold mt-3 mb-1">{children}</div>,
          p: ({ children }) => <p className="my-1.5 text-[color:var(--ui-text)] break-words [overflow-wrap:anywhere]">{children}</p>,
          a: ({ href, children }) => (
            <a className="text-[color:var(--ui-accent)] underline decoration-[color:var(--ui-accent)]/50 break-words [overflow-wrap:anywhere]" href={href} target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
          ul: ({ children }) => <div className="my-1.5 ml-4 min-w-0">{children}</div>,
          ol: ({ children }) => <div className="my-1.5 ml-4 min-w-0">{children}</div>,
          li: ({ children }) => <div className="text-[color:var(--ui-text)] break-words [overflow-wrap:anywhere] [&>p]:inline [&>p]:my-0">- {children}</div>,
          blockquote: ({ children }) => (
            <div className="my-2 border-l-2 border-[var(--ui-border)] pl-3 text-[color:var(--ui-text-muted)] break-words [overflow-wrap:anywhere]">{children}</div>
          ),
          hr: () => <hr className="border-[var(--ui-border-subtle)] my-3" />,
          table: ({ children }) => <MarkdownTable>{children}</MarkdownTable>,
          thead: ({ children }) => <thead className="border-b border-[var(--ui-border)]">{children}</thead>,
          tbody: ({ children }) => <tbody>{children}</tbody>,
          tr: ({ children }) => <tr>{children}</tr>,
          th: ({ children }) => <th className="px-3 py-1 text-left text-[color:var(--ui-accent)] break-words [overflow-wrap:anywhere]">{children}</th>,
          td: ({ children }) => <td className="px-3 py-1 text-[color:var(--ui-text-muted)] break-words [overflow-wrap:anywhere]">{children}</td>,
          code: ({ className, children }) => {
            const text = extractText(children);
            const match = /language-([^\s]+)/.exec(className || '');
            const isBlock = match || text.includes('\n');
            if (isBlock) {
              return <CodeBlock text={text.replace(/\n$/, '')} language={match?.[1]} />;
            }
            return <code className="rounded-md bg-[var(--ui-panel-2)] px-1.5 py-0.5 font-mono text-[0.85em] text-[color:var(--ui-accent)] whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{text}</code>;
          },
          pre: ({ children }) => <>{children}</>,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
