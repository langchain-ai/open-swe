import { useEffect, useMemo, useState } from "react"
import { TextAlignLeftIcon } from "@phosphor-icons/react/dist/ssr/TextAlignLeft"
import { CopyIconButton } from "@langchain/macaw-components/CopyButton"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { getSingletonHighlighter } from "shiki"
import type { BundledLanguage, ThemedToken } from "shiki"
import { useResolvedTheme } from "@/lib/theme"
import { cn } from "@/lib/utils"

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
      langs: [normalizedLanguage as BundledLanguage],
    })
      .then((highlighter) => {
        if (cancelled) return
        const result = highlighter.codeToTokens(code, {
          lang: normalizedLanguage as BundledLanguage,
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

  const wrapLabel = wrapped ? "Disable line wrap" : "Wrap lines"
  const lineClassName = wrapped
    ? "[overflow-wrap:anywhere] break-words whitespace-pre-wrap"
    : "whitespace-pre"

  return (
    <div className="my-[0.65rem] max-w-full overflow-hidden rounded-lg border border-subtle bg-surface-level-2">
      <div className="flex items-center justify-between gap-space-2 pt-space-1 pr-space-1 pl-space-2 select-none">
        <span className="truncate font-mono text-xxs text-secondary">
          {title || displayLanguage}
        </span>
        <span
          className="flex items-center gap-0.5"
          role="toolbar"
          aria-label="Code block actions"
        >
          <IconButton
            icon={TextAlignLeftIcon}
            label={wrapLabel}
            size="xs"
            color="secondary"
            variant="plain"
            onClick={() => setWrapped((value) => !value)}
            aria-pressed={wrapped}
            className={cn(wrapped && "bg-selected text-primary")}
          />
          <CopyIconButton copy={code} label="Copy code" size="xs" />
        </span>
      </div>
      <pre
        className={cn(
          "max-w-full overflow-x-auto px-space-2 pt-0.5 pb-space-2 font-mono text-xxs leading-[1.55]",
          wrapped && "whitespace-pre-wrap"
        )}
      >
        {tokens ? (
          <code className="block max-w-full">
            {tokens.map((lineTokens, lineIndex) => (
              <div key={lineIndex} className={cn("max-w-full", lineClassName)}>
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
          <code className={cn("block max-w-full text-primary", lineClassName)}>
            {code}
          </code>
        )}
      </pre>
    </div>
  )
}
