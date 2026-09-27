import { IconButton } from '@langchain/macaw-components/IconButton';
import { CheckIcon } from '@phosphor-icons/react/dist/ssr/Check';
import { CopyIcon } from '@phosphor-icons/react/dist/ssr/Copy';
import { useEffect, useRef, useState } from 'react';

type CopyState = 'idle' | 'copied' | 'selected';

// The app iframe has no clipboard-write permission, so navigator.clipboard rejects there.
function copyWithExecCommand(text: string): boolean {
  const textArea = document.createElement('textarea');
  textArea.value = text;
  textArea.style.position = 'fixed';
  textArea.style.opacity = '0';
  document.body.appendChild(textArea);
  textArea.select();
  try {
    return document.execCommand('copy');
  } finally {
    document.body.removeChild(textArea);
  }
}

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (e) {
    console.warn('navigator.clipboard rejected; falling back to execCommand', e);
  }
  try {
    return copyWithExecCommand(text);
  } catch (e) {
    console.warn('execCommand copy failed', e);
    return false;
  }
}

export function CopyLink({ url }: { url: string }) {
  const [state, setState] = useState<CopyState>('idle');
  const textRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    setState('idle');
  }, [url]);

  useEffect(() => {
    if (state !== 'copied') return;
    const timer = setTimeout(() => setState('idle'), 1500);
    return () => clearTimeout(timer);
  }, [state]);

  async function onCopy() {
    if (await copyText(url)) {
      setState('copied');
      return;
    }
    const node = textRef.current;
    const selection = window.getSelection();
    if (node && selection) {
      const range = document.createRange();
      range.selectNodeContents(node);
      selection.removeAllRanges();
      selection.addRange(range);
    }
    setState('selected');
  }

  return (
    <div className="flex min-w-0 items-center gap-space-1">
      <span ref={textRef} className="min-w-0 select-all truncate font-mono text-xs text-secondary">
        {url}
      </span>
      <IconButton
        icon={state === 'copied' ? CheckIcon : CopyIcon}
        label={
          state === 'copied'
            ? 'Copied'
            : state === 'selected'
              ? 'Copy blocked here; link selected, press ⌘C'
              : 'Copy Open SWE thread link'
        }
        size="xs"
        variant="plain"
        color="secondary"
        onClick={onCopy}
      />
    </div>
  );
}
