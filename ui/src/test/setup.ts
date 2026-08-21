// jsdom has no ResizeObserver; `use-stick-to-bottom` (the chat scroll
// container) constructs one at mount, so component tests need a stand-in.
if (
  typeof window !== "undefined" &&
  typeof window.ResizeObserver === "undefined"
) {
  class ResizeObserverStub {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  window.ResizeObserver = ResizeObserverStub
}
