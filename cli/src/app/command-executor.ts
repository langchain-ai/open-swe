import type { Message, SlashCommandName } from '@types';
import { slashCommands } from '@app/commands.js';

export type CommandCtx = {
  addMessage: (message: Omit<Message, 'id'>) => void;
  exit: () => void;
  requestUiClear?: () => void;
};

export async function executeSlashCommand(
  cmdName: SlashCommandName,
  ctx: CommandCtx,
): Promise<boolean> {
  switch (cmdName) {
    case 'help': {
      const lines = slashCommands.map((c) => {
        const alias = c.aliases?.length ? ` (aliases: ${c.aliases.join(', ')})` : '';
        return `  /${c.name}${alias} — ${c.description}`;
      });
      ctx.addMessage({ author: 'system', chunks: [{ kind: 'list', lines: ['Commands:', ...lines] }] });
      return true;
    }
    case 'quit': {
      ctx.addMessage({ author: 'system', chunks: [{ kind: 'text', text: 'Goodbye!' }] });
      setTimeout(() => ctx.exit(), 100);
      return true;
    }
    case 'clear': {
      ctx.requestUiClear?.();
      return true;
    }
    case 'logs': {
      const { getLogPath } = await import('@lib/logger');
      ctx.addMessage({
        author: 'system',
        chunks: [
          { kind: 'text', text: `Session log: ${getLogPath()}\nTail it with: tail -f "${getLogPath()}"` },
        ],
      });
      return true;
    }
    default:
      return false;
  }
}
