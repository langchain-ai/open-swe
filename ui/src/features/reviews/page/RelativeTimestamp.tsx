import { useEffect, useState } from "react"

import { formatRelativeTime } from "@/lib/utils"

export function RelativeTimestamp({ value }: { value: string }) {
  const timestamp = new Date(value).getTime()
  const [label, setLabel] = useState(() => formatRelativeTime(timestamp))

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>
    const update = () => {
      setLabel(formatRelativeTime(timestamp))
      timer = setTimeout(update, 1000)
    }
    update()
    return () => clearTimeout(timer)
  }, [timestamp])

  return (
    <time
      dateTime={value}
      title={new Date(timestamp).toLocaleString(undefined, {
        dateStyle: "full",
        timeStyle: "long",
      })}
    >
      {label}
    </time>
  )
}
