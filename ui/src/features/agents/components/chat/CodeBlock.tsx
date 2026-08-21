import { useMemo } from "react"
import type { BundledLanguage } from "shiki"
import {
  CodeBlockActions,
  CodeBlock as CodeBlockBase,
  CodeBlockCopyButton,
  CodeBlockHeader,
  CodeBlockTitle,
} from "@/components/ai-elements/code-block"

interface CodeBlockProps {
  text: string
  language?: string
}

const LANGUAGE_ALIASES: Record<string, string> = {
  ts: "typescript",
  js: "javascript",
  md: "markdown",
  yml: "yaml",
  sh: "bash",
  zsh: "bash",
  shell: "bash",
  py: "python",
  rb: "ruby",
  rs: "rust",
  "c#": "csharp",
  plaintext: "text",
  txt: "text",
}

function normalizeLanguage(language?: string): string {
  const raw = (language || "").toLowerCase().trim()
  if (!raw) return "text"
  return LANGUAGE_ALIASES[raw] || raw
}

function languageLabel(language: string): string {
  if (language === "typescript") return "ts"
  if (language === "javascript") return "js"
  return language
}

export function CodeBlock({ text, language }: CodeBlockProps) {
  const normalizedLanguage = useMemo(
    () => normalizeLanguage(language),
    [language]
  )

  return (
    <CodeBlockBase
      // Shiki paints the theme's own page colour onto the <pre>; drop it so
      // the block sits on the app's muted surface in both themes.
      className="my-2 max-w-full rounded-xl border-border/60 bg-muted [&_pre]:bg-transparent! [&_pre]:p-3 [&_pre]:text-[12px] [&_pre]:[overflow-wrap:anywhere] [&_pre]:break-words [&_pre]:whitespace-pre-wrap"
      code={text}
      language={normalizedLanguage as BundledLanguage}
    >
      <CodeBlockHeader className="border-0 bg-transparent">
        <CodeBlockTitle className="font-mono">
          {languageLabel(normalizedLanguage)}
        </CodeBlockTitle>
        <CodeBlockActions>
          <CodeBlockCopyButton aria-label="Copy code" size="icon-sm" />
        </CodeBlockActions>
      </CodeBlockHeader>
    </CodeBlockBase>
  )
}
