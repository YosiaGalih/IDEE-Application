import { Construction } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Card, CardTitle } from "@/components/ui/Card";

export type ScreenKey =
  | "login"
  | "dashboard"
  | "projects"
  | "newProject"
  | "workspace"
  | "agents"
  | "tasks"
  | "taskDetail"
  | "preview"
  | "testing"
  | "review"
  | "acceptance"
  | "approvals"
  | "deployments"
  | "knowledge"
  | "providers"
  | "usage"
  | "users"
  | "policies"
  | "audit";

type Props = { screen: ScreenKey };

/** Stands in for a screen until WP-1.4b and later work packages build it. */
export function PlaceholderPage({ screen }: Props) {
  const { t } = useTranslation();
  return (
    <Card className="mx-auto max-w-2xl">
      <div className="flex items-start gap-3">
        <Construction aria-hidden="true" className="mt-0.5 size-5 text-brand-600" />
        <div>
          <CardTitle>
            <span data-testid="screen-title">{t(`screens.${screen}`)}</span>
          </CardTitle>
          <p className="mt-1 text-sm text-fg-muted">{t("placeholder.body")}</p>
        </div>
      </div>
    </Card>
  );
}
