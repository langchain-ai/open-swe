import { useCallback, useEffect, useRef } from "react";

export const DOUBLE_PRESS_TIMEOUT_MS = 800;

/**
 * Returns a callback that fires `onFirstPress` on the first invocation and
 * `onDoublePress` if invoked again within DOUBLE_PRESS_TIMEOUT_MS. While the
 * "second press" window is open, `setPending(true)` is called so the caller
 * can render a transient hint (e.g. "Press Esc again to clear").
 */
export function useDoublePress(
  setPending: (pending: boolean) => void,
  onDoublePress: () => void,
  onFirstPress?: () => void,
): () => void {
  const lastPressRef = useRef<number>(0);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );

  const clearTimeoutSafe = useCallback(() => {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = undefined;
    }
  }, []);

  useEffect(() => {
    return () => clearTimeoutSafe();
  }, [clearTimeoutSafe]);

  return useCallback(() => {
    const now = Date.now();
    const timeSinceLastPress = now - lastPressRef.current;
    const isDoublePress =
      timeSinceLastPress <= DOUBLE_PRESS_TIMEOUT_MS &&
      timeoutRef.current !== undefined;

    if (isDoublePress) {
      clearTimeoutSafe();
      setPending(false);
      onDoublePress();
    } else {
      onFirstPress?.();
      setPending(true);
      clearTimeoutSafe();
      timeoutRef.current = setTimeout(() => {
        setPending(false);
        timeoutRef.current = undefined;
      }, DOUBLE_PRESS_TIMEOUT_MS);
    }

    lastPressRef.current = now;
  }, [setPending, onDoublePress, onFirstPress, clearTimeoutSafe]);
}
