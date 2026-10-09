import { Textarea } from "@langchain/macaw-components/Textarea"
import Editor, { type Monaco } from "@monaco-editor/react"

import { useIsHydrated } from "@/lib/hydration"
import { useResolvedTheme } from "@/lib/theme"

const FILL = "h-full min-h-0 flex-1"

function defineThemes(monaco: Monaco) {
  const transparent = { "editor.background": "#00000000" }
  monaco.editor.defineTheme("app-dark", {
    base: "vs-dark",
    inherit: true,
    rules: [],
    colors: transparent,
  })
  monaco.editor.defineTheme("app-light", {
    base: "vs",
    inherit: true,
    rules: [],
    colors: transparent,
  })
}

interface TextEditorProps {
  value: string
  onChange: (value: string) => void
  language?: "markdown" | "shell"
  ariaLabel?: string
  disabled?: boolean
  placeholder?: string
}

/** Monaco editor that fills its parent; a textarea stands in before hydration (SSR-safe). */
export function TextEditor({
  value,
  onChange,
  language = "markdown",
  ariaLabel,
  disabled,
  placeholder,
}: TextEditorProps) {
  const mounted = useIsHydrated()
  const theme = useResolvedTheme()

  if (!mounted) {
    return (
      <Textarea
        className={FILL}
        inputClassName="h-full font-mono text-xs"
        resize="none"
        size="md"
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        disabled={disabled}
      />
    )
  }

  return (
    <div className={FILL}>
      <Editor
        height="100%"
        language={language}
        value={value}
        onChange={(next) => onChange((next ?? "").replace(/\r\n/g, "\n"))}
        beforeMount={defineThemes}
        theme={`app-${theme}`}
        options={{
          ariaLabel,
          placeholder,
          readOnly: disabled,
          automaticLayout: true,
          minimap: { enabled: false },
          wordWrap: "on",
          fontSize: 13,
          tabSize: 2,
          scrollBeyondLastLine: false,
          padding: { top: 16, bottom: 16 },
          renderLineHighlight: "none",
        }}
      />
    </div>
  )
}
