import { Link, useNavigate } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";

import type {SessionUser} from "@/lib/api";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import {  api } from "@/lib/api";

export function AppHeader({ user }: { user: SessionUser }) {
  const qc = useQueryClient();
  const navigate = useNavigate();

  const onLogout = async () => {
    await api.logout();
    qc.setQueryData(["session"], null);
    navigate({ to: "/login" });
  };

  return (
    <header className="border-b">
      <div className="container mx-auto flex items-center gap-4 px-4 py-3">
        <Link to="/profile" className="font-medium">
          open-swe
        </Link>
        <nav className="flex items-center gap-3 text-sm">
          <Link
            to="/profile"
            className="text-muted-foreground hover:text-foreground"
            activeProps={{ className: "text-foreground font-medium" }}
          >
            Profile
          </Link>
          {user.is_admin && (
            <Link
              to="/admin"
              className="text-muted-foreground hover:text-foreground"
              activeProps={{ className: "text-foreground font-medium" }}
            >
              Admin
            </Link>
          )}
        </nav>
        <div className="ml-auto flex items-center gap-3">
          {user.is_admin && <Badge variant="secondary">admin</Badge>}
          <span className="text-muted-foreground text-sm">{user.login}</span>
          <Avatar className="size-7">
            {user.avatar_url && <AvatarImage src={user.avatar_url} alt={user.login} />}
            <AvatarFallback>{user.login.slice(0, 2).toUpperCase()}</AvatarFallback>
          </Avatar>
          <Separator orientation="vertical" className="h-6" />
          <Button variant="ghost" size="sm" onClick={onLogout}>
            Sign out
          </Button>
        </div>
      </div>
    </header>
  );
}
