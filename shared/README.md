# open-swe-shared

Tool-call middleware that both the Open SWE backend (`openswe/`) and its Managed Deep Agents runtime (`mda/`) run.

This code belongs in MDA and will move there entirely. For now it also has to live in the backend, which still runs the local threads created before MDA, which are being phased out, and the tool calls MDA still hands back to it. Once neither is left, move this package into `mda/`.

It holds no credentials and imports nothing from `openswe` or `mda`. Anything only one side can do, such as reaching the thread's sandbox or telling the person their sandbox died, is a method each side overrides.
