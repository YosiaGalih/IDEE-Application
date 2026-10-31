import { LogOut, UserRound, XCircle } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { isAlreadySignedOut, useLogout } from "@/api/hooks/useLogout";
import { useMe } from "@/api/hooks/useMe";
import { Button } from "@/components/ui/Button";
import { AUTH_PATHS } from "@/lib/safeNext";
import { ChangePasswordDialog } from "./ChangePasswordDialog";

/**
 * The account controls on the top bar: who is signed in, change password, sign out. Sign-out goes to
 * /login only once the session is gone (success, or a 401). Any other failure keeps the user here with
 * the error: a login form while the session lives would look signed out on a shared computer.
 */
export function UserMenu() {
  const { t } = useTranslation();
  const me = useMe();
  const logout = useLogout();
  const navigate = useNavigate();
  const toLogin = () => void navigate(AUTH_PATHS.login, { replace: true });
  const handleSignOut = () => {
    logout.mutate(undefined, {
      onSuccess: toLogin,
      onError: (error) => {
        if (isAlreadySignedOut(error)) toLogin();
      },
    });
  };
  const hasFailed = logout.isError && !isAlreadySignedOut(logout.error);
  return (
    <div role="group" aria-label={t("auth.menu.label")} className="flex min-w-0 items-center gap-2">
      {me.data ? (
        <span className="mr-2 flex min-w-0 items-center gap-1.5 text-sm text-fg">
          <UserRound aria-hidden="true" className="size-4 shrink-0 text-fg-muted" />
          <span className="max-w-56 truncate" title={me.data.user.display_name}>
            {t("auth.menu.signedInAs", { name: me.data.user.display_name })}
          </span>
        </span>
      ) : null}
      {hasFailed ? (
        <p role="alert" className="flex items-center gap-1.5 text-sm text-status-danger">
          <XCircle aria-hidden="true" className="size-4 shrink-0" />
          {t("auth.menu.logoutFailed")}
        </p>
      ) : null}
      <ChangePasswordDialog onSignOut={handleSignOut} />
      <Button variant="ghost" size="sm" onClick={handleSignOut} disabled={logout.isPending}>
        <LogOut aria-hidden="true" className="size-4" />
        {logout.isPending ? t("auth.menu.loggingOut") : t("auth.menu.logout")}
      </Button>
    </div>
  );
}
