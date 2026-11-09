import { createContext, useContext } from "react"

import { AccountStore } from "../core/accountStore"
import { LiveAgentService, type AgentService } from "../core/agentService"
import { APIClient, APIError } from "../core/apiClient"
import { DemoAgentService } from "../core/demoAgentService"
import { browserSignIn, devSignIn, SignInCancelled } from "../core/signIn"
import { AgentStore } from "./agentStore"
import { Observable, useObserve } from "./observable"

/** Who is signed in, and the agents store that goes with them. */
export class AppModel extends Observable {
  /** Null until the stored account has been read, then the signed-in store, if any. */
  store: AgentStore | null = null
  isRestoring = true
  isDemo = false
  isSigningIn = false
  signInError: string | null = null
  lastBackend = ""
  /**
   * Every sign-out still landing: the Keychain deletion and the server logout.
   * A sign-in waits for both, because either arriving later would undo it: the
   * deletion would remove the session it saved, and logout revokes the
   * account's stored credential, not one session's.
   */
  private pendingSignOut: Promise<unknown> = Promise.resolve()

  async restore() {
    this.lastBackend = await AccountStore.lastBackend()
    const stored = await AccountStore.load()
    if (stored) {
      // A restored backend passes the same checks as a typed one, so a session
      // never goes out over plain http to anything but this machine.
      try {
        const backend = APIClient.origin(stored.backend)
        this.start(
          new LiveAgentService(new APIClient(backend, stored.sessionToken))
        )
      } catch {
        await AccountStore.clear()
      }
    }
    this.isRestoring = false
    this.changed()
  }

  async signIn(backendInput: string) {
    this.signInError = null
    this.isSigningIn = true
    this.changed()
    try {
      await this.pendingSignOut
      const backend = APIClient.origin(backendInput)
      this.lastBackend = backend
      await AccountStore.setLastBackend(backend)
      const token = (await devSignIn(backend)) ?? (await browserSignIn(backend))
      await AccountStore.save({ backend, sessionToken: token })
      this.start(new LiveAgentService(new APIClient(backend, token)))
    } catch (error) {
      if (!(error instanceof SignInCancelled))
        this.signInError = APIError.describe(error)
    } finally {
      this.isSigningIn = false
      this.changed()
    }
  }

  startDemo() {
    this.isDemo = true
    this.start(new DemoAgentService())
    this.changed()
  }

  /**
   * Sign out: forget the session here at once, and end it on the server as the
   * dashboard does. The server call runs on its own, so an unreachable backend
   * never holds the app on a screen it is signing out of.
   */
  signOut() {
    this.forgetSession(this.store?.service.signOut())
  }

  /**
   * Leave the signed-in screens now and delete the stored session in the
   * background; with no `serverSignOut`, the server is not told, as for a
   * session it already refused.
   */
  private forgetSession(serverSignOut?: Promise<void>) {
    this.pendingSignOut = Promise.allSettled([
      this.pendingSignOut,
      serverSignOut,
      AccountStore.clear(),
    ])
    this.isDemo = false
    this.store = null
    this.changed()
  }

  private start(service: AgentService) {
    const store = new AgentStore(service)
    store.onUnauthorized = () => {
      this.signInError = "Your session expired. Sign in again."
      this.forgetSession()
    }
    this.store = store
  }
}

export const AppModelContext = createContext<AppModel | null>(null)

export function useAppModel(): AppModel {
  const model = useContext(AppModelContext)
  if (!model) throw new Error("useAppModel outside AppModelContext")
  return useObserve(model)
}

/** The signed-in agent store; screens under the signed-in stack always have one. */
export function useAgentStore(): AgentStore {
  const store = useAppModel().store
  useObserve(store)
  if (!store) throw new Error("useAgentStore while signed out")
  return store
}
