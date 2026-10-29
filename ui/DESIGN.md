# Open SWE dashboard design

Use the vendored `@langchain/macaw-components` and `@langchain/macaw-tokens` packages for shared UI.
Import components through their package subpaths.
Use semantic tokens for colors, including CSS passed to third-party renderers.

## Component discovery

The dashboard includes `@langchain/macaw-cli` as a development dependency.
Use Node.js 26 or newer for the CLI.
Before choosing a component, run from the repository root:

```sh
pnpm --dir ui exec macaw search "<capability>" --json
pnpm --dir ui exec macaw inspect Button --json
pnpm --dir ui exec macaw list --json
```

The CLI reads the vendored component package’s catalog, so discovery matches the installed API.
Read its [component guidance](node_modules/@langchain/macaw-components/docs/DESIGN.md) and [styling guidance](node_modules/@langchain/macaw-components/docs/STYLES.md) before implementing UI.

## Product layout and variants

Preserve Open SWE’s compact workspace layout and information density when adopting shared components.
Keep titles and repository headings visually distinct from supporting metadata.
Inspect the relevant component’s source and stories returned by the CLI before selecting its size, color, and variant.
Use an emphasized action for the primary task and quieter controls for secondary actions, navigation, and disclosures.
Do not shrink text or override shared colors simply to make a default variant fit an existing wrapper.
Check the wrapper’s height, padding, truncation, and focus-ring clearance instead.

Compose existing primitives for product-specific layouts.
Keep shared component behavior and reusable variants in Macaw rather than copying them into Open SWE.
Keep routing, permissions, data fetching, and domain behavior in the application.

## UI conventions

Standard actions, fields, menus, checkboxes, badges, and progress indicators use Macaw.
Rich button content needs an explicit accessible name (`aria-label` or `aria-labelledby`); check wrapping, truncation, and keyboard focus when adapting the default button layout.
Menu rows use `@/components/MenuItem` to retain the dashboard's icon spacing.

## Review UI changes

Check the affected controls in light and dark themes and at desktop and narrow widths.
Exercise keyboard focus, selection, hover, disabled, loading, empty, and error states where applicable.
Keep field boundaries and placeholder instructions readable; preserve semantic field surfaces and focus styling.
Expose selected, expanded, checked, resolved, and progress states through the appropriate accessible properties or names.
Preserve counts and status summaries when adding an explicit accessible name to a rich control.
Do not fade an entire resolved finding if that makes its text unreadable.
Check long labels, wrapping, truncation, and scrolling in compact tabs, menus, and diff headers.
Attach screenshots of the affected UI to the PR and distinguish fixture captures from authenticated product checks.

## Specialized integrations

These integrations retain their specialized behavior:

- Monaco for code editing, Lexical for the composer, and Pierre for diffs and file trees.
  Their surrounding controls use Macaw; Pierre's diff colors use Macaw tokens.
- Sonner for the imperative toast API, with Macaw icons and semantic styles in `@/components/Toaster`.
- Sandboxed artifact HTML and its in-frame annotation controls, which run in a separate document.
- The desktop's standalone setup form, which connects to the backend before loading the React dashboard and intentionally runs without JavaScript.
- The composer's hidden native file picker and `SecretInput`'s native password field.
  The latter uses Macaw's input style helpers and a labelled Macaw reveal button while preserving native password semantics and controlled visibility.
- Structural navigation/card links, interactive images, and inline disclosure controls inside navigation links or field labels.
  These preserve the host element's semantics; ordinary content links use Macaw `Link`.
