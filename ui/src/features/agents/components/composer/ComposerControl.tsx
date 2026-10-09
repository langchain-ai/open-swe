import { CaretDownIcon } from "@phosphor-icons/react/dist/ssr/CaretDown"

/** Trailing caret on the composer's dropdown triggers (run target, repo, branch). */
export function ComposerControlChevron() {
  return (
    <CaretDownIcon
      aria-hidden="true"
      className="-mx-0.5 size-3 shrink-0 text-icon-secondary opacity-70"
      weight="bold"
    />
  )
}
