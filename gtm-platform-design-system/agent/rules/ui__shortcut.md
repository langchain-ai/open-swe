# Shortcut

Kbd is one key. Shortcut is the chord: a row of Kbd chips, never a string like ⌘K inside one chip.

Import: `@langchain/gtm-platform-design-system/ui/shortcut`
Source: `src/ui/shortcut.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### Shortcut

| Prop | Type | Required |
|---|---|---|
| `keys` | `readonly ShortcutKey[]` | yes |
| `apple` | `boolean` | no |
| `className` | `string` | no |

## The decisions this carries

- Kbd is one key. Shortcut is the chord: a row of Kbd chips, never a string like ⌘K inside one chip.
- `mod` is the platform modifier (⌘ on Apple, Ctrl elsewhere). Do not hard-code ⌘ in product chrome.
- The group carries `aria-keyshortcuts`. The chips stay visual.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
