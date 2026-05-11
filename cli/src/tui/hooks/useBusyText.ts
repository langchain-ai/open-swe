import { useMemo } from 'react';

const BUSY_TEXT_OPTIONS = [
  'vibing...',
  'noodling...',
  'pondering...',
  'thinking really hard...',
  'spinning up...',
  'connecting the dots...',
  'brewing ideas...',
  'cooking...',
  'crunching...',
  'scheming...',
  'processing...'
] as const;

export const useBusyText = (seed?: number) => {
  return useMemo(() => {
    const idx = Math.floor(((seed ?? Math.random()) * 1000) % BUSY_TEXT_OPTIONS.length);
    return BUSY_TEXT_OPTIONS[idx];
  }, [seed]);
};