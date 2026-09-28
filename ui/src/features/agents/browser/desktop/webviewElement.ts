/** The Electron `<webview>` element, as far as the renderer touches it. */
export interface ElectronWebviewElement extends HTMLElement {
  src: string
  partition: string
  webpreferences?: string
  getWebContentsId: () => number
}

/**
 * `allowpopups` must reach the DOM as a literal attribute: Electron reads it
 * when the guest attaches, and react-dom drops boolean values for attributes
 * it does not recognise. React types it as a boolean, hence the cast.
 */
export const ALLOW_POPUPS_ATTRIBUTE = { allowpopups: "true" } as unknown as {
  readonly allowpopups?: boolean
}
