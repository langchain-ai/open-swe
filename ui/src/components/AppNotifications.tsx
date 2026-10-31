import { useEffect, useRef } from "react"
import { useQuery } from "@tanstack/react-query"
import { toast } from "sonner"
import { api, type AppNotification } from "@/lib/api"
import { useSession } from "@/lib/session"
import { invalidationTopic } from "@/lib/invalidations/topics"

function NotificationReceipt({
  notification,
}: {
  notification: AppNotification
}) {
  const element = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    let frame = 0
    let acknowledged = false
    const toastElement = element.current?.closest("[data-sonner-toast]")
    const acknowledge = () => {
      if (acknowledged || document.visibilityState !== "visible") return
      frame = requestAnimationFrame(() => {
        if (
          acknowledged ||
          document.visibilityState !== "visible" ||
          toastElement?.getAttribute("data-mounted") !== "true" ||
          toastElement.getAttribute("data-visible") !== "true" ||
          (toastElement.getAttribute("data-front") !== "true" &&
            toastElement.getAttribute("data-expanded") !== "true")
        )
          return
        acknowledged = true
        void api
          .acknowledgeNotification(notification.id)
          .catch((error: unknown) => {
            console.warn("Could not confirm notification delivery", error)
          })
      })
    }
    const observer = new MutationObserver(acknowledge)
    if (toastElement) observer.observe(toastElement, { attributes: true })
    acknowledge()
    document.addEventListener("visibilitychange", acknowledge)
    return () => {
      observer.disconnect()
      cancelAnimationFrame(frame)
      document.removeEventListener("visibilitychange", acknowledge)
    }
  }, [notification.id])
  return <span ref={element}>{notification.body}</span>
}

export function AppNotifications() {
  const { data: user } = useSession()
  const shown = useRef(new Set<string>())
  const { data } = useQuery({
    queryKey: ["notifications", user?.user_id],
    queryFn: api.pendingNotifications,
    enabled: Boolean(user?.user_id),
    meta: {
      invalidatedBy: [invalidationTopic("notifications", user?.user_id ?? "")],
    },
  })
  useEffect(() => {
    for (const notification of data ?? []) {
      if (shown.current.has(notification.id)) continue
      shown.current.add(notification.id)
      toast.info(notification.title, {
        id: notification.id,
        duration: Infinity,
        description: <NotificationReceipt notification={notification} />,
        action: {
          label: "Open conversation",
          onClick: () => window.location.assign(notification.url),
        },
      })
    }
  }, [data])
  return null
}
