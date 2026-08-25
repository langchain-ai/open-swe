import type {
  EnvironmentId,
  PreviewOpenInput,
  PreviewSessionSnapshot,
  PreviewViewportSetting,
  ScopedThreadRef,
} from "@openswe/contracts";
import type { AtomCommandResult } from "@openswe/client-runtime/state/runtime";

import { browserDefaultOpenViewport, resolveBrowserDefaults } from "~/browser/browserDefaults";
import { applyPreviewServerSnapshot, rememberPreviewUrl } from "~/previewStateStore";

interface OpenPreviewSessionInput<E> {
  openPreview: (input: {
    readonly environmentId: EnvironmentId;
    readonly input: PreviewOpenInput;
  }) => Promise<AtomCommandResult<PreviewSessionSnapshot, E>>;
  threadRef: ScopedThreadRef;
  url?: string;
  /** Overrides the configured default; automation passes an explicit size. */
  viewport?: PreviewViewportSetting;
}

export async function openPreviewSession<E>(
  input: OpenPreviewSessionInput<E>,
): Promise<AtomCommandResult<PreviewSessionSnapshot, E>> {
  const result = await input.openPreview({
    environmentId: input.threadRef.environmentId,
    input: {
      threadId: input.threadRef.threadId,
      ...(input.url === undefined ? {} : { url: input.url }),
      viewport: input.viewport ?? browserDefaultOpenViewport(await resolveBrowserDefaults()),
    },
  });
  if (result._tag === "Failure") {
    return result;
  }
  const snapshot = result.value;
  applyPreviewServerSnapshot(input.threadRef, snapshot);
  if (input.url !== undefined) {
    rememberPreviewUrl(
      input.threadRef,
      snapshot.navStatus._tag === "Idle" ? input.url : snapshot.navStatus.url,
    );
  }
  return result;
}
