// Node ≥ 22 defines its own `localStorage`/`sessionStorage` globals, which are
// `undefined` unless the process runs with `--localstorage-file`. Vitest's
// jsdom environment leaves those Node getters in place, shadowing jsdom's
// storage — so DOM tests see `window.localStorage === undefined`. Replace any
// missing storage with an in-memory implementation (fresh per test file).
class MemoryStorage implements Storage {
  #entries = new Map<string, string>()

  get length(): number {
    return this.#entries.size
  }

  key(index: number): string | null {
    return [...this.#entries.keys()][index] ?? null
  }

  getItem(key: string): string | null {
    return this.#entries.get(String(key)) ?? null
  }

  setItem(key: string, value: string): void {
    this.#entries.set(String(key), String(value))
  }

  removeItem(key: string): void {
    this.#entries.delete(String(key))
  }

  clear(): void {
    this.#entries.clear()
  }
}

if (typeof document !== "undefined") {
  const globals = globalThis as Record<string, unknown>
  for (const name of ["localStorage", "sessionStorage"]) {
    if (!globals[name]) {
      Object.defineProperty(globalThis, name, {
        value: new MemoryStorage(),
        configurable: true,
        writable: true,
      })
    }
  }
}
