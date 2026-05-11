import { Text } from 'ink';
import { themeColor } from '@tui/theme.js';

const LOGO_LINES = [
  '                _       ',
  '   ___ ___   __| | __ _ ',
  "  / __/ _ \\ / _` |/ _` |",
  ' | (_| (_) | (_| | (_| |',
  '  \\___\\___/ \\__,_|\\__,_|',
];

export const Logo = () => (
  <Text color={themeColor('brand')}>
    {LOGO_LINES.join('\n')}
  </Text>
);
