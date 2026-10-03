import { useQuery } from "@tanstack/react-query"

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
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
    <Avatar size={size} className={className}>
      {profile.data?.avatar_url && (
        <AvatarImage src={profile.data.avatar_url} alt="" />
      )}
      <AvatarFallback>{label.slice(0, 2).toUpperCase()}</AvatarFallback>
    </Avatar>
  )
}
