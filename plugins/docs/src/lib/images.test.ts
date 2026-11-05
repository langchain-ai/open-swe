import { expect, it } from "vitest"
import { imageFile } from "@/lib/images"

it.each([
  "100%.png",
  "a%zz.png",
  "https://github.com/acme/app/blob/main/a%zz.png",
])("ignores malformed image escapes: %s", (src) => {
  expect(imageFile(src, "docs/page.md")).toBeNull()
})
