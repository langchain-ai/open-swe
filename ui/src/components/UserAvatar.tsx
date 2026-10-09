import { useQuery } from "@tanstack/react-query"

import { Avatar } from "@langchain/macaw-components/Avatar"
import { api } from "@/lib/api"

export function UserAvatar({
  login,
  name,
  size,
  className,
}: {
  login?: string | null
  name?: string
  size?: "default" | "sm" | "lg"
  className?: string
}) {
  const profile = useQuery({
    queryKey: ["user-avatar", login?.toLowerCase()],
    queryFn: () => api.userAvatar(login!),
    enabled: Boolean(login),
    staleTime: 5 * 60_000,
  })
  const label = name || login || "?"
  return (
    <Avatar
      size={size === "default" ? "md" : size}
      className={className}
      label={label}
      imageUrl={profile.data?.avatar_url || undefined}
    />
  )
}
