import { ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useMe } from "@/api/hooks/useMe";
import { Card } from "@/components/ui/Card";
import { StateView } from "@/components/ui/StateView";
import { settingsSectionsFor } from "@/lib/settingsSections";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

/**
 * `/settings` (spec WP-3.1e2 Goal 2): the settings screens this user may open. Display only; each screen is
 * refused by the server without its permission.
 */
export function SettingsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t("screens.settings"));
  const me = useMe();
  const sections = me.isSuccess ? settingsSectionsFor(me.data.permissions) : [];
  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <header>
        <h1 data-testid="screen-title" tabIndex={-1} className="text-2xl font-semibold">
          {t("screens.settings")}
        </h1>
        {sections.length > 0 ? <p className="mt-1 text-sm text-fg-muted">{t("settings.lead")}</p> : null}
      </header>
      {sections.length === 0 ? (
        <StateView state="permission_denied" />
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2">
          {sections.map((section) => (
            <li key={section.key}>
              <Card className="h-full p-0">
                <Link
                  to={section.path}
                  className="flex h-full items-start justify-between gap-3 rounded-[inherit] p-4 hover:bg-surface-muted"
                >
                  <span>
                    <span className="block font-semibold">{t(`settings.sections.${section.key}.title`)}</span>
                    <span className="mt-1 block text-sm text-fg-muted">
                      {t(`settings.sections.${section.key}.body`)}
                    </span>
                  </span>
                  <ChevronRight aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
                </Link>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
