import preset from "@langchain/macaw-components/tailwind-preset"
import zIndices from "@langchain/macaw-components/utils/zIndices"

const kebab = (name) => name.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)

/** @type {import('tailwindcss').Config} */
export default {
  presets: [preset],
  theme: {
    extend: {
      zIndex: Object.fromEntries(
        Object.entries(zIndices).map(([name, value]) => [
          kebab(name),
          String(value),
        ])
      ),
    },
  },
}
