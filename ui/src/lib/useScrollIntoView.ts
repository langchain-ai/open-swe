import { useEffect, type RefObject } from "react"

export function useScrollIntoView(
  ref: RefObject<HTMLElement | null>,
  active: boolean
) {
  useEffect(() => {
    const element = ref.current
    if (!active || !element) return

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry) return
        observer.disconnect()
        if (entry.intersectionRatio === 1) return
        element.scrollIntoView({ block: "center", inline: "center" })
      },
      { threshold: 1 }
    )

    observer.observe(element)
    return () => observer.disconnect()
  }, [ref, active])
}
