import { Navigate, createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/")({ component: Index });

function Index() {
  return <Navigate to="/profile" />;
}
