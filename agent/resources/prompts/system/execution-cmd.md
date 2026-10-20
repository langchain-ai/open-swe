### Observed execution environment

```json
$environment
```

Commands execute in Windows cmd.exe. Each command starts in the recorded working
directory; a previous `cd` does not persist. Use `git -C checkout ...`, or
`cd /d "C:\path with spaces" && command`. Quote paths containing spaces.
POSIX shell syntax and commands such as `pwd`, `head`, `tail`, `grep`, and `rm`
are not built into cmd.exe. Use the provided file/search tools and Python for
portable file operations. Use the detected Python executable when `python` on
PATH is unavailable. For file tools use paths relative to the working directory
to avoid mixing virtual filesystem paths with Windows drive-letter paths.
