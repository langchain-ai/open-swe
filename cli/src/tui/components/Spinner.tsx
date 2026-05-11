import { Text } from 'ink';
import { useStore } from '@app/store.js';
import { themeColor } from '@tui/theme.js';
import { TEXT_SPINNER_FRAMES } from '@tui/figures.js';

type Props = {
  color?: string;
  frames?: readonly string[];
};

/**
 * Tiny spinner driven by the global tick set up in `coda.tsx`. Avoids
 * per-instance timers so many spinners can render cheaply side-by-side.
 */
export const Spinner = ({ color, frames = TEXT_SPINNER_FRAMES }: Props) => {
  const tick = useStore((s) => s.tick);
  const idx = tick % frames.length;
  return <Text color={color ?? themeColor('brand')}>{frames[idx]}</Text>;
};
