import { useCallback, useMemo, useState } from 'react';
import type { ApiKeyMenuItem, ApiKeys, Provider } from '@types';

const PROVIDER_LABELS: Record<Provider, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  google: 'Google',
};

const PROVIDER_ORDER: Provider[] = ['openai', 'anthropic', 'google'];

function maskKey(key: string | undefined): string {
  if (!key) return 'not set';
  if (key.length <= 6) return '••••';
  return `••••${key.slice(-4)}`;
}

export function buildApiKeyMenuItems(apiKeys: ApiKeys): ApiKeyMenuItem[] {
  const items: ApiKeyMenuItem[] = [];
  for (const provider of PROVIDER_ORDER) {
    const key = apiKeys[provider];
    const label = PROVIDER_LABELS[provider];
    items.push({
      provider,
      action: 'set',
      label: key ? `Update ${label} key` : `Set ${label} key`,
      detail: maskKey(key),
    });
    if (key) {
      items.push({
        provider,
        action: 'delete',
        label: `Remove ${label} key`,
        detail: maskKey(key),
      });
    }
  }
  return items;
}

export function useApiKeysMenu(apiKeys: ApiKeys) {
  const [showApiKeysMenu, setShowApiKeysMenu] = useState(false);
  const [apiKeysSelectionIndex, setApiKeysSelectionIndex] = useState(0);

  const apiKeyItems = useMemo(() => buildApiKeyMenuItems(apiKeys), [apiKeys]);

  const open = useCallback(() => {
    setShowApiKeysMenu(true);
    setApiKeysSelectionIndex(0);
  }, []);

  const close = useCallback(() => {
    setShowApiKeysMenu(false);
    setApiKeysSelectionIndex(0);
  }, []);

  return {
    showApiKeysMenu,
    apiKeyItems,
    apiKeysSelectionIndex,
    setApiKeysSelectionIndex,
    open,
    close,
  };
}
