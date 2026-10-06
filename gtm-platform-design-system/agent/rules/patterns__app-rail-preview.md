# AppRailPreview

Collapsed destination icons open one contextual HoverCard to the right after hover or focus intent. Expanded rows already carry their labels and do not duplicate the card.

Import: `@langchain/gtm-platform-design-system/patterns/app-rail-preview`
Source: `src/patterns/app-rail-preview.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### AppRailPreview

| Prop | Type | Required |
|---|---|---|
| `title` | `string` | yes |
| `description` | `string` | yes |
| `destination` | `string` | yes |
| `icon` | `Glyph` | yes |
| `items` | `readonly AppRailPreviewItem[]` | yes |
| `emptyMessage` | `string` | yes |
| `loading` | `boolean` | no |
| `onNavigate` | `(href: string) => void` | yes |

## The decisions this carries

- Collapsed destination icons open one contextual HoverCard to the right after hover or focus intent. Expanded rows already carry their labels and do not duplicate the card.
- Every destination in the expanded tree, including nested children, becomes its own collapsed icon. One bounded server briefing supplies every preview; opening another icon reuses that document.
- A preview shows the destination identity, at most three recent rows, and one Open destination footer. It is orientation, not a miniature page or collection browser.
- Rows use only the browser-safe fields the owning endpoint already projects. Never render raw provider payloads, tool data, credentials, or HTML in rail chrome.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
