import { useEffect, useMemo, useState } from "react"
import { Check, Copy, WrapText } from "lucide-react"
import { getSingletonHighlighter } from "shiki"
import type { ThemedToken } from "shiki"
import { useResolvedTheme } from "@/lib/theme"

interface CodeBlockProps {
  text: string
  language?: string
  /** Filename from the fence meta (```ts title="src/main.ts"), shown instead of the language. */
  title?: string | null
}

const SHIKI_THEME = { light: "github-light", dark: "github-dark" } as const

const TOKEN_CACHE = new Map<string, Array<Array<ThemedToken>>>()

function normalizeLanguage(language?: string): string {
  const raw = (language || "").toLowerCase().trim()
  if (!raw) return "text"

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
    // Shiki has no gitignore grammar; ini is a close match.
    gitignore: "ini",
  }

  return aliases[raw] || raw
}

function languageLabel(language: string): string {
  if (language === "text") return "text"
  if (language === "typescript") return "ts"
  if (language === "javascript") return "js"
  return language
}

export function CodeBlock({ text, language, title }: CodeBlockProps) {
  // Only the parser-added terminal newline: trailing spaces and blank lines are
  // part of the fence, and this value is what Copy writes to the clipboard.
  const code = useMemo(() => text.replace(/\n$/, ""), [text])
  const [tokens, setTokens] = useState<Array<Array<ThemedToken>> | null>(null)
  const [copied, setCopied] = useState(false)
  const [wrapped, setWrapped] = useState(false)
  const resolvedTheme = useResolvedTheme()
  const shikiTheme = SHIKI_THEME[resolvedTheme]
  const normalizedLanguage = useMemo(
    () => normalizeLanguage(language),
    [language]
  )
  const displayLanguage = useMemo(
    () => languageLabel(normalizedLanguage),
    [normalizedLanguage]
  )

  useEffect(() => {
    let cancelled = false
    // oxlint-disable-next-line react/set-state-in-effect
    setTokens(null)

    if (normalizedLanguage === "text") return

    const cacheKey = `${shikiTheme}::${normalizedLanguage}::${code}`
    const cached = TOKEN_CACHE.get(cacheKey)
    if (cached) {
      setTokens(cached)
      return
    }

    getSingletonHighlighter({
      themes: [shikiTheme],
      langs: [normalizedLanguage as any],
    })
      .then((highlighter) => {
        if (cancelled) return
        const result = highlighter.codeToTokens(code, {
          lang: normalizedLanguage as any,
          theme: shikiTheme,
        })
        if (TOKEN_CACHE.size >= 500) TOKEN_CACHE.clear()
        TOKEN_CACHE.set(cacheKey, result.tokens)
        setTokens(result.tokens)
      })
      .catch((err: unknown) => {
        console.warn("[code-block] Tokenization failed:", err)
        if (!cancelled) {
          setTokens(null)
        }
      })

    return () => {
      cancelled = true
    }
  }, [code, normalizedLanguage, shikiTheme])

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1200)
    } catch {
      setCopied(false)
    }
  }

  const wrapLabel = wrapped ? "Disable line wrap" : "Wrap lines"
  const lineClassName = wrapped
    ? "[overflow-wrap:anywhere] break-words whitespace-pre-wrap"
    : "whitespace-pre"

  return (
    <div className="my-[0.65rem] max-w-full overflow-hidden rounded-lg border border-border/70 bg-muted/50">
      <div className="flex items-center justify-between gap-2 pt-1 pr-1 pl-2.5 select-none">
        <span className="truncate font-mono text-[11px] text-muted-foreground">
          {title || displayLanguage}
        </span>
        <span
          className="flex items-center gap-0.5"
          role="toolbar"
          aria-label="Code block actions"
        >
          <button
            type="button"
            onClick={() => setWrapped((value) => !value)}
            aria-pressed={wrapped}
            aria-label={wrapLabel}
            title={wrapLabel}
            className="rounded p-1 text-muted-foreground transition-colors hover:text-foreground aria-pressed:text-foreground"
          >
            <WrapText className="size-3.5" />
          </button>
          <button
            type="button"
            onClick={handleCopy}
            aria-label={copied ? "Copied" : "Copy code"}
            title={copied ? "Copied" : "Copy code"}
            className="rounded p-1 text-muted-foreground transition-colors hover:text-foreground"
          >
            {copied ? (
              <Check className="size-3.5" />
            ) : (
              <Copy className="size-3.5" />
            )}
          </button>
        </span>
      </div>
      <pre
        className={`max-w-full overflow-x-auto px-2.5 pt-0.5 pb-2.5 text-[12.5px] leading-[1.55] ${wrapped ? "whitespace-pre-wrap" : ""}`}
      >
        {tokens ? (
          <code className="block max-w-full">
            {tokens.map((lineTokens, lineIndex) => (
              <div key={lineIndex} className={`max-w-full ${lineClassName}`}>
                {lineTokens.map((token, tokenIndex) => (
                  <span key={tokenIndex} style={{ color: token.color }}>
                    {token.content}
                  </span>
                ))}
                {lineTokens.length === 0 ? "\n" : null}
              </div>
            ))}
          </code>
        ) : (
          <code className={`block max-w-full text-foreground ${lineClassName}`}>
            {code}
          </code>
        )}
      </pre>
    </div>
  )
}
