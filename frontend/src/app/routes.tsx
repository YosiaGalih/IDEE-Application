import type { RouteObject } from "react-router";
import { AppLayout } from "@/components/layout/AppLayout";
import { ForgotPasswordPage } from "@/features/auth/ForgotPasswordPage";
import { LoginPage } from "@/features/auth/LoginPage";
import { RequireSession } from "@/features/auth/RequireSession";
import { ResetPasswordPage } from "@/features/auth/ResetPasswordPage";
import { UserMenu } from "@/features/auth/UserMenu";
import { AUTH_PATHS } from "@/lib/safeNext";
import { NotFoundPage } from "./pages/NotFoundPage";
import { RouteErrorPage } from "./pages/RouteErrorPage";
import { PlaceholderPage, type ScreenKey } from "./pages/PlaceholderPage";

const page = (screen: ScreenKey) => <PlaceholderPage screen={screen} />;

/**
 * The route map of ARCHITECTURE §3.1b. The auth pages are public; everything in the app shell sits behind
 * the session guard. Later work packages replace each placeholder with its screen.
 */
export const routes: RouteObject[] = [
  {
    errorElement: <RouteErrorPage />,
    children: [
      { path: AUTH_PATHS.login, element: <LoginPage /> },
      { path: AUTH_PATHS.forgotPassword, element: <ForgotPasswordPage /> },
      // One route for both: replacing the URL drops the token without remounting the page.
      { path: `${AUTH_PATHS.resetPassword}/:token?`, element: <ResetPasswordPage /> },
      {
        element: (
          <RequireSession>
            <AppLayout userMenu={<UserMenu />} />
          </RequireSession>
        ),
        errorElement: <RouteErrorPage />,
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
    ],
  },
];
