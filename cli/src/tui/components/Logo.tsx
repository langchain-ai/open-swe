import { Text } from 'ink';
import { themeColor } from '@tui/theme.js';

const LOGO_LINES = [
  '  ___                    ____  _    _ _____ ',
  " / _ \\ _ __   ___ _ __  / ___|| |  | | ____|",
  "| | | | '_ \\ / _ \\ '_ \\ \\___ \\| |/\\| |  _|  ",
  '| |_| | |_) |  __/ | | | ___) |  /\\  | |___ ',
  ' \\___/| .__/ \\___|_| |_||____/|_/  \\_|_____|',
  '      |_|                                   ',
];

export const Logo = () => (
  <Text color={themeColor('brand')}>
    {LOGO_LINES.join('\n')}
  </Text>
);
