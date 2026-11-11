import { NativeModule, requireNativeModule } from "expo"

type Events = {
  onCode(event: { code: string }): void
}

declare class OpenSWEAuthModule extends NativeModule<Events> {
  /** Start the loopback listener; resolves with its port. */
  startLoopback(): Promise<number>
  stopLoopback(): void
  /** A local backend's dev-login session, or null when it offers none. */
  devSignIn(backend: string): Promise<string | null>
}

export default requireNativeModule<OpenSWEAuthModule>("OpenSWEAuth")
