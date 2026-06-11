import { useEffect, useMemo, useState } from "react";
import { getSingletonHighlighter, type ThemedToken } from "shiki";
import { useResolvedTheme } from "@/lib/theme";

interface CodeBlockProps {
  text: string;
  language?: string;
}

const SHIKI_THEME = { light: "github-light", dark: "github-dark" } as const;

const TOKEN_CACHE = new Map<string, ThemedToken[][]>();

function normalizeLanguage(language?: string): string {
  const raw = (language || "").toLowerCase().trim();
  if (!raw) return "text";

  const aliases: Record<string, string> = {
    ts: "typescript",
    tsx: "tsx",
    js: "javascript",
    jsx: "jsx",
    md: "markdown",
    yml: "yaml",
    sh: "bash",
    zsh: "bash",
    shell: "bash",
    py: "python",
    rb: "ruby",
    rs: "rust",
    csharp: "csharp",
    "c#": "csharp",
    plaintext: "text",
    txt: "text",
  };

  return aliases[raw] || raw;
}

function languageLabel(language: string): string {
  if (language === "text") return "text";
  if (language === "typescript") return "ts";
  if (language === "javascript") return "js";
  return language;
}

export function CodeBlock({ text, language }: CodeBlockProps) {
  const [tokens, setTokens] = useState<ThemedToken[][] | null>(null);
  const [copied, setCopied] = useState(false);
  const resolvedTheme = useResolvedTheme();
  const shikiTheme = SHIKI_THEME[resolvedTheme];
  const normalizedLanguage = useMemo(() => normalizeLanguage(language), [language]);
  const displayLanguage = useMemo(() => languageLabel(normalizedLanguage), [normalizedLanguage]);

  useEffect(() => {
    let cancelled = false;
    setTokens(null);

    if (normalizedLanguage === "text") return;

    const cacheKey = `${shikiTheme}::${normalizedLanguage}::${text}`;
    const cached = TOKEN_CACHE.get(cacheKey);
    if (cached) {
      setTokens(cached);
      return;
    }

    getSingletonHighlighter({
      themes: [shikiTheme],
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      langs: [normalizedLanguage as any],
    })
      .then((highlighter) => {
        if (cancelled) return;
        const result = highlighter.codeToTokens(text, {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          lang: normalizedLanguage as any,
          theme: shikiTheme,
        });
        if (TOKEN_CACHE.size >= 500) TOKEN_CACHE.clear();
        TOKEN_CACHE.set(cacheKey, result.tokens);
        setTokens(result.tokens);
      })
      .catch((err: unknown) => {
        console.warn('[code-block] Tokenization failed:', err);
        if (!cancelled) {
          setTokens(null);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [text, normalizedLanguage, shikiTheme]);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="my-2 max-w-full overflow-hidden rounded-xl border border-[var(--ui-border-subtle)] bg-[var(--ui-code-bubble)]">
      <div className="flex items-center justify-between px-3 py-2 text-xs">
        <span className="font-mono text-[color:var(--ui-accent-2)]">{displayLanguage}</span>
        <button
          type="button"
          onClick={handleCopy}
          className="text-[color:var(--ui-text-muted)] hover:text-[color:var(--ui-text)] transition-colors"
          title="Copy code"
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="max-w-full px-3 pb-3 text-[12px] whitespace-pre-wrap break-words [overflow-wrap:anywhere]">
        {tokens ? (
          <code className="block max-w-full">
            {tokens.map((lineTokens, lineIndex) => (
              <div key={lineIndex} className="max-w-full whitespace-pre-wrap break-words [overflow-wrap:anywhere]">
                {lineTokens.map((token, tokenIndex) => (
                  <span key={tokenIndex} style={{ color: token.color }}>
                    {token.content}
                  </span>
                ))}
              </div>
            ))}
          </code>
        ) : (
          <code className="block max-w-full text-[color:var(--ui-text)] whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{text}</code>
        )}
      </pre>
    </div>
  );
}
