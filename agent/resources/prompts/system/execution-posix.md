### Observed execution environment

```json
$environment
```

Use POSIX-compatible shell syntax. Each command starts in the recorded working
directory; a previous `cd` does not persist. Quote paths containing spaces and use
`git -C checkout ...` when operating on a checkout. Use the detected Python
executable when available. For file tools prefer paths relative to the working
directory to avoid mixing host paths with a virtual filesystem root.
