"use client";

/*
 * Is the pointer a finger?
 *
 * Ported from fluid-functionalism's `use-touch-primary` (ANALYSIS.md row 11),
 * which credits Lina's `use-has-primary-touch`. The question it answers is not
 * "does this device have a touchscreen" but "is touch the way this person is
 * driving the page", which needs both halves: a touch capability AND a coarse
 * pointer. A laptop with a touchscreen answers false and keeps its hover
 * affordances, which is the entire point of asking two questions instead of one.
 *
 * HYDRATION-STABLE BY CONSTRUCTION. It returns false on the server and on the
 * first client render, so the non-touch branch is what both passes agree on and
 * the touch branch arrives in an effect. A component may therefore call this
 * and switch a class on it without a hydration mismatch.
 *
 * WHY IT UPDATES. `pointer: coarse` is not fixed for the life of a page: a
 * tablet gains a trackpad, a phone gains a stylus, a desktop browser's device
 * emulation flips. The media query is listened to, and so is the first
 * `pointerdown`, because a pointer event is the only signal that names the
 * device actually being used rather than the best one attached.
 *
 * WHAT CALLS IT. Every hover-only affordance in the product, because a hover
 * that never fires is an action that does not exist: `patterns/queue-row.tsx`
 * (the one row action), `ui/scroll-area.tsx` (the overlay thumb's reveal). The
 * rule those files inherit is stated in QUEUE_ROW_RULES: a hover-revealed
 * action must have a non-hover path.
 */

import { useEffect, useState } from "react";

const COARSE_POINTER_QUERY = "(pointer: coarse)";

/** True only when the device both has touch and reports a coarse pointer. */
function readTouchPrimary(): boolean {
  if (typeof window === "undefined" || typeof navigator === "undefined") {
    return false;
  }

  const hasTouch = "ontouchstart" in window || navigator.maxTouchPoints > 0;

  /* jsdom and older engines ship no matchMedia; absence is not coarseness. */
  if (typeof window.matchMedia !== "function") {
    return false;
  }

  return hasTouch && window.matchMedia(COARSE_POINTER_QUERY).matches;
}

/**
 * Whether touch is the primary pointer, `false` until the first effect runs.
 */
function useTouchPrimary(): boolean {
  const [isTouchPrimary, setIsTouchPrimary] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const controller = new AbortController();
    const { signal } = controller;

    const sync = () => {
      setIsTouchPrimary(readTouchPrimary());
    };

    if (typeof window.matchMedia === "function") {
      window
        .matchMedia(COARSE_POINTER_QUERY)
        .addEventListener("change", sync, { signal });
    }

    window.addEventListener("pointerdown", sync, { signal });

    sync();

    return () => {
      controller.abort();
    };
  }, []);

  return isTouchPrimary;
}

export { readTouchPrimary, useTouchPrimary };
