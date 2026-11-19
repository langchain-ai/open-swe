import { useEffect, useRef, useState, type RefObject } from "react"

export interface LoadedImage {
  url: string
  width: number
  height: number
}

/**
 * `blob` decoded as an image, with its natural size and an object URL. While a
 * new blob decodes, such as the same file read again, the last image stays, so
 * a refetch doesn't flash a loading state; each URL is revoked once replaced or
 * on unmount.
 */
export function useImage(blob: Blob | undefined): {
  image?: LoadedImage
  error?: string
} {
  const [state, setState] = useState<{
    image?: LoadedImage
    error?: string
  }>()
  useEffect(() => {
    if (!blob) return
    let current = true
    const url = URL.createObjectURL(blob)
    const element = new Image()
    element.src = url
    element.decode().then(
      () => {
        if (!current) return URL.revokeObjectURL(url)
        setState({
          image: {
            url,
            width: element.naturalWidth,
            height: element.naturalHeight,
          },
        })
      },
      () => {
        URL.revokeObjectURL(url)
        if (current) setState({ error: "The screenshot could not be read." })
      }
    )
    return () => {
      current = false
    }
  }, [blob])
  const shown = state?.image?.url
  useEffect(() => {
    if (shown) return () => URL.revokeObjectURL(shown)
  }, [shown])
  return blob ? (state ?? {}) : {}
}

/** Whether the element has come within 200px of the viewport; it stays true once it has. */
export function useNearViewport<T extends Element>(): [
  RefObject<T | null>,
  boolean,
] {
  const ref = useRef<T>(null)
  const [near, setNear] = useState(false)
  useEffect(() => {
    const element = ref.current
    if (!element) return
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          setNear(true)
          observer.disconnect()
        }
      },
      { rootMargin: "200px" }
    )
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  return [ref, near]
}
