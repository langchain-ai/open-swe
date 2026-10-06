"use client";

/*
 * The rep's wall clock, to the minute, as one shared external source.
 *
 * Every relative label the product renders ("In 42m", "2h", today's date) is a
 * claim about now, and now keeps moving while a rep reads it. The clock is
 * therefore subscribed to rather than read during render: `Date.now()` inside a
 * render is impure, and two renders of the same data would disagree.
 *
 * ONE SNAPSHOT, BUCKETED TO THE MINUTE. Every label on every surface is written
 * at minute resolution, so the snapshot is floored to the minute and two reads
 * inside one minute are the same number. That is what makes a re-render cheap
 * and a memo stable.
 *
 * getSnapshot IS SIDE-EFFECT-FREE. It returns the cached value and nothing
 * else. The value is advanced where the change actually happens: once when a
 * component subscribes, and once per tick after that. A getter that read the
 * system clock and cached it would be doing work React is allowed to call at
 * any time, including twice in one render.
 *
 * NO_CLOCK IS A STATE, NOT A TIME. A server render has no rep's clock to read,
 * so it reads `NO_CLOCK` and every caller renders the absent form of its label
 * rather than a duration measured from the epoch. Callers compare against
 * `NO_CLOCK` instead of testing for a falsy number, because "no clock yet" and
 * "midnight in 1970" are the same integer and only one of them is a state.
 */

import { useSyncExternalStore } from "react";

/** What a render with no rep's clock reads. Compared by name, never treated as a time. */
export const NO_CLOCK = 0;

const MS_PER_MINUTE = 60_000;

/** The last minute any subscriber read, cached because a snapshot has to be stable. */
let clockSnapshot: number = NO_CLOCK;

/** The current minute, floored, so a whole minute of reads is one value. */
function currentMinute(): number {
  return Math.floor(Date.now() / MS_PER_MINUTE) * MS_PER_MINUTE;
}

/**
 * One re-render a minute, plus one on subscribe so a surface that mounts
 * mid-minute does not render `NO_CLOCK` for up to sixty seconds.
 */
function subscribeToClock(onTick: () => void): () => void {
  clockSnapshot = currentMinute();
  onTick();
  const timer = window.setInterval(() => {
    clockSnapshot = currentMinute();
    onTick();
  }, MS_PER_MINUTE);
  return () => window.clearInterval(timer);
}

function readClock(): number {
  return clockSnapshot;
}

/** A server render has no rep's clock, and saying so is better than guessing one. */
function readClockOnServer(): number {
  return NO_CLOCK;
}

/** The rep's clock to the minute, as an external source rather than a render-time read. */
export function useClock(): number {
  return useSyncExternalStore(subscribeToClock, readClock, readClockOnServer);
}
