import { Navigate } from "@tanstack/react-router"

import {
  currentAuthRedirectPath,
  rememberAuthRedirect,
} from "./auth-redirect-core"

export * from "./auth-redirect-core"

export function RequireLogin() {
  const redirect = rememberAuthRedirect(currentAuthRedirectPath())
  return <Navigate to="/login" search={{ redirect }} />
}
