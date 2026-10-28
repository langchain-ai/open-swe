# Dashboard UI

Read [DESIGN.md](DESIGN.md) before changing Open SWE’s web/app UI.
Prefer the installed Macaw components and semantic tokens over custom controls and styles.

Before implementing or replacing a control, use Macaw CLI discovery from the repository root:

```sh
pnpm --dir ui exec macaw search "<capability>" --json
pnpm --dir ui exec macaw inspect <ComponentName> --json
```

Use the returned imports and read the installed component and styling guidance linked in DESIGN.md.
Discovery reads the installed catalog; do not assume APIs from another Macaw version.
Preserve the specialized integrations documented in DESIGN.md.
