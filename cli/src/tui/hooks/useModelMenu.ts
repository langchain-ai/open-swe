import { useCallback, useState } from 'react';
import type { ModelOption } from '@types';
import { modelOptions } from '@lib/models.js';

export function useModelMenu() {
  const [showModelMenu, setShowModelMenu] = useState(false);
  const [filteredModels, setFilteredModels] = useState<ModelOption[]>(modelOptions);
  const [modelSelectionIndex, setModelSelectionIndex] = useState(0);

  const open = useCallback(() => {
    setShowModelMenu(true);
    setFilteredModels(modelOptions);
    setModelSelectionIndex(0);
  }, []);

  const close = useCallback(() => setShowModelMenu(false), []);

  const filterFromQuery = useCallback((value: string) => {
    const input = value.toLowerCase();
    if (!input) {
      setFilteredModels(modelOptions);
      setModelSelectionIndex(0);
      return;
    }
    const matches = modelOptions.filter(
      (option) =>
        option.label.toLowerCase().includes(input) ||
        String(option.id).startsWith(input)
    );
    setFilteredModels(matches);
    setModelSelectionIndex(0);
  }, []);

  const reset = useCallback(() => {
    setFilteredModels(modelOptions);
    setModelSelectionIndex(0);
  }, []);

  return {
    // state
    showModelMenu,
    filteredModels,
    modelSelectionIndex,
    // setters
    setModelSelectionIndex,
    setFilteredModels,
    // controls
    open,
    close,
    filterFromQuery,
    reset,
  };
}