/** Keys that flap: Space, Up or W. */
export const FLAP_KEYS: ReadonlySet<string> = new Set([
  "Space",
  "ArrowUp",
  "KeyW",
])

/** Keys pressed on a control (a button, a field) belong to it, not to the game. */
export function isControl(target: EventTarget | null): boolean {
  return (
    target instanceof Element &&
    target.closest("button, input, [role='slider'], [role='switch']") !== null
  )
}
