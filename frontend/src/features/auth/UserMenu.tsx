import { LogOut, UserRound } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useLogout } from "@/api/hooks/useLogout";
import { useMe } from "@/api/hooks/useMe";
import { Button } from "@/components/ui/Button";
import { AUTH_PATHS } from "@/lib/safeNext";
import { ChangePasswordDialog } from "./ChangePasswordDialog";

/**
 * The account controls on the top bar: who is signed in, change password, sign out. Signing out always
 * ends on /login with an empty cache, even when the server fails the logout (useLogout clears on settle).
 */
export function UserMenu() {
  const { t } = useTranslation();
  const me = useMe();
  const logout = useLogout();
  const navigate = useNavigate();
  const handleSignOut = () => {
    if (logout.isPending) return;
    logout.mutate(undefined, { onSettled: () => void navigate(AUTH_PATHS.login, { replace: true }) });
  };
  return (
    <div role="group" aria-label={t("auth.menu.label")} className="flex items-center gap-2">
      {me.data ? (
        <span className="mr-2 flex items-center gap-1.5 text-sm text-fg">
          <UserRound aria-hidden="true" className="size-4 text-fg-muted" />
          {t("auth.menu.signedInAs", { name: me.data.user.display_name })}
        </span>
      ) : null}
      <ChangePasswordDialog onSignOut={handleSignOut} />
      <Button variant="ghost" size="sm" onClick={handleSignOut} disabled={logout.isPending}>
        <LogOut aria-hidden="true" className="size-4" />
        {logout.isPending ? t("auth.menu.loggingOut") : t("auth.menu.logout")}
      </Button>
    </div>
  );
}
