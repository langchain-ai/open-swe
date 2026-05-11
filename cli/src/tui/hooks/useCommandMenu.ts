import { useCallback, useState } from 'react';
import type { SlashCommand } from '@types';
import { slashCommands } from '@app/commands.js';
import { getSlashCommandSuggestions } from '@app/slash-command.js';

export function useCommandMenu() {
  const [showCommandMenu, setShowCommandMenu] = useState(false);
  const [filteredCommands, setFilteredCommands] = useState<SlashCommand[]>(slashCommands);
  const [commandSelectionIndex, setCommandSelectionIndex] = useState(0);

  const open = useCallback(() => setShowCommandMenu(true), []);
  const close = useCallback(() => {
    setShowCommandMenu(false);
    setCommandSelectionIndex(0);
  }, []);

  const reset = useCallback(() => {
    setShowCommandMenu(false);
    setFilteredCommands(slashCommands);
    setCommandSelectionIndex(0);
  }, []);

  const filterFromQuery = useCallback((value: string) => {
    const matches = getSlashCommandSuggestions(value);
    setFilteredCommands(matches);
    if (matches.length > 0) {
      setShowCommandMenu(true);
      setCommandSelectionIndex(0);
    } else {
      setShowCommandMenu(false);
      setCommandSelectionIndex(0);
    }
  }, []);

  return {
    // state
    showCommandMenu,
    filteredCommands,
    commandSelectionIndex,
    // setters
    setCommandSelectionIndex,
    setFilteredCommands,
    // controls
    open,
    close,
    reset,
    // behavior
    filterFromQuery,
  };
}
