import type { RouteObject } from "react-router";
import { AppLayout } from "@/components/layout/AppLayout";
import { NotFoundPage } from "./pages/NotFoundPage";
import { PlaceholderPage, type ScreenKey } from "./pages/PlaceholderPage";

const page = (screen: ScreenKey) => <PlaceholderPage screen={screen} />;

/** The route map of ARCHITECTURE §3.1b. WP-1.4b and later replace each placeholder with its screen. */
export const routes: RouteObject[] = [
  { path: "/login", element: page("login") },
  {
    element: <AppLayout />,
    children: [
      { index: true, element: page("dashboard") },
      { path: "projects", element: page("projects") },
      { path: "projects/new", element: page("newProject") },
      { path: "projects/:projectId/workspace", element: page("workspace") },
      { path: "projects/:projectId/tasks", element: page("tasks") },
      { path: "projects/:projectId/preview", element: page("preview") },
      { path: "projects/:projectId/testing", element: page("testing") },
      { path: "projects/:projectId/deployments", element: page("deployments") },
      { path: "projects/:projectId/knowledge", element: page("knowledge") },
      { path: "tasks/:taskId", element: page("taskDetail") },
      { path: "tasks/:taskId/testing", element: page("testing") },
      { path: "tasks/:taskId/review", element: page("review") },
      { path: "tasks/:taskId/acceptance", element: page("acceptance") },
      { path: "agents", element: page("agents") },
      { path: "approvals", element: page("approvals") },
      { path: "settings/providers", element: page("providers") },
      { path: "settings/usage", element: page("usage") },
      { path: "settings/users", element: page("users") },
      { path: "settings/policies", element: page("policies") },
      { path: "settings/audit", element: page("audit") },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];
