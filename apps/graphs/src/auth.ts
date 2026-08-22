import { timingSafeEqual } from "node:crypto"

import { Auth, HTTPException } from "@langchain/langgraph-sdk/auth"

function matchesToken(supplied: string, expected: string): boolean {
  const left = Buffer.from(supplied)
  const right = Buffer.from(expected)
  return left.length === right.length && timingSafeEqual(left, right)
}

export const auth = new Auth().authenticate(async (request) => {
  const authorization = request.headers.get("authorization")
  const separator = authorization?.indexOf(" ") ?? -1
  const scheme = separator < 0 ? "" : authorization!.slice(0, separator)
  const supplied = separator < 0 ? "" : authorization!.slice(separator + 1)
  const expected = process.env.OPEN_SWE_LOCAL_AUTH_TOKEN

  if (
    scheme.toLowerCase() !== "bearer" ||
    !expected ||
    !matchesToken(supplied, expected)
  ) {
    throw new HTTPException(401, { message: "Invalid bearer token" })
  }

  return { identity: "local-user", permissions: ["*"] }
})
