import { useLayoutEffect, type ReactNode } from 'react';
import { TooltipProvider } from '@langchain/macaw-components/Tooltip';
import { AppThemeProvider } from '@langchain/macaw-components/hooks/AppThemeProvider';
import { useColorScheme } from '@langchain/macaw-components/hooks/useColorScheme';

// The host owns theme preferences. Keep app state when its mode changes and
// avoid localStorage, which is unavailable in an opaque-origin sandbox.
export function HostTheme({ mode, children }: { mode: 'light' | 'dark'; children?: ReactNode }) {
  return (
    <AppThemeProvider defaultMode={mode} storageKey={null}>
      <TooltipProvider>
        <SyncTheme mode={mode}>{children}</SyncTheme>
      </TooltipProvider>
    </AppThemeProvider>
  );
}

function SyncTheme({ mode, children }: { mode: 'light' | 'dark'; children?: ReactNode }) {
  const { setMode } = useColorScheme();
  useLayoutEffect(() => setMode(mode), [mode, setMode]);
  return children;
}
