import { useTranslation } from "react-i18next";
import { Navigate } from "react-router";
import { ApiError } from "@/api/errors";
import { useMe } from "@/api/hooks/useMe";
import { uxStateForError } from "@/api/uxState";
import { StateView } from "@/components/ui/StateView";
import { settingsSectionsFor } from "@/lib/settingsSections";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

/**
 * `/settings` opens the first settings screen this user may (API §11 #18). Display only: each screen is
 * refused by the server without its permission.
 */
export function SettingsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t("screens.settings"));
  const me = useMe();
  // The data, not isSuccess: a failed background refresh keeps the user it already had.
  const [first] = me.data ? settingsSectionsFor(me.data.permissions) : [];
  if (first) return <Navigate to={first.path} replace />;

  let body: React.ReactNode;
  if (me.data) body = <StateView state="permission_denied" />;
  else if (me.isPending) body = <StateView state="loading" />;
  else
    body = (
      <StateView
        state={uxStateForError(me.error)}
        requestId={me.error instanceof ApiError ? me.error.requestId : null}
        onAction={() => void me.refetch()}
      />
    );
  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <h1 data-testid="screen-title" tabIndex={-1} className="text-2xl font-semibold">
        {t("screens.settings")}
      </h1>
      {body}
    </div>
  );
}
