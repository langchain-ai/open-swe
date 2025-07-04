import { useState, useEffect, useCallback, useRef } from "react";
import { debounce } from "lodash";

const DRAFT_STORAGE_KEY = "terminal-input-draft";
const DRAFTS_STORAGE_KEY = "terminal-input-drafts";

interface Draft {
  id: string;
  content: string;
  timestamp: number;
}

interface UseDraftStorageReturn {
  message: string;
  setMessage: (message: string) => void;
  clearCurrentDraft: () => void;
  saveDraft: (content: string) => void;
  loadDraft: (draftId: string) => void;
  deleteDraft: (draftId: string) => void;
  getAllDrafts: () => Draft[];
}

const saveDraftToLocalStorage = (content: string) => {
  try {
    if (content.trim()) {
      localStorage.setItem(DRAFT_STORAGE_KEY, content);
    } else {
      localStorage.removeItem(DRAFT_STORAGE_KEY);
    }
  } catch (error) {
    console.warn("Failed to save draft to localStorage:", error);
  }
};

const getDraftFromLocalStorage = (): string => {
  try {
    const stored = localStorage.getItem(DRAFT_STORAGE_KEY);
    return stored || "";
  } catch (error) {
    console.warn("Failed to retrieve draft from localStorage:", error);
    return "";
  }
};

const saveDraftsToLocalStorage = (drafts: Draft[]) => {
  try {
    localStorage.setItem(DRAFTS_STORAGE_KEY, JSON.stringify(drafts));
  } catch (error) {
    console.warn("Failed to save drafts to localStorage:", error);
  }
};

const getDraftsFromLocalStorage = (): Draft[] => {
  try {
    const stored = localStorage.getItem(DRAFTS_STORAGE_KEY);
    if (stored) {
      const parsed = JSON.parse(stored);
      return Array.isArray(parsed) ? parsed : [];
    }
    return [];
  } catch (error) {
    console.warn("Failed to retrieve drafts from localStorage:", error);
    return [];
  }
};

export const useDraftStorage = (): UseDraftStorageReturn => {
  const [message, setMessageState] = useState("");
  const hasLoadedInitialDraft = useRef(false);

  // Debounced auto-save function
  const debouncedSave = useCallback(
    debounce((content: string) => {
      saveDraftToLocalStorage(content);
    }, 500),
    [],
  );

  // Load initial draft on mount
  useEffect(() => {
    if (!hasLoadedInitialDraft.current) {
      const savedDraft = getDraftFromLocalStorage();
      if (savedDraft) {
        setMessageState(savedDraft);
      }
      hasLoadedInitialDraft.current = true;
    }
  }, []);

  const setMessage = useCallback(
    (newMessage: string) => {
      setMessageState(newMessage);
      debouncedSave(newMessage);
    },
    [debouncedSave],
  );

  const clearCurrentDraft = useCallback(() => {
    localStorage.removeItem(DRAFT_STORAGE_KEY);
  }, []);

  const saveDraft = useCallback((content: string) => {
    if (!content.trim()) return;

    const drafts = getDraftsFromLocalStorage();
    const newDraft: Draft = {
      id: Date.now().toString(),
      content: content.trim(),
      timestamp: Date.now(),
    };

    const updatedDrafts = [newDraft, ...drafts.slice(0, 9)]; // Keep only 10 most recent drafts
    saveDraftsToLocalStorage(updatedDrafts);
  }, []);

  const loadDraft = useCallback(
    (draftId: string) => {
      const drafts = getDraftsFromLocalStorage();
      const draft = drafts.find((d) => d.id === draftId);
      if (draft) {
        setMessage(draft.content);
      }
    },
    [setMessage],
  );

  const deleteDraft = useCallback((draftId: string) => {
    const drafts = getDraftsFromLocalStorage();
    const updatedDrafts = drafts.filter((d) => d.id !== draftId);
    saveDraftsToLocalStorage(updatedDrafts);
  }, []);

  const getAllDrafts = useCallback((): Draft[] => {
    return getDraftsFromLocalStorage();
  }, []);

  return {
    message,
    setMessage,
    clearCurrentDraft,
    saveDraft,
    loadDraft,
    deleteDraft,
    getAllDrafts,
  };
};
