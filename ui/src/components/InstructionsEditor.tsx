import Editor from "@monaco-editor/react"
import { Textarea } from "@/components/ui/textarea"
import { useIsHydrated } from "@/lib/hydration"

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

  if (!mounted) {
    return (
      <Textarea
        className="min-h-[360px] w-full font-mono text-xs"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
      />
    )
  }

  return (
    <div className="overflow-hidden rounded-md border border-border">
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
        theme="vs-dark"
      />
    </div>
  )
}
