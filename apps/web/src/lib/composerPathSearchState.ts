import {
  type ComposerPathSearchState,
  type ComposerPathSearchTarget,
} from "@openswe/client-runtime/state/threads";

import { useComposerPathSearch as useComposerPathSearchQuery } from "../state/queries";

export function useComposerPathSearch(target: ComposerPathSearchTarget): ComposerPathSearchState {
  const state = useComposerPathSearchQuery(target);
  return {
    entries: state.entries.map((entry) => ({
      path: entry.path,
      kind: entry.kind,
    })),
    error: state.error,
    isPending: state.isPending,
  };
}
