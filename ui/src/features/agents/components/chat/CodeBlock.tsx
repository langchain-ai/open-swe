import { useEffect, useMemo, useState } from "react"
import { getSingletonHighlighter } from "shiki"
import type { BundledLanguage, ThemeRegistration, ThemedToken } from "shiki"

import { Box, Inline } from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { Check, Copy, WrapText } from "@/components/glyphs"
import { cn } from "@/lib/utils"

interface CodeBlockProps {
  text: string
  language?: string
  /** Filename from the fence meta (```ts title="src/main.ts"), shown instead of the language. */
  title?: string | null
}

/*
 * Syntax colours are roles mapped onto the state family, written as token
 * references so one theme serves both modes and flips with `data-theme`:
 * keywords info, strings positive, constants attention, names risk, comments
 * and punctuation ink-subtle, everything else ink.
 */
const TOKEN_THEME_NAME = "gtm-tokens"
const TOKEN_THEME: ThemeRegistration = {
  name: TOKEN_THEME_NAME,
  type: "light",
  colors: {
    "editor.foreground": "var(--gtm-ink)",
    "editor.background": "transparent",
  },
  tokenColors: [
    { settings: { foreground: "var(--gtm-ink)" } },
    {
      scope: ["comment", "punctuation", "meta.brace", "string.comment"],
      settings: { foreground: "var(--gtm-ink-subtle)" },
    },
    {
      scope: [
        "keyword",
        "storage",
        "storage.type",
        "keyword.operator",
        "entity.name.tag",
        "entity.other.attribute-name",
        "support.type.property-name",
      ],
      settings: { foreground: "var(--gtm-info)" },
    },
    {
      scope: [
        "string",
        "string.regexp",
        "markup.inserted",
        "constant.character",
      ],
      settings: { foreground: "var(--gtm-positive)" },
    },
    {
      scope: [
        "constant",
        "constant.numeric",
        "constant.language",
        "variable.other.constant",
        "support.constant",
      ],
      settings: { foreground: "var(--gtm-attention)" },
    },
    {
      scope: [
        "entity.name.function",
        "support.function",
        "entity.name.type",
        "entity.name.class",
        "support.class",
        "markup.deleted",
      ],
      settings: { foreground: "var(--gtm-risk)" },
    },
  ],
}

const TOKEN_CACHE = new Map<string, Array<Array<ThemedToken>>>()

const COPIED_RESET_MS = 1200

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

    const cacheKey = `${normalizedLanguage}::${code}`
    const cached = TOKEN_CACHE.get(cacheKey)
    if (cached) {
      setTokens(cached)
      return
    }

    getSingletonHighlighter({
      themes: [TOKEN_THEME],
      langs: [normalizedLanguage as BundledLanguage],
    })
      .then((highlighter) => {
        if (cancelled) return
        const result = highlighter.codeToTokens(code, {
          lang: normalizedLanguage as BundledLanguage,
          theme: TOKEN_THEME_NAME,
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
  }, [code, normalizedLanguage])

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      window.setTimeout(() => setCopied(false), COPIED_RESET_MS)
    } catch {
      setCopied(false)
    }
  }

  const wrapLabel = wrapped ? "Disable line wrap" : "Wrap lines"
  const lineClassName = wrapped
    ? "wrap-anywhere whitespace-pre-wrap"
    : "whitespace-pre"

  return (
    <Box
      data-slot="code-block"
      bg="muted"
      border="line"
      radius="compact"
      className="max-w-full overflow-hidden"
    >
      <Inline
        align="center"
        justify="between"
        gap="sm"
        className="h-control-sm border-b border-line pr-0.5 pl-3 select-none"
      >
        <Box
          render={<span />}
          className="truncate font-mono text-meta text-ink-subtle"
        >
          {title || displayLanguage}
        </Box>
        <Inline gap="none" role="toolbar" aria-label="Code block actions">
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            onClick={() => setWrapped((value) => !value)}
            aria-pressed={wrapped}
            aria-label={wrapLabel}
            title={wrapLabel}
            className="text-ink-subtle hover:text-ink aria-pressed:text-ink"
          >
            <Icon icon={WrapText} size="sm" />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            onClick={handleCopy}
            aria-label={copied ? "Copied" : "Copy code"}
            title={copied ? "Copied" : "Copy code"}
            className="text-ink-subtle hover:text-ink"
          >
            <Icon icon={copied ? Check : Copy} size="sm" />
          </Button>
        </Inline>
      </Inline>
      <pre
        className={cn(
          "max-w-full overflow-x-auto px-3 py-2.5 font-mono text-meta",
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
          <code className={cn("block max-w-full text-ink", lineClassName)}>
            {code}
          </code>
        )}
      </pre>
    </Box>
  )
}
