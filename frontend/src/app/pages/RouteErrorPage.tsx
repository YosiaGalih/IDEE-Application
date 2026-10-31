import { useTranslation } from "react-i18next";
import { useLocation, useNavigate, useRouteError } from "react-router";
import { ApiError } from "@/api/errors";
import { uxStateForError } from "@/api/uxState";
import { StateView } from "@/components/ui/StateView";
import { loginPath } from "@/lib/safeNext";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

/**
 * Replaces React Router's default error screen, which prints the message and stack trace in English.
 * Shows the state for the failure, the request id when there is one, and a reload as the way out.
 */
export function RouteErrorPage() {
  const { t } = useTranslation();
  const error = useRouteError();
  const state = uxStateForError(error);
  useDocumentTitle(t(`states.${state === "loading" ? "error" : state}.title`));
  const navigate = useNavigate();
  const location = useLocation();
  const handleAction = () => {
    if (state === "unauthenticated") void navigate(loginPath(location));
    else window.location.reload();
  };
  return (
    <main className="mx-auto max-w-xl p-6">
      <StateView
        state={state}
        requestId={error instanceof ApiError ? error.requestId : null}
        onAction={handleAction}
        titleAs="h1"
      />
    </main>
  );
}
