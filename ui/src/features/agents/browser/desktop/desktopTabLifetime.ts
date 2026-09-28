import type { DesktopBrowserBridge, DesktopBrowserTabDefaults } from "@/desktop"

interface DesktopTabLease {
  references: number
  closeTimer: number | null
  ready: Promise<void>
}

const leases = new Map<string, DesktopTabLease>()
const pendingOperations = new Map<string, Promise<void>>()

/** Serialises create/close per tab so a fast remount cannot race a close. */
function enqueue(tabId: string, operation: () => Promise<void>): Promise<void> {
  const previous = pendingOperations.get(tabId)
  const pending = previous
    ? previous.catch(() => undefined).then(operation)
    : operation()
  pendingOperations.set(tabId, pending)
  void pending
    .finally(() => {
      if (pendingOperations.get(tabId) === pending)
        pendingOperations.delete(tabId)
    })
    .catch(() => undefined)
  return pending
}

export interface AcquiredDesktopTab {
  readonly ready: Promise<void>
  readonly release: () => void
}

/**
 * Reference-counted main-process tab. The first acquirer creates it with the
 * tab's zoom and appearance so the guest never paints a frame at defaults;
 * the last release closes it on the next tick, which lets a remount reuse it.
 */
export function acquireDesktopTab(
  bridge: DesktopBrowserBridge,
  tabId: string,
  defaults: DesktopBrowserTabDefaults
): AcquiredDesktopTab {
  const current =
    leases.get(tabId) ??
    ({
      references: 0,
      closeTimer: null,
      ready: enqueue(tabId, () => bridge.createTab(tabId, defaults)),
    } satisfies DesktopTabLease)
  if (current.closeTimer !== null) window.clearTimeout(current.closeTimer)
  current.references += 1
  current.closeTimer = null
  leases.set(tabId, current)
  return {
    ready: current.ready,
    release: () => {
      const lease = leases.get(tabId)
      if (!lease) return
      lease.references = Math.max(0, lease.references - 1)
      if (lease.references > 0) return
      lease.closeTimer = window.setTimeout(() => {
        const latest = leases.get(tabId)
        if (!latest || latest.references > 0) return
        leases.delete(tabId)
        void enqueue(tabId, () => bridge.closeTab(tabId)).catch(() => undefined)
      }, 0)
    },
  }
}
