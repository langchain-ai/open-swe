import { useCallback } from "react"
import { useSession } from "./session"

export function displayRepoName(
  fullName: string,
  controllingOrg?: string | null
): string {
  const [owner, name, ...rest] = fullName.split("/")
  return controllingOrg &&
    owner?.toLowerCase() === controllingOrg.toLowerCase() &&
    name &&
    !rest.length
    ? name
    : fullName
}

export function useRepoName() {
  const session = useSession()
  const org = session.data?.controlling_org
  return useCallback(
    (fullName: string) => displayRepoName(fullName, org),
    [org]
  )
}
