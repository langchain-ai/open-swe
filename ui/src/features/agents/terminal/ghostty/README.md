### Ghostty browser terminal internals and provenance

This is the browser adapter for the official `libghostty-vt` C ABI, ported from T3 Code at commit `9afef94a61466422128da9c3b723b633d4c7ed1d`.

- `runtime.ts` owns the singleton WebAssembly runtime and ABI layouts.
- `core.ts` translates terminal handles into render snapshots and encodes keyboard, paste, mouse, selection, and hyperlink operations.
- `renderer.ts` renders snapshots to Canvas 2D.
- `surface.ts` owns browser input, IME, selection, scrolling, links, sizing, themes, fonts, and cursor blinking. Transport and application actions are callbacks.
- `vendor/` contains committed WASM copied from T3 Code; `fonts/` contains the symbols-only Nerd Font.
- `ui/scripts/build-libghostty-wasm.sh` reproducibly rebuilds both WASM files from Ghostty revision `9f62873bf195e4d8a762d768a1405a5f2f7b1697` using Zig 0.15.2.
- `ui/native/libghostty-vt/` contains the exact Ghostty pin, MIT license, and C ABI headers used by the adapter. `T3-LICENSE` preserves the source port's MIT license; `fonts/LICENSE` is the exact Nerd Font license.

The WASM files are read-only browser assets. Keep terminal transport out of the surface and do not add React state to its render loop.
