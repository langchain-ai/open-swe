import { useCallback, useRef, useState } from 'react';
import { searchFiles } from '@lib/file-search.js';

export function useFileSearchMenu() {
  const [showFileSearchMenu, setShowFileSearchMenu] = useState(false);
  const [fileSearchMatches, setFileSearchMatches] = useState<string[]>([]);
  const [fileSearchSelectionIndex, setFileSearchSelectionIndex] = useState(0);
  const fileSearchQueryRef = useRef<string | null>(null);
  const fileSearchDebounceTimer = useRef<NodeJS.Timeout | null>(null);

  const resetFileSearchMenu = useCallback(() => {
    setShowFileSearchMenu(false);
    setFileSearchMatches([]);
    setFileSearchSelectionIndex(0);
    fileSearchQueryRef.current = null;
  }, []);

  const triggerFileSearch = useCallback((query: string) => {
    if (fileSearchDebounceTimer.current) clearTimeout(fileSearchDebounceTimer.current);
    fileSearchDebounceTimer.current = setTimeout(async () => {
      const results = await searchFiles(query, process.cwd());
      setFileSearchMatches(results);
      setShowFileSearchMenu(results.length > 0);
      setFileSearchSelectionIndex(0);
    }, 150);
  }, []);

  const handleAtReference = useCallback((value: string) => {
    const lastWordMatch = value.match(/@(\S*)$/);
    if (!lastWordMatch) {
      resetFileSearchMenu();
      return;
    }
    const fileQuery = lastWordMatch[1];
    fileSearchQueryRef.current = lastWordMatch[0];
    setShowFileSearchMenu(true);
    if (fileQuery) triggerFileSearch(fileQuery);
    else setFileSearchMatches([]);
  }, [resetFileSearchMenu, triggerFileSearch]);

  const applyTabCompletion = useCallback((query: string) => {
    const selectedFile = fileSearchMatches[fileSearchSelectionIndex];
    if (selectedFile && fileSearchQueryRef.current) {
      const queryStart = query.lastIndexOf(fileSearchQueryRef.current);
      if (queryStart !== -1) {
        const newQuery = query.substring(0, queryStart) + `@${selectedFile} `;
        resetFileSearchMenu();
        return newQuery;
      }
    }
    return query;
  }, [fileSearchMatches, fileSearchSelectionIndex, resetFileSearchMenu]);

  const applySubmitSelection = useCallback((value: string) => {
    const selectedFile = fileSearchMatches[fileSearchSelectionIndex];
    if (!selectedFile || !fileSearchQueryRef.current) return value;
    const queryStart = value.lastIndexOf(fileSearchQueryRef.current);
    if (queryStart !== -1) {
      const newValue = value.substring(0, queryStart) + `@${selectedFile} `;
      resetFileSearchMenu();
      return newValue;
    }
    return value;
  }, [fileSearchMatches, fileSearchSelectionIndex, resetFileSearchMenu]);

  return {
    // state
    showFileSearchMenu,
    fileSearchMatches,
    fileSearchSelectionIndex,
    // setters
    setFileSearchSelectionIndex,
    // behaviors
    resetFileSearchMenu,
    handleAtReference,
    applyTabCompletion,
    applySubmitSelection,
  };
}