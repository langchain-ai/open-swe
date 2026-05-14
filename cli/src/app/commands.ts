import type { SlashCommand } from '@types';

export const slashCommands: SlashCommand[] = [
  { name: 'help', description: 'Show available commands and usage.' },
  { name: 'logs', description: 'Print the path to this session’s log file.' },
  { name: 'clear', description: 'Start a new conversation.', aliases: ['new'] },
  { name: 'quit', description: 'Exit the application.', aliases: ['exit'] },
];
