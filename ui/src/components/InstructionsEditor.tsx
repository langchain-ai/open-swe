import Editor from "@monaco-editor/react"
import { Box } from "@langchain/gtm-platform-design-system/ui/box"
import { Textarea } from "@langchain/gtm-platform-design-system/ui/textarea"
import { useIsHydrated } from "@/lib/hydration"
import { useResolvedTheme } from "@/lib/theme"

interface InstructionsEditorProps {
  value: string
  onChange: (value: string) => void
  disabled?: boolean
  placeholder?: string
}

/** Monaco-backed code editor that falls back to a textarea before mount (SSR-safe). */
export function InstructionsEditor({
  value,
  onChange,
  disabled,
  placeholder,
}: InstructionsEditorProps) {
  const mounted = useIsHydrated()
  const theme = useResolvedTheme()

  if (!mounted) {
    return (
      <Textarea
        className="min-h-90 w-full font-mono text-label"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
      />
    )
  }

  return (
    <Box border="line" radius="compact" className="overflow-hidden">
      <Editor
        height="360px"
        language="markdown"
        value={value}
        onChange={(v) => onChange(v ?? "")}
        options={{
          readOnly: disabled,
          minimap: { enabled: false },
          wordWrap: "on",
          fontSize: 12,
          lineNumbers: "on",
          scrollBeyondLastLine: false,
          padding: { top: 12, bottom: 12 },
          renderLineHighlight: "none",
        }}
        theme={theme === "dark" ? "vs-dark" : "light"}
      />
    </Box>
  )
}
