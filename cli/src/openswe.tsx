import { render } from 'ink';
import { App } from './tui/App.js';
import { getStoredApiKeys, deleteAllApiKeys, getStoredModelConfig } from '@lib/storage';
import { clearLog } from '@lib/logger';
import { useStore } from '@app/store.js';

const clearTerminal = () => {
  if (process.stdout.isTTY) {
    process.stdout.write('\x1B[2J\x1B[3J\x1B[H');
  }
};

// Ask the terminal to wrap pasted text in `\x1b[200~ … \x1b[201~` so the paste
// handler can route it through `insertTextAtCursor` instead of the keystroke
// path — which loses chunks when a paste is split across React batches.
const enableBracketedPaste = () => {
  if (process.stdout.isTTY) process.stdout.write('\x1B[?2004h');
};
const disableBracketedPaste = () => {
  if (process.stdout.isTTY) process.stdout.write('\x1B[?2004l');
};

export async function main() {
  await clearLog();

  // Belt-and-suspenders: also clear bracketed-paste mode on hard exit
  // (Ctrl+C / uncaught) so the user's shell doesn't inherit it.
  process.on('exit', disableBracketedPaste);

  const storedModelConfig = await getStoredModelConfig();
  if (storedModelConfig) {
    useStore.setState({ modelConfig: storedModelConfig });
  }

  const storedApiKeys = await getStoredApiKeys();
  useStore.setState({ apiKeys: storedApiKeys });

  let running = true;
  while (running) {

    const updateSize = () => {
      useStore.setState({
        terminalCols: process.stdout.columns ?? 80,
        terminalRows: process.stdout.rows ?? 24,
      });
    };
    process.stdout.on('resize', updateSize);
    updateSize();

    const blinkInterval = setInterval(() => {
      const { busy, toggleBlink } = useStore.getState();
      if (busy) {
        toggleBlink();
      }
    }, 600);

    const tickInterval = setInterval(() => {
      const { busy, advanceTick } = useStore.getState();
      if (busy) {
        advanceTick();
      }
    }, 120);

    enableBracketedPaste();
    const instance = render(<App />);
    try {
      await instance.waitUntilExit();
    } finally {
      clearInterval(blinkInterval);
      clearInterval(tickInterval);
      process.stdout.removeListener('resize', updateSize);
      disableBracketedPaste();
    }

    const { resetRequested, clearRequested } = useStore.getState();
    if (resetRequested) {
      useStore.setState({
        resetRequested: false,
        apiKeys: {},
        messages: [],
      });
      await deleteAllApiKeys();
    } else if (clearRequested) {
      useStore.setState({ clearRequested: false });
      clearTerminal();
    } else {
      running = false;
    }
  }
}
