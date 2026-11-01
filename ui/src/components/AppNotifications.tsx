import { useEffect, useRef } from "react"
import { useQuery } from "@tanstack/react-query"
import { useRouter } from "@tanstack/react-router"
import { api } from "@/lib/api"
import { useSession } from "@/lib/session"
import { invalidationTopic } from "@/lib/invalidations/topics"
import { DesktopNotification } from "@/lib/notifications"
import { useHrefLinkOptions } from "@/lib/appLocation"

/** Shows pending notifications as OS notifications and confirms each one the OS displayed. */
export function AppNotifications() {
  const { data: user } = useSession()
  const router = useRouter()
  const linkOptions = useHrefLinkOptions()
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
      const displayed = DesktopNotification.show(notification.title, {
        body: notification.body,
        tag: `notification-${notification.id}`,
        onClick: () => {
          if (notification.path)
            void router.navigate(linkOptions(notification.path))
          else window.open(notification.url, "_blank", "noopener")
        },
        onShow: () =>
          void api
            .acknowledgeNotification(notification.id)
            .catch((error: unknown) => {
              console.warn("Could not confirm notification delivery", error)
            }),
      })
      if (displayed) shown.current.add(notification.id)
    }
  }, [data, linkOptions, router])
  return null
}
