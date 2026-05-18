import { createFileRoute } from "@tanstack/react-router";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { loginUrl } from "@/lib/api";

interface SearchParams {
  org?: string;
}

export const Route = createFileRoute("/not-authorized")({
  component: NotAuthorizedPage,
  validateSearch: (search: Record<string, unknown>): SearchParams => ({
    org: typeof search.org === "string" ? search.org : undefined,
  }),
});

function NotAuthorizedPage() {
  const { org } = Route.useSearch();
  return (
    <main className="container mx-auto flex min-h-svh items-center p-6">
      <Card className="mx-auto max-w-md">
        <CardHeader>
          <CardTitle>Not authorized</CardTitle>
          <CardDescription>
            {org
              ? `Dashboard access is restricted to members of ${org}.`
              : "Dashboard access is restricted to an approved GitHub organization."}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-muted-foreground text-sm">
            If you believe you should have access, ask an admin to add you to the org.
          </p>
          <Button asChild>
            <a href={loginUrl()}>Try a different account</a>
          </Button>
        </CardContent>
      </Card>
    </main>
  );
}
