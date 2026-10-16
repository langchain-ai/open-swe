# Open SWE dashboard

TanStack Start + React 19 app styled with the [Macaw design system](https://github.com/langchain-ai/macaw-design-system).

## Design system

- Components come from `@langchain/macaw-components/<Family>` (`Button`, `IconButton`, `Dialog`, `DropdownMenu`, `Select`, `Tooltip`, …). Read `node_modules/@langchain/macaw-components/docs/STYLES.md` before building UI.
- Colours, radii, shadows and type come from Macaw's semantic Tailwind classes (`bg-surface-level-1`, `text-secondary`, `border-default`, `bg-elevated`, …), loaded through `tailwind.config.cjs` and `src/styles.css`. Don't use raw palette classes or hex values.
- Icons are Phosphor leaf imports: `import { CheckIcon } from "@phosphor-icons/react/dist/ssr/Check"`.
- Merge classes with `cn` from `@/lib/utils` (Macaw's token-aware merge).
- Dark mode is the `.dark` class on `<html>`, managed by `src/lib/theme.ts`.
- Toasts use `toast` from `sonner`, rendered by the Macaw-styled `src/components/Toaster.tsx`.

`vendor/macaw/` holds the Macaw 2.0.0-alpha tarballs until they are published to npm.
