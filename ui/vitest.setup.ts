import { JSDOM } from "jsdom"

// Vitest copies a jsdom global onto globalThis only when the name is absent
// there, unless it is on its own always-override list — which `localStorage` is
// not. Node 26 defines an experimental `localStorage` global that reads back
// undefined without `--localstorage-file`, so jsdom's never lands. Node 22 has
// no such global, hence CI never sees this.
if (!(globalThis as { localStorage?: Storage }).localStorage) {
  const { window } = new JSDOM("", { url: "http://localhost:3000" })
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: window.localStorage,
  })
}
