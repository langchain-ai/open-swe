import { Navigate, createFileRoute } from "@tanstack/react-router";

// Integrations were folded into Profile Settings. Keep the route as a redirect
// so existing links don't 404.
export const Route = createFileRoute("/integrations")({
  component: () => <Navigate to="/my-settings" replace />,
});
