import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useLocation } from "react-router";
import { ApiError } from "@/api/errors";
import { useMe } from "@/api/hooks/useMe";
import { uxStateForError, type UxState } from "@/api/uxState";
import { StateView } from "@/components/ui/StateView";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { loginPath } from "@/lib/safeNext";

type Props = { children: ReactNode };

/**
 * The session guard of every page in the app shell: loading while /auth/me answers, /login?next= on a 401,
 * the failure's state with a retry otherwise. A failed background refresh keeps the app on screen unless
 * it is a 401.
 */
export function RequireSession({ children }: Props) {
  const me = useMe();
  const location = useLocation();
  const state = me.isError ? uxStateForError(me.error) : null;
  if (state === "unauthenticated") return <Navigate to={loginPath(location)} replace />;
  if (me.data !== undefined) return children;
  // A retry after an error is pending again (no data yet), so it shows loading, not the stale error.
  if (me.isPending) return <SessionState state="loading" />;
  return (
    <SessionState
      state={state ?? "error"}
      requestId={me.error instanceof ApiError ? me.error.requestId : null}
      onRetry={() => void me.refetch()}
    />
  );
}

type StateProps = { state: UxState; requestId?: string | null; onRetry?: () => void };

function SessionState({ state, requestId = null, onRetry }: StateProps) {
  const { t } = useTranslation();
  useDocumentTitle(t(`states.${state}.title`));
  return (
    <main id="main" className="mx-auto max-w-xl p-6">
      <StateView
        state={state}
        requestId={requestId}
        titleAs="h1"
        {...(onRetry ? { onAction: onRetry } : {})}
      />
    </main>
  );
}
